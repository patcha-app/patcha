import json
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
import git
from git import Repo

from memorai.config import config
from memorai.db.models import Event, EventType, GitCommit, GitStash


class GitCollector:
    def __init__(self, repo_path: Optional[str] = None):
        self.repo_path = Path(repo_path) if repo_path else Path.cwd()

        self.language_extensions = {
            '.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.cpp', '.c', '.h', '.hpp',
            '.cs', '.php', '.rb', '.go', '.rs', '.swift', '.kt', '.scala', '.sh',
            '.bash', '.zsh', '.fish', '.ps1', '.r', '.R', '.m', '.mm', '.pl', '.pm',
            '.lua', '.dart', '.elm', '.ex', '.exs', '.clj', '.cljs', '.hs', '.ml',
            '.fs', '.fsx', '.vb', '.pas', '.dpr', '.asm', '.s', '.f90', '.f95',
            '.html', '.htm', '.css', '.scss', '.sass', '.less', '.vue', '.svelte',
            '.xml', '.xsl', '.xslt', '.sql', '.graphql', '.gql',
            '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.env',
            '.dockerfile', '.dockerignore', '.gitignore', '.gitattributes',
        }

    def is_git_repo(self) -> bool:
        try:
            Repo(self.repo_path)
            return True
        except git.exc.InvalidGitRepositoryError:
            return False

    def _filter_language_files(self, files: List[str]) -> List[str]:
        filtered_files = []
        for file_path in files:
            file_ext = Path(file_path).suffix.lower()
            if file_ext in self.language_extensions:
                filtered_files.append(file_path)
        return filtered_files

    def _find_git_repos(self, search_path: Optional[Path] = None) -> List[Path]:
        search_path = search_path or self.repo_path
        git_repos = []

        try:
            if (search_path / '.git').exists():
                git_repos.append(search_path)

            for depth in range(1, 4):
                pattern = '/'.join(['*'] * depth) + '/.git'
                for git_dir in search_path.glob(pattern):
                    repo_path = git_dir.parent
                    rel_parts = repo_path.relative_to(search_path).parts
                    if any(part.startswith('.') for part in rel_parts):
                        continue
                    if repo_path not in git_repos:
                        git_repos.append(repo_path)

        except Exception as e:
            print(f"Error searching for git repositories: {e}")

        return git_repos

    def collect_commits(self, since: Optional[datetime] = None) -> List[Event]:
        events = []

        git_repos = self._find_git_repos(Path.home())
        if not git_repos:
            return []

        for repo_path in git_repos:
            try:
                repo = Repo(repo_path)

                # If since is provided, only get commits after that time
                if since:
                    # Use git log with --since parameter for efficiency
                    commits = list(repo.iter_commits(since=since.strftime('%Y-%m-%d %H:%M:%S')))
                else:
                    commits = list(repo.iter_commits(max_count=50))  # Limit to recent 50 commits for performance

                for commit in commits:
                    commit_time = datetime.fromtimestamp(commit.committed_date, tz=timezone.utc)

                    # Double-check the timestamp (since git --since might be inclusive)
                    if since and commit_time < since:
                        continue

                    # Get all changed files
                    all_files_changed = list(commit.stats.files.keys())

                    # Filter to only include language files for better summaries
                    language_files = self._filter_language_files(all_files_changed)

                    try:
                        branch = repo.active_branch.name
                    except TypeError:
                        branch = "unknown"

                    try:
                        if commit.parents:
                            diff_text = repo.git.diff(commit.parents[0].hexsha, commit.hexsha)[:4000]
                        else:
                            diff_text = ""
                    except Exception:
                        diff_text = ""

                    git_commit = GitCommit(
                        hash=commit.hexsha,
                        message=commit.message.strip(),
                        author=str(commit.author),
                        timestamp=commit_time,
                        files_changed=language_files if language_files else all_files_changed[:5],
                        insertions=commit.stats.total["insertions"],
                        deletions=commit.stats.total["deletions"],
                        branch=branch,
                        diff=diff_text,
                    )

                    event = Event(
                        timestamp=commit_time,
                        type=EventType.GIT_COMMIT,
                        source="git",
                        project=repo_path.name,
                        raw_content=json.dumps(git_commit.model_dump(), default=str),
                        source_doc_id=f"{repo_path}:{commit.hexsha}",
                        metadata={
                            "repo_path": str(repo_path),
                            "files_count": len(all_files_changed),
                            "language_files_count": len(language_files),
                            "lines_changed": git_commit.insertions + git_commit.deletions,
                            "filtered_for_summary": len(language_files) > 0
                        }
                    )
                    events.append(event)

            except Exception as e:
                print(f"Error collecting git commits from {repo_path}: {e}")
                continue

        return events

    def collect_stashes(self, since: Optional[datetime] = None) -> List[Event]:
        if not self.is_git_repo():
            return []

        events = []
        try:
            repo = Repo(self.repo_path)

            for stash in repo.git.stash("list").split("\n"):
                if not stash.strip():
                    continue

                stash_parts = stash.split(": ")
                if len(stash_parts) < 3:
                    continue

                stash_name = stash_parts[0]
                stash_message = ": ".join(stash_parts[2:])

                try:
                    stash_info = repo.git.stash("show", "--stat", stash_name)
                    files_changed = [line.split("|")[0].strip()
                                   for line in stash_info.split("\n")[:-1]]
                except:
                    files_changed = []

                git_stash = GitStash(
                    name=stash_name,
                    message=stash_message,
                    timestamp=datetime.now(timezone.utc),
                    files_changed=files_changed
                )

                event = Event(
                    timestamp=git_stash.timestamp,
                    type=EventType.GIT_STASH,
                    source="git",
                    project=self._get_project_name(),
                    raw_content=json.dumps(git_stash.model_dump(), default=str),
                    metadata={
                        "repo_path": str(self.repo_path),
                        "files_count": len(files_changed)
                    }
                )
                events.append(event)

        except Exception as e:
            print(f"Error collecting git stashes: {e}")

        return events

    def _get_project_name(self, repo_path: Optional[Path] = None) -> str:
        target_path = repo_path or self.repo_path
        return target_path.name

    def record_staging_snapshot(self) -> None:
        """Snapshot staged/unstaged state for all known repos and append to log."""
        repos = self._find_git_repos(Path.home())
        config.data_dir.mkdir(parents=True, exist_ok=True)
        snapshot_file = config.data_dir / "git_stage_snapshots.jsonl"

        for repo_path in repos:
            try:
                repo = Repo(repo_path)

                try:
                    staged = [d.a_path for d in repo.index.diff("HEAD")]
                except Exception:
                    staged = []

                unstaged = [d.a_path for d in repo.index.diff(None)]
                untracked = list(repo.untracked_files)[:20]

                try:
                    staged_diff = repo.git.diff("HEAD", "--staged")[:4000]
                except Exception:
                    staged_diff = ""

                entry = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "repo": str(repo_path),
                    "staged": staged,
                    "unstaged": unstaged,
                    "untracked": untracked,
                    "staged_diff": staged_diff,
                }
                with open(snapshot_file, "a") as f:
                    f.write(json.dumps(entry) + "\n")

            except Exception:
                continue

    def collect_staging_events(self, since: datetime) -> List[Event]:
        """Return events for when the staged file set changed since `since`.

        Emits one event per change in staged files — e.g. when the user
        runs `git add` or `git reset`. Consecutive snapshots with identical
        staged files produce no event.
        """
        snapshot_file = config.data_dir / "git_stage_snapshots.jsonl"
        if not snapshot_file.exists():
            return []

        snapshots_by_repo: dict = defaultdict(list)
        with open(snapshot_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts = datetime.fromisoformat(entry["timestamp"])
                    if ts >= since:
                        snapshots_by_repo[entry["repo"]].append((ts, entry))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

        events: List[Event] = []

        for repo, snapshots in snapshots_by_repo.items():
            if len(snapshots) < 2:
                continue

            prev_staged = set(snapshots[0][1].get("staged", []))

            for ts, entry in snapshots[1:]:
                curr_staged = set(entry.get("staged", []))
                newly_staged = curr_staged - prev_staged
                newly_unstaged = prev_staged - curr_staged

                if newly_staged or newly_unstaged:
                    project = Path(repo).name
                    all_staged = sorted(curr_staged)
                    file_list = ", ".join(all_staged[:10])
                    suffix = f" (+{len(all_staged) - 10} more)" if len(all_staged) > 10 else ""

                    parts = []
                    if newly_staged:
                        parts.append(f"staged: {', '.join(sorted(newly_staged))}")
                    if newly_unstaged:
                        parts.append(f"unstaged: {', '.join(sorted(newly_unstaged))}")

                    diff_content = entry.get("staged_diff", "")
                    diff_section = f"\nDiff:\n{diff_content}" if diff_content else ""

                    raw = (
                        f"Git index change in {project}\n"
                        f"Change: {'; '.join(parts)}\n"
                        f"Currently staged: {file_list}{suffix}"
                        f"{diff_section}"
                    )

                    events.append(Event(
                        timestamp=ts,
                        type=EventType.GIT_STAGED,
                        source="git",
                        project=project,
                        raw_content=raw,
                        metadata={
                            "repo_path": repo,
                            "staged_files": all_staged,
                            "newly_staged": sorted(newly_staged),
                            "newly_unstaged": sorted(newly_unstaged),
                            "unstaged_files": entry.get("unstaged", []),
                        },
                    ))

                prev_staged = curr_staged

        return events
