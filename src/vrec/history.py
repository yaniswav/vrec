"""Recording history: what has already been recorded, reviewed, or still needs doing.

history.json keeps track of every video (by its URL) even if you later move or delete
the recorded file (for example after copying it to another device).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict, cast
from urllib.parse import urlsplit

from vrec.naming import find_existing_recording
from vrec.playlist import legacy_url_key, url_key, video_ref

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
        return _set_aside(path)

    if not isinstance(raw, dict):
        return _set_aside(path)
    if "version" in raw:
        videos = raw.get("videos")
        if raw["version"] != 1 or not isinstance(videos, dict):
            # A newer (or damaged) file: never overwrite or migrate it.
            return _set_aside(path)
        # Our own previously-saved file (see `save()`); drop anything that isn't an entry.
        kept = cast(Videos, {k: v for k, v in videos.items() if isinstance(v, dict)})
        if _rekey_legacy(kept):
            save(path, kept)
        return kept

    videos = _migrate_legacy(raw)
    _rekey_legacy(videos)
    save(path, videos)
    return videos


def _set_aside(path: Path) -> Videos:
    """Rename a history file vrec can't use to history.unreadable.json and start empty."""
    broken = path.with_name(path.stem + ".unreadable" + path.suffix)
    path.replace(broken)
    print(f"{path.name} was unreadable: set aside as {broken.name}.")
    return {}


def _rekey_legacy(videos: Videos) -> bool:
    """Move entries stored under an old-format key (see `legacy_url_key`) to the current `url_key`.

    The entry's own `url` tells us what it was recorded for. Returns True if anything moved.
    An entry already present under the new key wins.
    """
    changed = False
    for key in list(videos):
        entry = videos[key]
        url = entry.get("url", "") if isinstance(entry, dict) else ""
        if not url or key != legacy_url_key(url):
            continue
        new_key = url_key(url)
        if new_key != key:
            del videos[key]
            videos.setdefault(new_key, entry)
            changed = True
    return changed


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


def save(path: Path, videos: Videos, sleep: Callable[[float], None] = time.sleep, attempts: int = 5) -> None:
    """Write the history atomically (write to a temp file, then replace).

    The replace is retried a few times: an antivirus or an editor can hold the file for a moment.
    """
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"version": 1, "videos": videos}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for attempt in range(attempts):
        try:
            tmp.replace(path)
            return
        except OSError:
            if attempt == attempts - 1:
                raise
            sleep(0.2)


def resolve_key(videos: Videos, url: str) -> str | None:
    """The key of this video's entry, or None if it has none.

    The exact `url_key` comes first. Otherwise an entry recorded under another link of the same clip
    (same host and same video id, see `video_ref`) counts. Existing keys are never rewritten.
    """
    key = url_key(url)
    if key in videos:
        return key
    ref = video_ref(url)
    if ref is None:
        return None
    for other, entry in videos.items():
        entry_url = entry.get("url") if isinstance(entry, dict) else None
        if isinstance(entry_url, str) and video_ref(entry_url) == ref:
            return other
    return None


def entry_of(videos: Videos, url: str) -> VideoRecord | None:
    """This video's entry (recorded under this link or another link of the same clip), if any."""
    key = resolve_key(videos, url)
    known = videos.get(key) if key is not None else None
    return known if isinstance(known, dict) else None


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
    videos[resolve_key(videos, url) or url_key(url)] = {
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
    known = entry_of(videos, url)
    if known is None:
        return None
    status = known.get("status")
    return status if isinstance(status, str) else None


def display_title(videos: Videos, url: str, title: str | None) -> str:
    """Best title to show for a video: the given one, else the last known one, else derived from the URL."""
    if title:
        return title
    known = entry_of(videos, url)
    if known is not None and isinstance(known.get("title"), str) and known["title"]:
        return known["title"]
    return urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").strip() or url


def adopt_existing_files(
    videos_list: list[tuple[str, str | None]],
    videos: Videos,
    folder: str | Path | None,
    path: Path,
) -> None:
    """Videos recorded before the history existed: find them back by their file name."""
    found = 0
    for url, title in videos_list:
        if resolve_key(videos, url) is None and title:
            file = find_existing_recording(folder, title)
            if file:
                record(path, videos, url, title, STATUS_DONE, "found on disk", file)
                found += 1
    if found:
        print(f"{found} already-recorded video(s) found in your OBS folder.")
