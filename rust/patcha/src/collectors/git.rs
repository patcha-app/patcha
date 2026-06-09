use crate::{
    collectors::guard::CollectorGuard,
    models::{Event, EventType},
};
use anyhow::Result;
use chrono::{DateTime, Utc};
use git2::{Repository, Sort};
use std::collections::{HashMap, HashSet};
use std::io::Write;
use std::path::{Path, PathBuf};

const STAGE_MAX_LOG_ROWS: usize = 20_000;
const STAGE_TRIM_EVERY: usize = 500;

// Directories skipped when scanning for git repos (same as Python)
const SKIP_DIRS: &[&str] = &[
    "Music", "Pictures", "Movies", "Library",
    "Applications", "iCloud Drive", "iCloud",
];

pub struct GitCollector {
    commits_guard: CollectorGuard,
    stashes_guard: CollectorGuard,
    data_dir: PathBuf,
    stage_write_count: usize,
}

impl GitCollector {
    pub fn new(data_dir: PathBuf) -> Self {
        Self {
            commits_guard: CollectorGuard::new("git_commits", 3),
            stashes_guard: CollectorGuard::new("git_stashes", 3),
            data_dir,
            stage_write_count: 0,
        }
    }

    // -----------------------------------------------------------------------
    // Commits
    // -----------------------------------------------------------------------

    pub fn collect_commits(&mut self, since: Option<DateTime<Utc>>) -> Vec<Event> {
        if !self.commits_guard.ok() {
            return Vec::new();
        }
        match self.collect_commits_inner(since) {
            Ok(events) => {
                self.commits_guard.success();
                events
            }
            Err(e) => {
                self.commits_guard.fail(&e);
                Vec::new()
            }
        }
    }

    fn collect_commits_inner(&self, since: Option<DateTime<Utc>>) -> Result<Vec<Event>> {
        let repos = find_git_repos(&dirs::home_dir().unwrap_or_default());
        let mut events = Vec::new();

        for repo_path in repos {
            let Ok(repo) = Repository::open(&repo_path) else {
                continue;
            };

            let mut revwalk = match repo.revwalk() {
                Ok(rw) => rw,
                Err(_) => continue,
            };
            let _ = revwalk.push_head();
            revwalk.set_sorting(Sort::TIME)?;

            let limit = if since.is_none() { 50 } else { usize::MAX };
            let mut count = 0;

            for oid in revwalk.flatten() {
                if count >= limit {
                    break;
                }
                let Ok(commit) = repo.find_commit(oid) else {
                    continue;
                };

                let commit_ts = commit.time().seconds();
                let timestamp =
                    DateTime::from_timestamp(commit_ts, 0).unwrap_or_else(Utc::now);

                if let Some(since) = since {
                    if timestamp < since {
                        break;
                    }
                }

                let message = commit.message().unwrap_or("").trim().to_owned();
                let author = commit.author().name().unwrap_or("").to_owned();
                let hash = oid.to_string();

                let branch = repo
                    .head()
                    .ok()
                    .and_then(|h| h.shorthand().map(|s| s.to_owned()))
                    .unwrap_or_else(|| "unknown".into());

                // Get diff stats
                let (files_changed, insertions, deletions, diff_text) =
                    commit_stats(&repo, &commit);

                let raw = serde_json::json!({
                    "hash": hash,
                    "message": message,
                    "author": author,
                    "timestamp": timestamp.to_rfc3339(),
                    "files_changed": files_changed,
                    "insertions": insertions,
                    "deletions": deletions,
                    "branch": branch,
                    "diff": diff_text,
                });

                let mut e = Event::new(EventType::GitCommit, raw.to_string());
                e.timestamp = timestamp;
                e.source = Some("git".to_owned());
                e.project = Some(repo_path.file_name().unwrap_or_default().to_string_lossy().into_owned());
                e.source_doc_id = Some(format!("{}:{}", repo_path.display(), hash));
                e.metadata.insert("repo_path".into(), serde_json::json!(repo_path.to_string_lossy()));
                e.metadata.insert("files_count".into(), serde_json::json!(files_changed.len()));
                e.metadata.insert("lines_changed".into(), serde_json::json!(insertions + deletions));
                e.metadata.insert("files_changed".into(), serde_json::json!(files_changed));

                events.push(e);
                count += 1;
            }
        }

        Ok(events)
    }

