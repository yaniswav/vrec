"""Reading videos.txt and normalizing URLs for history lookups."""

from __future__ import annotations

import re
from pathlib import Path

from vrec.naming import INVISIBLE_CHARS, clean_title

_URL_RE = re.compile(r"https?://\S+")


def url_key(url: str) -> str:
    """Normalize a URL so the same video is recognized regardless of query string or trailing slash."""
    return url.split("#")[0].split("?")[0].rstrip("/").lower()


def read_playlist(path: Path) -> list[tuple[str, str | None]]:
    """Read one link per line, with or without a title on the line before it
    (the format produced by extensions that copy browser tabs). Returns [(url, title)]."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="cp1252")

    videos: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    previous_title: str | None = None
    for line in text.splitlines():
        line = INVISIBLE_CHARS.sub("", line).strip()
        if not line or line.startswith("#"):
            continue
        match = _URL_RE.search(line)
        if match:
            title = line[: match.start()].strip() or previous_title
            if match.group(0) not in seen:
                seen.add(match.group(0))
                videos.append((match.group(0), clean_title(title) if title else None))
            previous_title = None
        else:
            previous_title = line
    return videos
