"""Filename cleanup, renaming, and detecting already-recorded videos."""

from __future__ import annotations

import re
import time
from pathlib import Path

# Zero-width and bidi-control characters that sometimes sneak into copy-pasted titles/URLs.
INVISIBLE_CHARS = re.compile("[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")

# ASCII control characters (replaced by a space, then whitespace is collapsed).
_CONTROL_CHARS = re.compile("[\x00-\x1f\x7f]")

# Windows device names: a file called like this (even with an extension) can't be created.
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"} | {f"COM{n}" for n in range(1, 10)} | {f"LPT{n}" for n in range(1, 10)}
)

# File name prefixes used by recorder.record_one().
TEST_PREFIX = "TEST"
FAILED_BLACK_PREFIX = "FAILED black image"
INCOMPLETE_PREFIX = "INCOMPLETE"
INTERRUPTED_PREFIX = "INTERRUPTED"  # recordings cut short by Ctrl+C or an error (lot 4)

# Legacy (French) failure marker used by recordings made before this rewrite.
_LEGACY_FAILED_MARKER = "ECHEC"

# Stems starting with any of these are never a genuine completed recording.
_EXCLUDED_PREFIXES = (TEST_PREFIX, INCOMPLETE_PREFIX, INTERRUPTED_PREFIX)


def clean_title(text: str | None) -> str:
    """Turn a page title into a filesystem-safe recording name."""
    text = INVISIBLE_CHARS.sub("", text or "")
    text = re.sub(r"\s*\|\s*[^|]{1,20}$", "", text)  # strip a trailing " | site name"
    text = _CONTROL_CHARS.sub(" ", text)
    text = re.sub(r'[\\/:*?"<>|]', "", text)
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    text = text[:100] or "video"
    stem, dot, rest = text.partition(".")
    if stem.strip().upper() in _RESERVED_NAMES:
        text = stem + "_" + dot + rest
    return text


def find_existing_recording(folder: str | Path | None, title: str | None) -> Path | None:
    """Look for a file already recorded for this title in the OBS output folder."""
    if not folder or not title:
        return None
    try:
        for f in Path(folder).iterdir():
            if (
                f.is_file()
                and (f.stem == title or f.stem.endswith(" - " + title))
                and not f.stem.startswith(_EXCLUDED_PREFIXES)
                and "FAILED" not in f.stem
                and _LEGACY_FAILED_MARKER not in f.stem
            ):
                return f
    except OSError:
        pass
    return None


def rename_recording(path: str | Path, new_stem: str) -> Path:
    """Rename a freshly recorded file, avoiding collisions. Retries briefly if OBS still holds it."""
    path = Path(path)
    target = path.with_name(new_stem + path.suffix)
    n = 2
    while target.exists():
        target = path.with_name(f"{new_stem} ({n}){path.suffix}")
        n += 1
    for _ in range(30):  # OBS sometimes keeps the file open for a few seconds
        try:
            path.rename(target)
            return target
        except OSError:
            time.sleep(1)
    return path
