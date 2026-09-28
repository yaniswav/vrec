"""Recording history: what has already been recorded, reviewed, or still needs doing.

history.json keeps track of every video (by its URL) even if you later move or delete
the recorded file (for example after copying it to another device).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, TypedDict, cast

from vrec.naming import find_existing_recording
from vrec.playlist import url_key

STATUS_DONE = "done"
STATUS_MARKED = "marked"
STATUS_REVIEW = "review"
STATUS_FAILED = "failed"
STATUS_NEW = "new"

# Statuses that mean "still needs recording".
TODO = (None, STATUS_FAILED, STATUS_NEW)

_LABELS = {
    STATUS_DONE: "DONE",
    STATUS_MARKED: "DONE",
    STATUS_REVIEW: "REVIEW",
    STATUS_FAILED: "FAILED",
    STATUS_NEW: "NEW",
    None: "NEW",
}

# Legacy (French, pre-v1) status and detail values, translated on migration.
_LEGACY_STATUS = {
    "fait": STATUS_DONE,
    "marquee": STATUS_MARKED,
    "a_revoir": STATUS_REVIEW,
    "echec": STATUS_FAILED,
    "nouvelle": STATUS_NEW,
}
_LEGACY_DETAIL = {
    "fichier retrouvé": "found on disk",
    "marquée à la main": "marked by hand",
    "remise à zéro": "reset",
    "chargement bloqué": "loading stalled",
}


class VideoRecord(TypedDict):
    """One video's entry in history.json, as written by `record()`/`_migrate_legacy()`."""

    url: str
    title: str
    status: str | None
    detail: str
    file: str
    quality: str
    date: str


Videos = dict[str, VideoRecord]


def label(status: str | None) -> str:
    """Menu label for a status: DONE, REVIEW, FAILED, or NEW."""
    return _LABELS.get(status, "NEW")


def load(path: Path) -> Videos:
    """Load the history file, migrating the legacy (pre-v1) format if needed."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        broken = path.with_name(path.stem + ".unreadable" + path.suffix)
        path.replace(broken)
        print(f"{path.name} was unreadable: set aside as {broken.name}.")
        return {}

    if isinstance(raw, dict) and raw.get("version") == 1:
        # Our own previously-saved file (see `save()`), not externally validated, so it is trusted
        # to match the Videos shape.
        return cast(Videos, raw.get("videos", {}))

    videos = _migrate_legacy(raw if isinstance(raw, dict) else {})
    save(path, videos)
    return videos


def _migrate_legacy(raw: dict[str, Any]) -> Videos:
    videos: Videos = {}
    for key, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        etat = entry.get("etat")
        status: str | None = _LEGACY_STATUS.get(etat, etat) if isinstance(etat, str) else etat
        detail_raw = entry.get("detail", "")
        detail = _LEGACY_DETAIL.get(detail_raw, detail_raw) if isinstance(detail_raw, str) else detail_raw
        videos[key] = {
            "url": entry.get("lien", ""),
            "title": entry.get("titre", ""),
            "status": status,
            "detail": detail,
            "file": entry.get("fichier", ""),
            "quality": entry.get("qualite", ""),
            "date": entry.get("date", ""),
        }
    return videos


def save(path: Path, videos: Videos) -> None:
    """Write the history atomically (write to a temp file, then replace)."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"version": 1, "videos": videos}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)


def record(
    path: Path,
    videos: Videos,
    url: str,
    title: str,
    status: str,
    detail: str = "",
    file: Path | str | None = None,
    quality: str = "",
) -> None:
    """Update one video's entry in-place and save immediately."""
    videos[url_key(url)] = {
        "url": url,
        "title": title,
        "status": status,
        "detail": detail,
        "file": str(file) if file else "",
        "quality": quality,
        "date": time.strftime("%Y-%m-%d %H:%M"),
    }
    save(path, videos)


def status_of(videos: Videos, url: str) -> str | None:
    known = videos.get(url_key(url))
    return known["status"] if known else None


def display_title(videos: Videos, url: str, title: str | None) -> str:
    """Best title to show for a video: the given one, else the last known one, else derived from the URL."""
    if title:
        return title
    known = videos.get(url_key(url))
    if known and known.get("title"):
        return known["title"]
    return url_key(url).rsplit("/", 1)[-1].replace("-", " ").strip() or url


def adopt_existing_files(
    videos_list: list[tuple[str, str | None]],
    videos: Videos,
    folder: str | Path | None,
    path: Path,
) -> None:
    """Videos recorded before the history existed: find them back by their file name."""
    found = 0
    for url, title in videos_list:
        if url_key(url) not in videos and title:
            file = find_existing_recording(folder, title)
            if file:
                record(path, videos, url, title, STATUS_DONE, "found on disk", file)
                found += 1
    if found:
        print(f"{found} already-recorded video(s) found in your OBS folder.")
