import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
import git
from git import Repo

from patcha.config import config
from patcha.db.models import Event, EventType, GitCommit, GitStash
from patcha.utils.guard import CollectorGuard
from patcha.utils.jsonl import trim_jsonl

logger = logging.getLogger(__name__)

# Staging snapshots carry full diffs and are appended every poll, so keep a
# tighter bound than the lightweight window/screen logs.
_STAGE_MAX_LOG_ROWS = 20_000
_STAGE_TRIM_EVERY = 500


class GitCollector:
    def __init__(self, repo_path: Optional[str] = None):
        self.repo_path = Path(repo_path) if repo_path else Path.cwd()
        self._commits_guard = CollectorGuard("git_commits")
        self._stashes_guard = CollectorGuard("git_stashes")
        self._stage_write_count: int = 0

    def is_git_repo(self) -> bool:
        try:
            Repo(self.repo_path)
            return True
        except git.exc.InvalidGitRepositoryError:
            return False

    _SKIP_DIRS = {
        "Music",
        "Pictures",
        "Movies",
        "Library",
        "Applications",
        "iCloud Drive",
        "iCloud",
    }

    def _find_git_repos(self, search_path: Optional[Path] = None) -> List[Path]:
        search_path = search_path or self.repo_path
        git_repos: List[Path] = []

        def _walk(path: Path, depth: int) -> None:
            if depth > 3:
                return
            try:
                for entry in path.iterdir():
                    if not entry.is_dir():
                        continue
                    name = entry.name
                    if name.startswith("."):
                        continue
                    if name in self._SKIP_DIRS:
                        continue
                    if name == ".git":
                        if path not in git_repos:
                            git_repos.append(path)
                        return
                    _walk(entry, depth + 1)
            except PermissionError:
                pass
            except Exception as e:
                logger.debug("Error walking %s: %s", path, e)

        try:
            if (search_path / ".git").exists():
                git_repos.append(search_path)
            else:
                _walk(search_path, 1)
        except Exception as e:
            logger.debug("Error searching for git repositories: %s", e)

        return git_repos

    def collect_commits(self, since: Optional[datetime] = None) -> List[Event]:
        if not self._commits_guard.ok:
            return []
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
                    commits = list(
                        repo.iter_commits(since=since.strftime("%Y-%m-%d %H:%M:%S"))
                    )
                else:
                    commits = list(
                        repo.iter_commits(max_count=50)
                    )  # Limit to recent 50 commits for performance

                for commit in commits:
                    commit_time = datetime.fromtimestamp(
                        commit.committed_date, tz=timezone.utc
                    )

                    # Double-check the timestamp (since git --since might be inclusive)
                    if since and commit_time < since:
                        continue

                    all_files_changed = list(commit.stats.files.keys())

                    try:
                        branch = repo.active_branch.name
                    except TypeError:
                        branch = "unknown"

                    try:
                        if commit.parents:
                            diff_text = repo.git.diff(
                                commit.parents[0].hexsha, commit.hexsha
                            )
                        else:
                            diff_text = ""
                    except Exception:
                        diff_text = ""

                    git_commit = GitCommit(
                        hash=commit.hexsha,
                        message=commit.message.strip(),
                        author=str(commit.author),
                        timestamp=commit_time,
                        files_changed=all_files_changed[:30],
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
                            "lines_changed": git_commit.insertions
                            + git_commit.deletions,
                        },
                    )
                    events.append(event)
                self._commits_guard.success()

            except Exception as e:
                self._commits_guard.fail(e, msg=f"{repo_path}: {e}")
                continue

        return events

    def collect_stashes(self, since: Optional[datetime] = None) -> List[Event]:
        if not self._stashes_guard.ok or not self.is_git_repo():
            return []

        events = []
        try:
            repo = Repo(self.repo_path)

            stash_lines = repo.git.stash(
                "list", "--format=%gd%x00%H%x00%ct%x00%gs"
            ).split("\n")
            for stash in stash_lines:
                if not stash.strip():
                    continue

                stash_parts = stash.split("\x00", 3)
                if len(stash_parts) != 4:
                    continue

                stash_name, stash_hash, stash_timestamp, stash_message = stash_parts
                try:
                    stash_time = datetime.fromtimestamp(
                        int(stash_timestamp), tz=timezone.utc
                    )
                except ValueError:
                    stash_time = datetime.now(timezone.utc)

                if since and stash_time < since:
                    continue

                try:
                    stash_info = repo.git.stash("show", "--stat", stash_name)
                    files_changed = [
                        line.split("|")[0].strip()
                        for line in stash_info.split("\n")[:-1]
                    ]
                except Exception:
                    files_changed = []

                git_stash = GitStash(
                    name=stash_name,
                    message=stash_message,
                    timestamp=stash_time,
                    files_changed=files_changed,
                )

                event = Event(
                    timestamp=git_stash.timestamp,
                    type=EventType.GIT_STASH,
                    source="git",
                    project=self._get_project_name(),
                    raw_content=json.dumps(git_stash.model_dump(), default=str),
                    source_doc_id=f"{self.repo_path.resolve()}:stash:{stash_hash}",
                    metadata={
                        "repo_path": str(self.repo_path),
                        "stash_hash": stash_hash,
                        "files_count": len(files_changed),
                    },
                )
                events.append(event)

            self._stashes_guard.success()
        except Exception as e:
            self._stashes_guard.fail(e)

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
                    staged_diff = repo.git.diff("HEAD", "--staged")
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

                self._stage_write_count += 1
                if self._stage_write_count % _STAGE_TRIM_EVERY == 0:
                    trim_jsonl(snapshot_file, _STAGE_MAX_LOG_ROWS)

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
                    suffix = (
                        f" (+{len(all_staged) - 10} more)"
                        if len(all_staged) > 10
                        else ""
                    )

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

                    events.append(
                        Event(
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
                        )
                    )

                prev_staged = curr_staged

        return events
