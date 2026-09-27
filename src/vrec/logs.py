"""Per-run log files.

When the `run_logs` feature is on, every run writes `data/logs/vrec-YYYYMMDD-HHMMSS.log`:
a mirror of everything printed to the console, with in-place progress lines (the ones
using `\\r`) collapsed to just their final state, plus a short header and every input()
prompt and its answer -- with anything that looks like a password masked.
"""

from __future__ import annotations

import builtins
import contextlib
import platform
import re
import sys
import time
import traceback
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TextIO

from vrec import __version__
from vrec.features import FeatureSet

LOG_DIR_NAME = "logs"
MAX_LOG_FILES = 20

_SPLIT_RE = re.compile(r"([\r\n])")


class _Tee:
    """Mirrors writes to a real stream unchanged, and separately to a line callback.

    `\\r` resets the current (not-yet-terminated) line instead of appending to it, so only
    the text after the last `\\r` before a `\\n` is ever handed to the callback -- exactly what
    an in-place progress line looks like once it stops updating.
    """

    def __init__(self, stream: TextIO, on_line: Callable[[str], None]) -> None:
        self._stream = stream
        self._on_line = on_line
        self._pending = ""

    def write(self, s: str) -> int:
        n = self._stream.write(s)
        for token in _SPLIT_RE.split(s):
            if not token:
                continue
            if token == "\r":
                self._pending = ""
            elif token == "\n":
                self._on_line(self._pending + "\n")
                self._pending = ""
            else:
                self._pending += token
        return n

    def note(self, text: str) -> None:
        """Append text to the log only (not the console) and close the current line."""
        self._on_line(self._pending + text + "\n")
        self._pending = ""

    def flush(self) -> None:
        self._stream.flush()

    def __getattr__(self, name: str) -> object:
        return getattr(self._stream, name)


class RunLog:
    """Handle for the active per-run log file, or a no-op stand-in when logging is disabled."""

    def __init__(self, path: Path | None = None, file: TextIO | None = None) -> None:
        self.path = path
        self._file = file

    def log_exception(self, exc: BaseException) -> None:
        """Write a full traceback to the log file only -- it never reaches the console."""
        if self._file is None:
            return
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self._file.write("\n" + text)
        self._file.flush()


def _is_password_prompt(prompt: str) -> bool:
    return "password" in prompt.lower()


def _header(argv: list[str], features: FeatureSet) -> list[str]:
    off = features.disabled()
    return [
        f"vrec {__version__}",
        f"Python {platform.python_version()}",
        platform.platform(),
        "Args: " + " ".join(argv),
        "Features off: " + (", ".join(off) if off else "(none)"),
        "-" * 60,
    ]


def _rotate(log_dir: Path, keep: int) -> None:
    """Delete all but the `keep` most recent vrec-*.log files (names sort chronologically)."""
    existing = sorted(log_dir.glob("vrec-*.log"))
    for old in existing[: max(0, len(existing) - keep)]:
        with contextlib.suppress(OSError):
            old.unlink()


@contextlib.contextmanager
def capture(data_dir: Path, argv: list[str], features: FeatureSet) -> Iterator[RunLog]:
    """Tee stdout/stderr (and input()) into a per-run log file, if `run_logs` is enabled.

    Yields a RunLog: a real one (with a live file) when the feature is on, otherwise a
    no-op stand-in, so callers don't need to branch on whether logging is active.
    """
    if not features.enabled("run_logs"):
        yield RunLog()
        return

    log_dir = data_dir / LOG_DIR_NAME
    log_dir.mkdir(parents=True, exist_ok=True)
    _rotate(log_dir, MAX_LOG_FILES - 1)
    path = log_dir / f"vrec-{time.strftime('%Y%m%d-%H%M%S')}.log"

    with path.open("w", encoding="utf-8", newline="\n") as file:
        for line in _header(argv, features):
            file.write(line + "\n")
        file.flush()

        def write_line(line: str) -> None:
            file.write(line)
            file.flush()

        orig_stdout, orig_stderr, orig_input = sys.stdout, sys.stderr, builtins.input
        stdout_tee = _Tee(orig_stdout, write_line)
        stderr_tee = _Tee(orig_stderr, write_line)

        def logged_input(prompt: str = "") -> str:
            # The prompt is written here (once, so it's logged) rather than passed to
            # orig_input(): builtins.input() only writes it itself when sys.stdout is
            # still the *original* stdout object, which by now it no longer is.
            stdout_tee.write(prompt)
            try:
                answer = orig_input()
            except EOFError:
                stdout_tee.note("<EOF>")
                raise
            stdout_tee.note("***" if _is_password_prompt(prompt) else answer)
            return answer

        sys.stdout, sys.stderr, builtins.input = stdout_tee, stderr_tee, logged_input
        try:
            yield RunLog(path=path, file=file)
        finally:
            sys.stdout, sys.stderr, builtins.input = orig_stdout, orig_stderr, orig_input
