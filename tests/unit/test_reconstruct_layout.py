import pytest
from patcha.collectors.accessibility import AccessibilityCollector

reconstruct = AccessibilityCollector._reconstruct_layout


@pytest.mark.unit
def test_empty_observations():
    assert reconstruct([]) == ""


@pytest.mark.unit
def test_single_observation():
    obs = [{"text": "Hello world this is a long enough line to pass", "x": 0.1, "y": 0.5, "w": 0.2, "h": 0.02}]
    result = reconstruct(obs)
    assert "Hello world" in result


@pytest.mark.unit
def test_single_line_joined_with_spaces():
    row = [
        {"text": "File", "x": 0.05, "y": 0.98, "w": 0.04, "h": 0.01},
        {"text": "Edit", "x": 0.10, "y": 0.98, "w": 0.04, "h": 0.01},
        {"text": "View", "x": 0.15, "y": 0.98, "w": 0.04, "h": 0.01},
        {"text": "Selection", "x": 0.20, "y": 0.98, "w": 0.06, "h": 0.01},
        {"text": "Terminal", "x": 0.27, "y": 0.98, "w": 0.06, "h": 0.01},
        {"text": "Help", "x": 0.34, "y": 0.98, "w": 0.04, "h": 0.01},
    ]
    result = reconstruct(row)
    assert "File" in result and "Edit" in result and "View" in result
    assert "\n" not in result
    assert "\n" not in result


@pytest.mark.unit
def test_multi_line_produces_newline(ocr_observations):
    result = reconstruct(ocr_observations)
    assert "\n" in result


@pytest.mark.unit
def test_top_to_bottom_order(ocr_observations):
    result = reconstruct(ocr_observations)
    lines = result.strip().splitlines()
    assert "File" in lines[0]
    assert any("def foo" in line or "return 42" in line for line in lines[1:])


@pytest.mark.unit
def test_left_to_right_order_within_line():
    obs = [
        {"text": "Zebra-word", "x": 0.8, "y": 0.5, "w": 0.05, "h": 0.01},
        {"text": "Alpha-word", "x": 0.1, "y": 0.5, "w": 0.05, "h": 0.01},
        {"text": "Middle-word", "x": 0.45, "y": 0.5, "w": 0.05, "h": 0.01},
    ]
    result = reconstruct(obs)
    assert result.index("Alpha") < result.index("Middle") < result.index("Zebra")


@pytest.mark.unit
def test_truncates_at_4000_chars():
    obs = [
        {"text": "x" * 100, "x": 0.0, "y": 1.0 - i * 0.005, "w": 0.5, "h": 0.004}
        for i in range(100)
    ]
    result = reconstruct(obs)
    assert len(result) <= 4000


@pytest.mark.unit
def test_returns_empty_for_short_result():
    obs = [{"text": "Hi", "x": 0.1, "y": 0.5, "w": 0.1, "h": 0.01}]
    result = reconstruct(obs)
    assert result == ""


@pytest.mark.unit
def test_adaptive_threshold_tall_text():
    tall_obs = [
        {"text": "First heading row of content", "x": 0.0, "y": 0.9, "w": 0.3, "h": 0.08},
        {"text": "Second paragraph of content", "x": 0.0, "y": 0.5, "w": 0.3, "h": 0.08},
        {"text": "Third section with more text", "x": 0.0, "y": 0.1, "w": 0.3, "h": 0.08},
    ]
    result = reconstruct(tall_obs)
    lines = [line for line in result.splitlines() if line.strip()]
    assert len(lines) == 3
