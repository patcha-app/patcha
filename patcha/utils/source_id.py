import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse, urlencode, parse_qsl

_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "msclkid",
    "ref",
    "source",
    "_hsenc",
    "_hsmi",
}


def derive_source_doc_id(raw: str) -> Optional[str]:
    """
    Return a stable source_doc_id for mutable documents, or None for
    immutable events (browser visits at a point in time, git commits, etc.).

    Callers should set event.source_doc_id only when they intend to track
    the *current state* of a document that will be re-fetched over time.
    """
    s = raw.strip()

    # Google Docs
    m = re.search(r"/document/d/([A-Za-z0-9_-]+)", s)
    if m:
        return f"gdoc:{m.group(1)}"

    # Google Sheets
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", s)
    if m:
        return f"gsheet:{m.group(1)}"

    # Google Slides
    m = re.search(r"/presentation/d/([A-Za-z0-9_-]+)", s)
    if m:
        return f"gslides:{m.group(1)}"

    # Notion — page ID is the 32-char hex suffix after the last '-' in the slug.
    # Handles both notion.so/{slug-ID} and notion.so/{workspace}/{slug-ID}.
    m = re.search(r"notion\.so/(?:[^/?#]+/)?[^/?#]*-([0-9a-f]{32})(?:[?#]|$)", s)
    if m:
        return f"notion:{m.group(1)}"

    # GitHub file at HEAD (not a commit permalink — those are immutable)
    # e.g. https://github.com/owner/repo/blob/main/path/to/file.md
    m = re.match(r"https://github\.com/([^/]+/[^/]+)/blob/[^/]+/(.+?)(?:\?.*)?$", s)
    if m:
        return f"github:{m.group(1)}/{m.group(2)}"

    # Local file path
    if s.startswith("/") or s.startswith("~"):
        path = str(Path(s).expanduser().resolve())
        return f"file:{path}"

    # Generic URL — normalize by stripping fragment and known tracking params,
    # then use the cleaned URL as the identity.
    parsed = urlparse(s)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        clean_qs = urlencode(
            [
                (k, v)
                for k, v in parse_qsl(parsed.query)
                if k.lower() not in _TRACKING_PARAMS
            ]
        )
        normalized = urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path.rstrip("/") or "/",
                "",  # params
                clean_qs,
                "",  # fragment stripped
            )
        )
        return f"url:{normalized}"

    return None
