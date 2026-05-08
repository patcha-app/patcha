import pytest
from patcha.collectors.accessibility import AccessibilityCollector

diff = AccessibilityCollector._content_diff


@pytest.mark.unit
def test_no_change():
    assert diff("line1\nline2", "line1\nline2") == ""


@pytest.mark.unit
def test_new_lines_added():
    result = diff("line1", "line1\nline2")
    assert "line2" in result
    assert "line1" not in result


@pytest.mark.unit
def test_removed_lines_not_included():
    result = diff("line1\nline2", "line2")
    assert result == ""


@pytest.mark.unit
def test_empty_lines_skipped():
    result = diff("a", "a\n\n\nb")
    assert "" not in result.splitlines()
    assert "b" in result


@pytest.mark.unit
def test_completely_new_content():
    result = diff("old content here", "brand new content that is different")
    assert "brand new content that is different" in result


@pytest.mark.unit
def test_empty_old():
    result = diff("", "new line")
    assert "new line" in result


@pytest.mark.unit
def test_empty_both():
    assert diff("", "") == ""
