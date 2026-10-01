"""Reading videos.txt and normalizing URLs for history lookups."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from vrec.naming import INVISIBLE_CHARS, clean_title

_URL_RE = re.compile(r"https?://\S+")


_TRACKING_PARAMS = frozenset({"fbclid", "gclid", "ref", "ref_src", "si", "feature"})


def _is_tracking(param: str) -> bool:
    name = param.split("=", 1)[0].lower()
    return name.startswith("utm_") or name in _TRACKING_PARAMS


def url_key(url: str) -> str:
    """Normalize a URL so the same video is recognized across share-link noise.

    Scheme and host are lowercased, the path keeps its case, the fragment and a trailing slash are
    dropped, and the query keeps its parameters (so `watch?v=A` and `watch?v=B` stay distinct)
    except tracking ones (utm_*, fbclid, gclid, ref, ref_src, si, feature), sorted for stability.
    """
    parts = urlsplit(url.strip())
    params = sorted(p for p in parts.query.split("&") if p and not _is_tracking(p))
    key = f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/')}"
    return f"{key}?{'&'.join(params)}" if params else key


def legacy_url_key(url: str) -> str:
    """The pre-0.2 key (whole query dropped, everything lowercased), kept to migrate old history files."""
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
            key = url_key(match.group(0))
            if key not in seen:
                seen.add(key)
                videos.append((match.group(0), clean_title(title) if title else None))
            previous_title = None
        else:
            previous_title = line
    return videos
