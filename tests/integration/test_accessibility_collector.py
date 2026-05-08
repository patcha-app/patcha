import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from patcha.collectors.accessibility import AccessibilityCollector


@pytest.fixture
def collector(tmp_path, mocker):
    mocker.patch("patcha.collectors.accessibility.config", data_dir=tmp_path)
    mocker.patch("patcha.collectors.accessibility.settings", get=lambda k: None)
    c = AccessibilityCollector()
    c.log_file = tmp_path / "screen_log.jsonl"
    return c


@pytest.mark.integration
def test_collect_screen_text_reads_log(collector, screen_log):
    collector.log_file = screen_log
    since = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    events = collector.collect_screen_text(since)
    assert len(events) > 0
    assert all(e["source"] == "accessibility" for e in events)


@pytest.mark.integration
def test_collect_screen_text_since_filter(collector, screen_log):
    collector.log_file = screen_log
    since = datetime(2026, 1, 1, 12, 0, 5, tzinfo=timezone.utc)
    events = collector.collect_screen_text(since)
    for e in events:
        assert e["timestamp"] >= since


@pytest.mark.integration
def test_collect_screen_text_empty_log(collector, tmp_path):
    collector.log_file = tmp_path / "empty.jsonl"
    collector.log_file.write_text("")
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert collector.collect_screen_text(since) == []


@pytest.mark.integration
def test_collect_screen_text_missing_log(collector, tmp_path):
    collector.log_file = tmp_path / "nonexistent.jsonl"
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert collector.collect_screen_text(since) == []


@pytest.mark.integration
def test_reconstruct_layout_roundtrip(ocr_observations):
    result = AccessibilityCollector._reconstruct_layout(ocr_observations)
    assert "File" in result
    assert "def foo" in result


@pytest.mark.integration
def test_record_current_screen_writes_jsonl(collector, mocker):
    mocker.patch.object(
        collector,
        "_get_screen_content",
        return_value={
            "app": "TestApp",
            "window_title": "Test Window",
            "text": "A" * 100,
        },
    )
    collector.record_current_screen()
    assert collector.log_file.exists()
    line = json.loads(collector.log_file.read_text().strip())
    assert line["app"] == "TestApp"
    assert line["text"] == "A" * 100


@pytest.mark.integration
def test_record_current_screen_skips_short_diff(collector, mocker):
    base = "\n".join(f"Content line {i} with enough text here" for i in range(5))
    mocker.patch.object(
        collector,
        "_get_screen_content",
        return_value={"app": "App", "window_title": "Win", "text": base},
    )
    collector.record_current_screen()
    # Second call: adds a tiny new line ("x" = 1 char) — diff < _MIN_DIFF_LEN (30)
    mocker.patch.object(
        collector,
        "_get_screen_content",
        return_value={"app": "App", "window_title": "Win", "text": base + "\nx"},
    )
    collector.record_current_screen()
    lines = [line for line in collector.log_file.read_text().splitlines() if line]
    assert len(lines) == 1


@pytest.mark.integration
def test_screenshot_hash_skips_unchanged_app(collector, tmp_path, mocker):
    mocker.patch(
        "patcha.collectors.accessibility._has_screen_recording_permission",
        return_value=True,
    )
    collector._ensure_ocr_binary = lambda: Path("/fake/ocr")

    fake_png = tmp_path / "frame.png"
    fake_png.write_bytes(b"\x89PNG fake image data for hashing")

    def fake_run(cmd, **kwargs):
        m = mocker.MagicMock()
        m.returncode = 0
        if cmd[0] == "screencapture":
            import shutil

            shutil.copy(str(fake_png), cmd[-1])
            m.stdout = ""
        else:
            m.stdout = "[]"
        return m

    mocker.patch("patcha.collectors.accessibility.subprocess.run", side_effect=fake_run)

    collector._take_ocr_screenshot("App", "Win")
    result2 = collector._take_ocr_screenshot("App", "Win")

    assert ("App", "Win") in collector._screenshot_hashes
    assert result2 is None