    // -----------------------------------------------------------------------
    // Staging snapshots + events
    // -----------------------------------------------------------------------

    /// Snapshot the staged/unstaged state for all repos and append to log.
    pub fn record_staging_snapshot(&mut self) {
        let repos = find_git_repos(&dirs::home_dir().unwrap_or_default());
        let snapshot_file = self.data_dir.join("git_stage_snapshots.jsonl");

        if let Some(parent) = snapshot_file.parent() {
            let _ = std::fs::create_dir_all(parent);
        }

        for repo_path in repos {
            let Ok(repo) = Repository::open(&repo_path) else {
                continue;
            };

            let (staged, unstaged, untracked, staged_diff) = staging_state(&repo);

            let entry = serde_json::json!({
                "timestamp": Utc::now().to_rfc3339(),
                "repo": repo_path.to_string_lossy(),
                "staged": staged,
                "unstaged": unstaged,
                "untracked": untracked,
                "staged_diff": staged_diff,
            });

            if let Ok(mut f) = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&snapshot_file)
            {
                let _ = writeln!(f, "{}", entry);
            }

            self.stage_write_count += 1;
            if self.stage_write_count % STAGE_TRIM_EVERY == 0 {
                let _ = trim_jsonl(&snapshot_file, STAGE_MAX_LOG_ROWS);
            }
        }
    }

    /// Emit events for changes in the staged file set since `since`.
    pub fn collect_staging_events(&self, since: DateTime<Utc>) -> Vec<Event> {
        let snapshot_file = self.data_dir.join("git_stage_snapshots.jsonl");
        if !snapshot_file.exists() {
            return Vec::new();
        }

        let Ok(content) = std::fs::read_to_string(&snapshot_file) else {
            return Vec::new();
        };

        // Group snapshots by repo
        let mut by_repo: HashMap<String, Vec<(DateTime<Utc>, serde_json::Value)>> =
            HashMap::new();

        for line in content.lines() {
            let Ok(obj) = serde_json::from_str::<serde_json::Value>(line) else {
                continue;
            };
            let Some(ts_str) = obj.get("timestamp").and_then(|v| v.as_str()) else {
                continue;
            };
            let Ok(ts) = DateTime::parse_from_rfc3339(ts_str) else {
                continue;
            };
            let ts = ts.with_timezone(&Utc);
            if ts < since {
                continue;
            }
            let repo = obj.get("repo").and_then(|v| v.as_str()).unwrap_or("").to_owned();
            by_repo.entry(repo).or_default().push((ts, obj));
        }

        let mut events = Vec::new();

        for (repo, snapshots) in &by_repo {
            if snapshots.len() < 2 {
                continue;
            }

            let mut prev_staged: HashSet<String> = snapshot_files(&snapshots[0].1, "staged");

            for (ts, entry) in &snapshots[1..] {
                let curr_staged: HashSet<String> = snapshot_files(entry, "staged");
                let newly_staged: HashSet<_> = curr_staged.difference(&prev_staged).collect();
                let newly_unstaged: HashSet<_> = prev_staged.difference(&curr_staged).collect();

                if newly_staged.is_empty() && newly_unstaged.is_empty() {
                    prev_staged = curr_staged;
                    continue;
                }

                let project = Path::new(repo)
                    .file_name()
                    .map(|n| n.to_string_lossy().into_owned())
                    .unwrap_or_default();

                let all_staged: Vec<String> = {
                    let mut v: Vec<String> = curr_staged.iter().cloned().collect();
                    v.sort();
                    v
                };

                let file_list = all_staged[..all_staged.len().min(10)].join(", ");
                let suffix = if all_staged.len() > 10 {
                    format!(" (+{} more)", all_staged.len() - 10)
                } else {
                    String::new()
                };

                let mut parts = Vec::new();
                if !newly_staged.is_empty() {
                    let mut ns: Vec<_> = newly_staged.iter().collect();
                    ns.sort();
                    parts.push(format!("staged: {}", ns.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", ")));
                }
                if !newly_unstaged.is_empty() {
                    let mut nu: Vec<_> = newly_unstaged.iter().collect();
                    nu.sort();
                    parts.push(format!("unstaged: {}", nu.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", ")));
                }

                let diff = entry.get("staged_diff").and_then(|v| v.as_str()).unwrap_or("");
                let diff_section = if diff.is_empty() {
                    String::new()
                } else {
                    format!("\nDiff:\n{diff}")
                };

                let raw = format!(
                    "Git index change in {project}\nChange: {}\nCurrently staged: {file_list}{suffix}{diff_section}",
                    parts.join("; ")
                );

                let mut e = Event::new(EventType::GitStaged, raw);
                e.timestamp = *ts;
                e.source = Some("git".to_owned());
                e.project = Some(project);
                e.metadata.insert("repo_path".into(), serde_json::json!(repo));
                e.metadata.insert("staged_files".into(), serde_json::json!(all_staged));
                e.metadata.insert("newly_staged".into(), serde_json::json!(newly_staged.iter().collect::<Vec<_>>()));
                e.metadata.insert("newly_unstaged".into(), serde_json::json!(newly_unstaged.iter().collect::<Vec<_>>()));

                events.push(e);
                prev_staged = curr_staged;
            }
        }

        events
    }
}

