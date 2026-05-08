import pytest
from patcha.collectors.browser import BrowserCollector


@pytest.fixture
def collector():
    from unittest.mock import patch
    with patch("patcha.collectors.browser.config"):
        c = BrowserCollector.__new__(BrowserCollector)
        c.history_paths = {}
        return c


@pytest.mark.unit
def test_non_youtube_url_unchanged(collector):
    assert collector._enhance_youtube_title("My Title", "https://github.com") == "My Title"


@pytest.mark.unit
def test_youtube_watch_url(collector):
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    result = collector._enhance_youtube_title("Never Gonna Give You Up", url)
    assert "Never Gonna Give You Up" in result


@pytest.mark.unit
def test_youtube_short_url(collector):
    url = "https://youtu.be/dQw4w9WgXcQ"
    result = collector._enhance_youtube_title("Some Video", url)
    assert "Some Video" in result


@pytest.mark.unit
def test_empty_title_with_youtube(collector):
    url = "https://www.youtube.com/watch?v=abc123"
    result = collector._enhance_youtube_title("Untitled", url)
    assert result is not None and len(result) > 0


@pytest.mark.unit
def test_non_youtube_empty_title(collector):
    assert collector._enhance_youtube_title("Untitled", "https://example.com") == "Untitled"
