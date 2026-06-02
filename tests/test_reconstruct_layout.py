from patcha.collectors.accessibility import AccessibilityCollector

# Real OCR observations captured from a Slack thread panel (normalized coords, bottom-left origin
# from Apple Vision — higher y = higher on screen).
SLACK_THREAD = [
    {"text": "Anchal Sethi VIP", "x": 0.642, "y": 0.806, "w": 0.0625, "h": 0.0135},
    {"text": "Today at 10:54 AM", "x": 0.709, "y": 0.804, "w": 0.061, "h": 0.0135},
    {"text": "@Apurv", "x": 0.644, "y": 0.783, "w": 0.032, "h": 0.0139},
    {
        "text": "There are 59,552.828132 units available. When the agent attempts a full",
        "x": 0.642,
        "y": 0.763,
        "w": 0.282,
        "h": 0.0158,
    },
    {
        "text": "redemption, the API returns a 400 error. However, the redemption succeeds",
        "x": 0.641,
        "y": 0.743,
        "w": 0.297,
        "h": 0.0157,
    },
    {
        "text": "when the unit quantity is rounded to 3 decimal places.",
        "x": 0.641,
        "y": 0.725,
        "w": 0.211,
        "h": 0.0136,
    },
    {
        "text": "• Are redemptions supported only up to 3 decimal places?",
        "x": 0.645,
        "y": 0.705,
        "w": 0.233,
        "h": 0.0134,
    },
    {"text": "1 reply", "x": 0.616, "y": 0.652, "w": 0.025, "h": 0.0134},
    {"text": "Apurv VIP", "x": 0.642, "y": 0.629, "w": 0.038, "h": 0.017},
    {"text": "Yes", "x": 0.642, "y": 0.609, "w": 0.0145, "h": 0.0134},
]


def _reconstruct(obs):
    return AccessibilityCollector._reconstruct_layout(obs)


def test_no_noisy_separators():
    out = _reconstruct(SLACK_THREAD)
    assert "===" not in out
    assert "---" not in out


def test_name_and_timestamp_merge_on_one_line():
    lines = _reconstruct(SLACK_THREAD).splitlines()
    assert lines[0] == "Anchal Sethi VIP Today at 10:54 AM"


def test_body_lines_separate_and_in_order():
    out = _reconstruct(SLACK_THREAD)
    body = [
        "There are 59,552.828132 units available. When the agent attempts a full",
        "redemption, the API returns a 400 error. However, the redemption succeeds",
        "when the unit quantity is rounded to 3 decimal places.",
    ]
    # Each body line is its own line, and they appear in top-to-bottom order.
    idxs = [out.splitlines().index(b) for b in body]
    assert idxs == sorted(idxs)
    for b in body:
        assert b in out.splitlines()


def test_blank_line_before_reply_block():
    lines = _reconstruct(SLACK_THREAD).splitlines()
    reply_idx = lines.index("1 reply")
    # The large vertical jump from the question to "1 reply" inserts exactly one blank line.
    assert lines[reply_idx - 1] == ""
    assert lines[reply_idx - 2] != ""


def test_zoom_invariance():
    """Scaling every coordinate (simulating zoom) must not change the line structure."""
    base = _reconstruct(SLACK_THREAD)
    for scale in (0.5, 1.5, 2.0):
        zoomed = [
            {k: (v * scale if k in ("x", "y", "w", "h") else v) for k, v in o.items()}
            for o in SLACK_THREAD
        ]
        assert _reconstruct(zoomed) == base, f"layout changed at scale={scale}"


def test_empty_and_single():
    assert _reconstruct([]) == ""
    single = [{"text": "hello", "x": 0.1, "y": 0.5, "w": 0.05, "h": 0.0}]
    assert _reconstruct(single) == "hello"


# Synthetic 3-pane layout (sidebar / messages / thread), all sharing the same y-bands — the case
# that previously interleaved. Each pane has enough rows for the gutter projection to fire.
def _three_columns():
    obs = []
    h = 0.018
    # left sidebar: x ~0.02-0.18
    for i, t in enumerate(
        ["Threads", "Huddles", "Home", "# general", "# alerts", "# dev"]
    ):
        obs.append({"text": t, "x": 0.02, "y": 0.90 - i * 0.05, "w": 0.10, "h": h})
    # message column: x ~0.30-0.60
    for i, t in enumerate(
        [
            "proj-treasury-dev",
            "Apurv 5:27 PM",
            "use prod konsole",
            "Dinesh 5:32 PM",
            "check on priority",
            "remove one disclaimer",
        ]
    ):
        obs.append({"text": t, "x": 0.30, "y": 0.90 - i * 0.05, "w": 0.28, "h": h})
    # thread panel: x ~0.66-0.95
    for i, t in enumerate(
        [
            "Thread",
            "Anchal Sethi",
            "There are units available",
            "400 error",
            "Apurv",
            "Yes",
        ]
    ):
        obs.append({"text": t, "x": 0.66, "y": 0.90 - i * 0.05, "w": 0.27, "h": h})
    return obs


def test_columns_do_not_interleave():
    out = _reconstruct(_three_columns())
    lines = [ln for ln in out.splitlines() if ln]
    # Each pane's items must appear as a contiguous run (column-major), not interleaved by row.
    sidebar = ["Threads", "Huddles", "Home", "# general", "# alerts", "# dev"]
    messages = [
        "proj-treasury-dev",
        "Apurv 5:27 PM",
        "use prod konsole",
        "Dinesh 5:32 PM",
        "check on priority",
        "remove one disclaimer",
    ]
    thread = [
        "Thread",
        "Anchal Sethi",
        "There are units available",
        "400 error",
        "Apurv",
        "Yes",
    ]
    for pane in (sidebar, messages, thread):
        idxs = [lines.index(t) for t in pane]
        assert idxs == sorted(idxs), f"pane out of order: {pane}"
        # contiguous: max index - min index == len-1 means no other pane's lines interleave
        assert max(idxs) - min(idxs) == len(pane) - 1, f"pane interleaved: {pane}"
    # A row-interleaved (broken) output would have put 'Threads' and 'proj-treasury-dev' adjacent.
    assert abs(lines.index("Threads") - lines.index("proj-treasury-dev")) > 1
