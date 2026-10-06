"""Reading videos.txt and normalizing URLs for history lookups."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

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


_ID_PARAMS = ("v", "id", "video_id", "videoid")
_QUERY_ID_RE = re.compile(r"[A-Za-z0-9_-]{5,}")
_PATH_ID_RE = re.compile(r"\d{5,}")


@lru_cache(maxsize=4096)
def video_ref(url: str) -> tuple[str, str] | None:
    """(host, video id) of a link, or None when no id can be told apart.

    The host ignores `www.` and the scheme. The id is a query value (`v=`, `id=`, `video_id=`) of at
    least 5 letters, digits, `-` or `_`, else the last path segment made only of 5 or more digits. The
    same id on two hosts is two different videos. Being conservative: no id means "use url_key".
    """
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").removeprefix("www.")
    if not host:
        return None
    query = dict(parse_qsl(parts.query))
    for name in _ID_PARAMS:
        value = query.get(name, "")
        if _QUERY_ID_RE.fullmatch(value):
            return host, value
    for segment in reversed(parts.path.split("/")):
        if _PATH_ID_RE.fullmatch(segment):
            return host, segment
    return None


def legacy_url_key(url: str) -> str:
    """The pre-0.2 key (whole query dropped, everything lowercased), kept to migrate old history files."""
    return url.split("#")[0].split("?")[0].rstrip("/").lower()


def read_playlist(path: Path) -> list[tuple[str, str | None]]:
    """The videos of videos.txt, each clip once. See `parse_playlist`."""
    return parse_playlist(path)[0]


def parse_playlist(path: Path) -> tuple[list[tuple[str, str | None]], int]:
    """Read one link per line, with or without a title on the line before it
    (the format produced by extensions that copy browser tabs).

    Returns ([(url, title)], number of duplicates dropped). A clip listed twice, even under two
    different links (same `video_ref`), is kept once, at its first place; it takes the title of a
    later line if the first one had none.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="cp1252")

    videos: list[tuple[str, str | None]] = []
    seen: dict[str | tuple[str, str], int] = {}
    duplicates = 0
    previous_title: str | None = None
    for line in text.splitlines():
        line = INVISIBLE_CHARS.sub("", line).strip()
        if not line or line.startswith("#"):
            continue
        match = _URL_RE.search(line)
        if match:
            url = match.group(0)
            title = line[: match.start()].strip() or previous_title
            cleaned = clean_title(title) if title else None
            keys: list[str | tuple[str, str]] = [url_key(url)]
            ref = video_ref(url)
            if ref:
                keys.append(ref)
            index = next((seen[k] for k in keys if k in seen), None)
            if index is None:
                for k in keys:
                    seen[k] = len(videos)
                videos.append((url, cleaned))
            else:
                duplicates += 1
                if videos[index][1] is None and cleaned:
                    videos[index] = (videos[index][0], cleaned)
            previous_title = None
        else:
            previous_title = line
    return videos, duplicates
