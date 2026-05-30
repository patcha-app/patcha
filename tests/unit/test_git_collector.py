from datetime import timedelta

import pytest
from git import Repo

from patcha.collectors.git import GitCollector
from patcha.db.models import EventType


@pytest.fixture(autouse=True)
def clear_git_hook_environment(monkeypatch):
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(name, raising=False)


def _commit_file(repo_path, filename, content):
    path = repo_path / filename
    path.write_text(content)
    repo = Repo(repo_path)
    repo.index.add([filename])
    repo.index.commit(f"commit {filename}")


def test_collect_stashes_uses_stable_identity_and_timestamp(tmp_path):
    repo = Repo.init(tmp_path)
    repo.config_writer().set_value("user", "name", "Test User").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()
    _commit_file(tmp_path, "tracked.txt", "initial\n")

    (tmp_path / "tracked.txt").write_text("changed\n")
    repo.git.stash("push", "-m", "save tracked change")

    collector = GitCollector(str(tmp_path))
    first_events = collector.collect_stashes()
    second_events = collector.collect_stashes()

    assert len(first_events) == 1
    assert len(second_events) == 1

    first = first_events[0]
    second = second_events[0]

    assert first.type == EventType.GIT_STASH
    assert first.source_doc_id == second.source_doc_id
    assert first.timestamp == second.timestamp
    assert first.metadata["stash_hash"] == second.metadata["stash_hash"]
    assert first.metadata["repo_path"] == str(tmp_path)


def test_collect_stashes_respects_since_filter(tmp_path):
    repo = Repo.init(tmp_path)
    repo.config_writer().set_value("user", "name", "Test User").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()
    _commit_file(tmp_path, "tracked.txt", "initial\n")

    (tmp_path / "tracked.txt").write_text("changed\n")
    repo.git.stash("push", "-m", "save tracked change")

    collector = GitCollector(str(tmp_path))
    event = collector.collect_stashes()[0]

    assert collector.collect_stashes(event.timestamp + timedelta(seconds=1)) == []
