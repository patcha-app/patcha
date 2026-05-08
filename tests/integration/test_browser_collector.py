from datetime import datetime, timezone

import pytest

from patcha.collectors.browser import BrowserCollector
from patcha.db.models import EventType


@pytest.fixture
def chrome_collector(chrome_db, mocker):
    mocker.patch(
        "patcha.collectors.browser.config",
        browser_history_paths={
            "chrome": str(chrome_db),
            "safari": "/nonexistent/History.db",
            "arc": "/nonexistent/History",
        },
    )
    return BrowserCollector()


@pytest.fixture
def safari_collector(safari_db, mocker):
    mocker.patch(
        "patcha.collectors.browser.config",
        browser_history_paths={
            "chrome": "/nonexistent/History",
            "safari": str(safari_db),
            "arc": "/nonexistent/History",
        },
    )
    return BrowserCollector()


_SINCE = datetime(2025, 1, 1, tzinfo=timezone.utc)


@pytest.mark.integration
def test_collect_chrome_returns_event(chrome_collector):
    events = chrome_collector.collect_chrome_history(since=_SINCE)
    assert len(events) == 1
    assert events[0].type == EventType.BROWSER
    assert events[0].source == "chrome"


@pytest.mark.integration
def test_collect_chrome_timestamp_conversion(chrome_collector):
    events = chrome_collector.collect_chrome_history(since=_SINCE)
    ts = events[0].timestamp
    expected = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    assert abs((ts - expected).total_seconds()) < 2


@pytest.mark.integration
def test_collect_chrome_domain_in_metadata(chrome_collector):
    events = chrome_collector.collect_chrome_history(since=_SINCE)
    assert events[0].metadata.get("domain") == "github.com"


@pytest.mark.integration
def test_collect_safari_returns_event(safari_collector):
    events = safari_collector.collect_safari_history(since=_SINCE)
    assert len(events) == 1
    assert events[0].type == EventType.BROWSER
    assert events[0].source == "safari"


@pytest.mark.integration
def test_nonexistent_chrome_db_returns_empty(mocker):
    mocker.patch(
        "patcha.collectors.browser.config",
        browser_history_paths={
            "chrome": "/nonexistent/path/History",
            "safari": "/nonexistent/path",
            "arc": "/nonexistent/path",
        },
    )
    collector = BrowserCollector()
    assert collector.collect_chrome_history() == []


@pytest.mark.integration
def test_chrome_guard_disables_after_failures(tmp_path, mocker):
    mocker.patch(
        "patcha.collectors.browser.config",
        browser_history_paths={
            "chrome": str(tmp_path / "History"),
            "safari": "/nonexistent",
            "arc": "/nonexistent",
        },
    )
    (tmp_path / "History").write_bytes(b"not a sqlite db")
    collector = BrowserCollector()
    for _ in range(3):
        collector.collect_chrome_history()
    assert not collector._chrome_guard.ok


@pytest.mark.integration
def test_collect_all_skips_missing_sources(chrome_collector):
    events = chrome_collector.collect_all()
    assert all(e.type == EventType.BROWSER for e in events)
