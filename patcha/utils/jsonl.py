"""Helpers for bounded append-only JSONL logs.

Several collectors append a line per poll to a .jsonl file and later read it
back filtered by timestamp. Without trimming these grow without bound (disk and,
because the whole file is re-read each cycle, memory/CPU). `trim_jsonl` keeps
only the most recent `max_lines` lines via an atomic replace.
"""

from pathlib import Path


def trim_jsonl(path: Path, max_lines: int) -> int:
    """Truncate `path` to its last `max_lines` lines. Returns the kept count."""
    try:
        with open(path, "rb") as f:
            lines = f.readlines()
    except OSError:
        return 0
    if len(lines) <= max_lines:
        return len(lines)
    keep = lines[-max_lines:]
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "wb") as f:
            f.writelines(keep)
        tmp.replace(path)
    except OSError:
        return len(lines)
    return len(keep)
