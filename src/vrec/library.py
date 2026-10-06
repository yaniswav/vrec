"""Finding videos you already have on disk, wherever you moved them.

The OBS folder is not the only place a finished recording ends up: people sort them into subfolders
or copy them to a library. `scan` reads those folders once, and `Library.find` matches a video title
against the file names, ignoring accents, case, punctuation and the suffixes added by converters.
"""

from __future__ import annotations

import difflib
import os
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from vrec.naming import ECHEC_MARKER, EXCLUDED_PREFIXES, clean_title

VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts", ".flv"})

# A scan never reads more than this many video files: a library on a huge drive stays quick.
MAX_FILES = 100_000

# Folders that are never worth walking into.
_SKIPPED_DIRS = frozenset({"$recycle.bin", "system volume information"})

# Fuzzy matches must be very close, and long enough that a short title can't match by chance.
FUZZY_RATIO = 0.92
FUZZY_MIN_LENGTH = 12
FUZZY_LENGTH_RATIO = 0.9

# Added by converters after the title: `_360`, `_3D_360_TB`, `_180`, `_SBS`, ` (2)`...
_SUFFIX_RE = re.compile(r"(?:[ _-]+(?:3D|360|180|SBS|TB|LR|OU)|\s*\(\d+\))+$", re.IGNORECASE)

# Quotes and apostrophes that look alike but aren't the same character.
_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"'})

_VREC_PREFIX_RE = re.compile(r"(?:" + "|".join(EXCLUDED_PREFIXES) + r")(?: - |$)")


def normalize(text: str) -> str:
    """Lowercase letters and digits only, accents removed, single spaces: "Café, l'été!" -> "cafe lete"."""
    text = unicodedata.normalize("NFKD", text.translate(_QUOTES)).replace("_", " ")
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = "".join(c for c in text if c.isalnum() or c.isspace())
    return re.sub(r"\s+", " ", text).strip()


def is_excluded(stem: str) -> bool:
    """A file vrec itself marks as not a finished recording (TEST, INCOMPLETE, FAILED...)."""
    return bool(_VREC_PREFIX_RE.match(stem)) or "FAILED" in stem or ECHEC_MARKER in stem


def stem_variants(stem: str) -> tuple[str, ...]:
    """The normalized forms a file name can be compared under: as is, without converter suffixes, and
    without a "<something> - " prefix (a number, or any label vrec once put before the title)."""
    stems = {stem, _SUFFIX_RE.sub("", stem)}
    stems |= {s.split(" - ", 1)[1] for s in stems if " - " in s}
    return tuple(dict.fromkeys(n for n in map(normalize, stems) if n))


def title_variants(title: str) -> tuple[str, ...]:
    """The normalized forms of a title: as is, and cut to the 100 characters of a recording's name."""
    return tuple(dict.fromkeys(n for n in (normalize(title), normalize(clean_title(title))) if n))


@dataclass(frozen=True)
class Match:
    """A file that looks like a video's recording. `exact`: same name once normalized."""

    path: Path
    exact: bool


@dataclass
class Library:
    """Every video file found, indexed by normalized name."""

    exact: dict[str, Path] = field(default_factory=dict)
    fuzzy: list[tuple[str, Path]] = field(default_factory=list)

    def __len__(self) -> int:
        return len({path for path in self.exact.values()})

    def add(self, path: Path) -> None:
        if is_excluded(path.stem):
            return
        for variant in stem_variants(path.stem):
            self.exact.setdefault(variant, path)
            self.fuzzy.append((variant, path))

    def find(self, title: str | None) -> Match | None:
        """The file that most likely is this title's recording, an exact name match first."""
        if not title:
            return None
        variants = title_variants(title)
        for variant in variants:
            if variant in self.exact:
                return Match(self.exact[variant], True)
        best: tuple[float, Path] | None = None
        for variant in variants:
            if len(variant) < FUZZY_MIN_LENGTH:
                continue
            matcher = difflib.SequenceMatcher(None, "", variant, autojunk=False)
            for name, path in self.fuzzy:
                short, long_ = sorted((len(name), len(variant)))
                if short < FUZZY_MIN_LENGTH or short / long_ < FUZZY_LENGTH_RATIO:
                    continue
                matcher.set_seq1(name)
                if (
                    matcher.real_quick_ratio() >= FUZZY_RATIO
                    and matcher.quick_ratio() >= FUZZY_RATIO
                    and (ratio := matcher.ratio()) >= FUZZY_RATIO
                    and (best is None or ratio > best[0])
                ):
                    best = (ratio, path)
        return Match(best[1], False) if best else None


def scan(folders: Iterable[str | Path | None]) -> Library:
    """Index the video files under these folders (recursively). Missing or unreadable ones are skipped."""
    library = Library()
    count = 0
    seen: set[Path] = set()
    for folder in folders:
        if not folder:
            continue
        root = Path(folder).expanduser()
        for directory, subdirs, names in os.walk(root, onerror=lambda error: None):
            subdirs[:] = sorted(d for d in subdirs if d.lower() not in _SKIPPED_DIRS)
            for name in sorted(names):
                path = Path(directory, name)
                if path.suffix.lower() not in VIDEO_EXTENSIONS or path in seen:
                    continue
                seen.add(path)
                library.add(path)
                count += 1
                if count >= MAX_FILES:
                    return library
    return library