// ---------------------------------------------------------------------------
// Repo discovery (max depth 3, skips known-slow dirs)
// ---------------------------------------------------------------------------

fn find_git_repos(search_path: &Path) -> Vec<PathBuf> {
    let mut repos = Vec::new();
    walk_for_repos(search_path, 0, &mut repos);
    repos
}

fn walk_for_repos(path: &Path, depth: usize, out: &mut Vec<PathBuf>) {
    if depth > 3 {
        return;
    }
    let Ok(entries) = std::fs::read_dir(path) else {
        return;
    };
    for entry in entries.flatten() {
        let child = entry.path();
        if !child.is_dir() {
            continue;
        }
        let name = child.file_name().and_then(|n| n.to_str()).unwrap_or("");
        if name.starts_with('.') || SKIP_DIRS.contains(&name) {
            continue;
        }
        if name == ".git" {
            out.push(path.to_path_buf());
            return;
        }
        walk_for_repos(&child, depth + 1, out);
    }
}

// ---------------------------------------------------------------------------
// git2 helpers
// ---------------------------------------------------------------------------

fn commit_stats(
    repo: &Repository,
    commit: &git2::Commit,
) -> (Vec<String>, usize, usize, String) {
    let mut files = Vec::new();
    let mut insertions = 0usize;
    let mut deletions = 0usize;
    let mut diff_text = String::new();

    let parent_tree = commit.parents().next().and_then(|p| p.tree().ok());
    let commit_tree = commit.tree().ok();

    if let (Some(ct), parent_opt) = (commit_tree, parent_tree) {
        if let Ok(diff) = repo.diff_tree_to_tree(parent_opt.as_ref(), Some(&ct), None) {
            let _ = diff.foreach(
                &mut |delta, _| {
                    if let Some(path) = delta.new_file().path() {
                        files.push(path.to_string_lossy().into_owned());
                    }
                    true
                },
                None,
                None,
                None,
            );

            if let Ok(stats) = diff.stats() {
                insertions = stats.insertions();
                deletions = stats.deletions();
            }

            let mut diff_bytes: Vec<u8> = Vec::new();
            if diff.print(git2::DiffFormat::Patch, |_d, _h, line| {
                diff_bytes.extend_from_slice(line.content());
                true
            }).is_ok() {
                diff_text = String::from_utf8_lossy(&diff_bytes).into_owned();
                if diff_text.len() > 16_000 {
                    diff_text.truncate(16_000);
                    diff_text.push_str("\n...(truncated)");
                }
            }
        }
    }

    files.truncate(30);
    (files, insertions, deletions, diff_text)
}

