import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from patcha.collectors.accessibility import AccessibilityCollector

pytestmark = pytest.mark.ocr_image

_FONT_PATH = "/System/Library/Fonts/Monaco.ttf"
_FONT_SIZE = 28
_LINE_HEIGHT = 42  # px between baselines at 28pt


def _font():
    try:
        return ImageFont.truetype(_FONT_PATH, _FONT_SIZE)
    except OSError:
        return ImageFont.load_default()


def _make_layout(panes: list[dict], img_w: int, img_h: int) -> Image.Image:
    """Draw text panes onto a white image.

    Each pane: {"x": px_left, "y": px_top, "lines": [...]}
    """
    img = Image.new("RGB", (img_w, img_h), "white")
    draw = ImageDraw.Draw(img)
    font = _font()
    for pane in panes:
        for i, line in enumerate(pane["lines"]):
            draw.text((pane["x"], pane["y"] + i * _LINE_HEIGHT), line, fill="black", font=font)
    return img


def _run_ocr(binary: Path, img: Image.Image) -> list[dict]:
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        img.save(path)
        result = subprocess.run(
            [str(binary), path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        return json.loads(result.stdout)
    finally:
        os.unlink(path)


def _reconstruct(obs: list[dict], select_x=None) -> str:
    return AccessibilityCollector._reconstruct_layout(obs, select_x=select_x)


@pytest.fixture(scope="session")
def ocr_binary(tmp_path_factory):
    src = Path(__file__).parents[1] / "patcha" / "macos" / "ocr.swift"
    out = tmp_path_factory.mktemp("bin") / "ocr"
    result = subprocess.run(
        ["swiftc", str(src), "-o", str(out)],
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0:
        pytest.skip("swiftc unavailable — skipping OCR image tests")
    return out


# ---------------------------------------------------------------------------
# Two-pane horizontal layout
# ---------------------------------------------------------------------------

_LEFT_LINES = [
    "SIDEBAR ALPHA",
    "SIDEBAR BETA",
    "SIDEBAR GAMMA",
    "SIDEBAR DELTA",
    "SIDEBAR EPSILON",
    "SIDEBAR ZETA",
]
_RIGHT_LINES = [
    "THREAD ALPHA",
    "THREAD BETA",
    "THREAD GAMMA",
    "THREAD DELTA",
    "THREAD EPSILON",
    "THREAD ZETA",
]

_2H_IMG_W = 1200
_2H_IMG_H = 800
_2H_LEFT_X = 50
_2H_RIGHT_X = 750
_2H_PANE_Y = 100
# normalized centers for select_x
_2H_LEFT_MID = (_2H_LEFT_X + 450) / 2 / _2H_IMG_W   # ~0.21
_2H_RIGHT_MID = (_2H_RIGHT_X + 1150) / 2 / _2H_IMG_W  # ~0.79


def _two_pane_h_image():
    return _make_layout(
        [
            {"x": _2H_LEFT_X, "y": _2H_PANE_Y, "lines": _LEFT_LINES},
            {"x": _2H_RIGHT_X, "y": _2H_PANE_Y, "lines": _RIGHT_LINES},
        ],
        _2H_IMG_W,
        _2H_IMG_H,
    )


def test_two_pane_h_cursor_select_left(ocr_binary):
    obs = _run_ocr(ocr_binary, _two_pane_h_image())
    assert obs, "OCR returned no observations"
    out = _reconstruct(obs, select_x=_2H_LEFT_MID)
    assert any(t in out for t in _LEFT_LINES), "left pane content missing"
    assert not any(t in out for t in _RIGHT_LINES), "right pane should be excluded"


def test_two_pane_h_cursor_select_right(ocr_binary):
    obs = _run_ocr(ocr_binary, _two_pane_h_image())
    assert obs, "OCR returned no observations"
    out = _reconstruct(obs, select_x=_2H_RIGHT_MID)
    assert any(t in out for t in _RIGHT_LINES), "right pane content missing"
    assert not any(t in out for t in _LEFT_LINES), "left pane should be excluded"


def test_two_pane_h_no_cursor_all_columns(ocr_binary):
    obs = _run_ocr(ocr_binary, _two_pane_h_image())
    assert obs, "OCR returned no observations"
    out = _reconstruct(obs, select_x=None)
    lines = [ln for ln in out.splitlines() if ln]
    assert any(t in lines for t in _LEFT_LINES), "left pane missing"
    assert any(t in lines for t in _RIGHT_LINES), "right pane missing"
    # column-major: all recognized left lines must precede all recognized right lines
    left_idxs = [i for i, ln in enumerate(lines) if ln in _LEFT_LINES]
    right_idxs = [i for i, ln in enumerate(lines) if ln in _RIGHT_LINES]
    if left_idxs and right_idxs:
        assert max(left_idxs) < min(right_idxs), "columns interleaved"


# ---------------------------------------------------------------------------
# Three-pane horizontal layout
# ---------------------------------------------------------------------------

_SIDEBAR_LINES = ["NAV HOME", "NAV INBOX", "NAV DRAFTS", "NAV ARCHIVE", "NAV STARRED", "NAV MUTED"]
_MAIN_LINES = ["MSG ALPHA", "MSG BETA", "MSG GAMMA", "MSG DELTA", "MSG EPSILON", "MSG ZETA"]
_THREAD_LINES = ["REPLY ALPHA", "REPLY BETA", "REPLY GAMMA", "REPLY DELTA", "REPLY EPSILON", "REPLY ZETA"]

_3H_IMG_W = 1800
_3H_IMG_H = 900
_3H_SIDEBAR_X = 50
_3H_MAIN_X = 600
_3H_THREAD_X = 1300
_3H_PANE_Y = 100
_3H_SIDEBAR_MID = (_3H_SIDEBAR_X + 400) / 2 / _3H_IMG_W   # ~0.12
_3H_MAIN_MID = (_3H_MAIN_X + 1100) / 2 / _3H_IMG_W        # ~0.47
_3H_THREAD_MID = (_3H_THREAD_X + 1750) / 2 / _3H_IMG_W    # ~0.85


def _three_pane_h_image():
    return _make_layout(
        [
            {"x": _3H_SIDEBAR_X, "y": _3H_PANE_Y, "lines": _SIDEBAR_LINES},
            {"x": _3H_MAIN_X, "y": _3H_PANE_Y, "lines": _MAIN_LINES},
            {"x": _3H_THREAD_X, "y": _3H_PANE_Y, "lines": _THREAD_LINES},
        ],
        _3H_IMG_W,
        _3H_IMG_H,
    )


@pytest.mark.parametrize(
    "select_x,keep,drop",
    [
        (_3H_SIDEBAR_MID, _SIDEBAR_LINES, _MAIN_LINES + _THREAD_LINES),
        (_3H_MAIN_MID, _MAIN_LINES, _SIDEBAR_LINES + _THREAD_LINES),
        (_3H_THREAD_MID, _THREAD_LINES, _SIDEBAR_LINES + _MAIN_LINES),
    ],
    ids=["sidebar", "main", "thread"],
)
def test_three_pane_h_cursor_select_each(ocr_binary, select_x, keep, drop):
    obs = _run_ocr(ocr_binary, _three_pane_h_image())
    assert obs, "OCR returned no observations"
    out = _reconstruct(obs, select_x=select_x)
    assert any(t in out for t in keep), f"expected pane content missing (select_x={select_x:.2f})"
    assert not any(t in out for t in drop), f"unexpected pane content present (select_x={select_x:.2f})"


def test_three_pane_h_no_interleave(ocr_binary):
    obs = _run_ocr(ocr_binary, _three_pane_h_image())
    assert obs, "OCR returned no observations"
    out = _reconstruct(obs, select_x=None)
    lines = [ln for ln in out.splitlines() if ln]

    for pane_lines, label in [
        (_SIDEBAR_LINES, "sidebar"),
        (_MAIN_LINES, "main"),
        (_THREAD_LINES, "thread"),
    ]:
        idxs = [i for i, ln in enumerate(lines) if ln in pane_lines]
        if len(idxs) < 2:
            continue
        assert max(idxs) - min(idxs) == len(idxs) - 1, f"{label} pane lines interleaved"

    # column-major: sidebar before main before thread
    sb = [i for i, ln in enumerate(lines) if ln in _SIDEBAR_LINES]
    mn = [i for i, ln in enumerate(lines) if ln in _MAIN_LINES]
    th = [i for i, ln in enumerate(lines) if ln in _THREAD_LINES]
    if sb and mn:
        assert max(sb) < min(mn), "sidebar must precede main"
    if mn and th:
        assert max(mn) < min(th), "main must precede thread"


# ---------------------------------------------------------------------------
# Two-pane vertical layout (top / bottom bands)
# ---------------------------------------------------------------------------

_TOP_LINES = ["TOP ROW ONE", "TOP ROW TWO", "TOP ROW THREE", "TOP ROW FOUR"]
_BOTTOM_LINES = ["BOTTOM ROW ONE", "BOTTOM ROW TWO", "BOTTOM ROW THREE", "BOTTOM ROW FOUR"]

_2V_IMG_W = 1200
_2V_IMG_H = 900
_2V_TOP_Y = 50
_2V_BOTTOM_Y = 540


def _two_pane_v_image():
    return _make_layout(
        [
            {"x": 100, "y": _2V_TOP_Y, "lines": _TOP_LINES},
            {"x": 100, "y": _2V_BOTTOM_Y, "lines": _BOTTOM_LINES},
        ],
        _2V_IMG_W,
        _2V_IMG_H,
    )


def test_two_pane_vertical_top_before_bottom(ocr_binary):
    obs = _run_ocr(ocr_binary, _two_pane_v_image())
    assert obs, "OCR returned no observations"
    out = _reconstruct(obs, select_x=None)
    lines = [ln for ln in out.splitlines() if ln]
    top_idxs = [i for i, ln in enumerate(lines) if ln in _TOP_LINES]
    bot_idxs = [i for i, ln in enumerate(lines) if ln in _BOTTOM_LINES]
    assert top_idxs, "top band content missing from output"
    assert bot_idxs, "bottom band content missing from output"
    assert max(top_idxs) < min(bot_idxs), "top band must appear before bottom band"


# ---------------------------------------------------------------------------
# Three-pane vertical layout (top / middle / bottom bands)
# ---------------------------------------------------------------------------

_TOP3_LINES = ["ZONE A ROW ONE", "ZONE A ROW TWO", "ZONE A ROW THREE"]
_MID3_LINES = ["ZONE B ROW ONE", "ZONE B ROW TWO", "ZONE B ROW THREE"]
_BOT3_LINES = ["ZONE C ROW ONE", "ZONE C ROW TWO", "ZONE C ROW THREE"]

_3V_IMG_W = 1200
_3V_IMG_H = 1050
_3V_TOP_Y = 50
_3V_MID_Y = 400
_3V_BOT_Y = 750


def _three_pane_v_image():
    return _make_layout(
        [
            {"x": 100, "y": _3V_TOP_Y, "lines": _TOP3_LINES},
            {"x": 100, "y": _3V_MID_Y, "lines": _MID3_LINES},
            {"x": 100, "y": _3V_BOT_Y, "lines": _BOT3_LINES},
        ],
        _3V_IMG_W,
        _3V_IMG_H,
    )


def test_three_pane_vertical_order(ocr_binary):
    obs = _run_ocr(ocr_binary, _three_pane_v_image())
    assert obs, "OCR returned no observations"
    out = _reconstruct(obs, select_x=None)
    lines = [ln for ln in out.splitlines() if ln]
    top_idxs = [i for i, ln in enumerate(lines) if ln in _TOP3_LINES]
    mid_idxs = [i for i, ln in enumerate(lines) if ln in _MID3_LINES]
    bot_idxs = [i for i, ln in enumerate(lines) if ln in _BOT3_LINES]
    assert top_idxs, "zone A content missing"
    assert mid_idxs, "zone B content missing"
    assert bot_idxs, "zone C content missing"
    assert max(top_idxs) < min(mid_idxs), "zone A must precede zone B"
    assert max(mid_idxs) < min(bot_idxs), "zone B must precede zone C"
