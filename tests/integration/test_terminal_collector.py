from datetime import datetime, timezone
from pathlib import Path

import pytest

from patcha.collectors.terminal import TerminalCollector
from patcha.db.models import EventType


@pytest.mark.integration
def test_collect_zsh_history_parses_timestamps(zsh_history_file, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: zsh_history_file.parent)
    collector = TerminalCollector()
    events = collector.collect_zsh_history()
    assert len(events) == 3
    assert all(e.type == EventType.TERMINAL for e in events)
    assert all(e.source == "zsh" for e in events)


@pytest.mark.integration
def test_collect_zsh_history_since_filter(zsh_history_file, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: zsh_history_file.parent)
    collector = TerminalCollector()
    since = datetime.fromtimestamp(1735732860, tz=timezone.utc)
    events = collector.collect_zsh_history(since=since)
    assert len(events) == 2


@pytest.mark.integration
def test_collect_zsh_history_skips_malformed(tmp_path, monkeypatch):
    bad = tmp_path / ".zsh_history"
    bad.write_text("this is not valid zsh format\nalso bad\n: broken;no_command\n")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    collector = TerminalCollector()
    events = collector.collect_zsh_history()
    assert events == []


@pytest.mark.integration
def test_collect_bash_history_uses_file_mtime(tmp_path, monkeypatch):
    hist = tmp_path / ".bash_history"
    hist.write_text("git status\ngit log\n")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    collector = TerminalCollector()
    events = collector.collect_bash_history()
    assert len(events) == 2
    mtime = datetime.fromtimestamp(hist.stat().st_mtime, tz=timezone.utc)
    for e in events:
        assert abs((e.timestamp - mtime).total_seconds()) < 1


@pytest.mark.integration
def test_guard_disables_after_failures(tmp_path, monkeypatch):
    hist = tmp_path / ".zsh_history"
    hist.write_text(": 1735732800:0;cmd\n")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    collector = TerminalCollector()
    original_open = open

    call_count = 0

    def boom(*args, **kwargs):
        nonlocal call_count
        if str(args[0]).endswith(".zsh_history") and "rb" in str(args):
            call_count += 1
            raise OSError("disk failure")
        return original_open(*args, **kwargs)

    monkeypatch.setattr("builtins.open", boom)
    for _ in range(3):
        collector.collect_zsh_history()
    assert not collector._zsh_guard.ok


@pytest.mark.integration
def test_collect_all_aggregates(zsh_history_file, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: zsh_history_file.parent)
    collector = TerminalCollector()
    events = collector.collect_all()
    zsh_events = [e for e in events if e.source == "zsh"]
    assert len(zsh_events) == 3
