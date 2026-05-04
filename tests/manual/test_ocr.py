import pytest

from patcha.collectors.accessibility import AccessibilityCollector


def test_ocr_screen_capture():
    collector = AccessibilityCollector()
    result = collector._take_ocr_screenshot("", "")
    if result is None:
        pytest.skip("OCR binary unavailable or screencapture inaccessible")
    print(f"\ncontent ({len(result['text'])} chars):\n{result['text']}")
    assert result["text"].strip()