fn staging_state(repo: &Repository) -> (Vec<String>, Vec<String>, Vec<String>, String) {
    // Staged files: diff HEAD → index
    let staged = {
        let head_tree = repo
            .head()
            .ok()
            .and_then(|h| h.peel_to_tree().ok());
        let index = repo.index().ok();
        let mut files = Vec::new();
        if let (Some(tree), Some(idx)) = (head_tree, index) {
            if let Ok(diff) = repo.diff_tree_to_index(Some(&tree), Some(&idx), None) {
                let _ = diff.foreach(
                    &mut |delta, _| {
                        if let Some(p) = delta.new_file().path() {
                            files.push(p.to_string_lossy().into_owned());
                        }
                        true
                    },
                    None, None, None,
                );
            }
        }
        files
    };

    // Unstaged files: diff index → workdir
    let unstaged = {
        let mut files = Vec::new();
        if let Ok(diff) = repo.diff_index_to_workdir(None, None) {
            let _ = diff.foreach(
                &mut |delta, _| {
                    if let Some(p) = delta.old_file().path() {
                        files.push(p.to_string_lossy().into_owned());
                    }
                    true
                },
                None, None, None,
            );
        }
        files
    };

    // Untracked (max 20)
    let untracked: Vec<String> = {
        let mut opts = git2::StatusOptions::new();
        opts.include_untracked(true);
        repo.statuses(Some(&mut opts))
            .ok()
            .map(|statuses| {
                statuses
                    .iter()
                    .filter(|s| s.status().contains(git2::Status::WT_NEW))
                    .take(20)
                    .filter_map(|s| s.path().map(|p| p.to_owned()))
                    .collect()
            })
            .unwrap_or_default()
    };

    // Staged diff text
    let staged_diff = {
        let head_tree = repo.head().ok().and_then(|h| h.peel_to_tree().ok());
        let index = repo.index().ok();
        let mut diff_text = String::new();
        if let (Some(tree), Some(idx)) = (head_tree, index) {
            if let Ok(diff) = repo.diff_tree_to_index(Some(&tree), Some(&idx), None) {
                let mut buf = Vec::new();
                let _ = diff.print(git2::DiffFormat::Patch, |_, _, line| {
                    buf.extend_from_slice(line.content());
                    true
                });
                diff_text = String::from_utf8_lossy(&buf).into_owned();
                if diff_text.len() > 8_000 {
                    diff_text.truncate(8_000);
                }
            }
        }
        diff_text
    };

    (staged, unstaged, untracked, staged_diff)
}

fn snapshot_files(entry: &serde_json::Value, key: &str) -> HashSet<String> {
    entry
        .get(key)
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().filter_map(|v| v.as_str().map(|s| s.to_owned())).collect())
        .unwrap_or_default()
}

fn trim_jsonl(path: &Path, max_rows: usize) -> Result<()> {
    let content = std::fs::read_to_string(path)?;
    let lines: Vec<&str> = content.lines().collect();
    if lines.len() <= max_rows {
        return Ok(());
    }
    let keep = &lines[lines.len() - max_rows..];
    let tmp = path.with_extension("jsonl.tmp");
    std::fs::write(&tmp, keep.join("\n") + "\n")?;
    std::fs::rename(tmp, path)?;
    Ok(())
}
