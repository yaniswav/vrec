"""Small console-output helpers shared across the app."""

from __future__ import annotations

import math
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager


def human_duration(seconds: float | None) -> str:
    """Format a duration in seconds as "H:MM:SS" or "M:SS". Returns "?" if unknown."""
    if not seconds or not math.isfinite(seconds):
        return "?"
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def first_line(error: BaseException) -> str:
    """The first line of an exception's message, with common noisy prefixes stripped."""
    lines = str(error).strip().splitlines()
    text = lines[0] if lines else type(error).__name__
    return re.sub(r"^([\w.]+: )?(Error: )?", "", text)


def warn(message: str) -> None:
    """Print a short, friendly warning without stopping the program."""
    print(f"\n   ! {message}")


class ProgressLine:
    """A status line rewritten in place (with a carriage return) until `end()` is called."""

    def __init__(self) -> None:
        self._width = 0

    def show(self, text: str) -> None:
        line = f"   {text}"
        print(f"\r{line.ljust(self._width)}", end="", flush=True)
        self._width = max(self._width, len(line))

    def end(self) -> None:
        if self._width:
            print()
            self._width = 0


# ---------- Windows console: a click must not freeze vrec ----------

_ENABLE_QUICK_EDIT_MODE = 0x0040
_ENABLE_EXTENDED_FLAGS = 0x0080
_STD_INPUT_HANDLE = -10


@contextmanager
def no_quick_edit() -> Iterator[bool]:
    """Turn off the console's QuickEdit mode for the duration of the block (Windows only).

    With QuickEdit on, a single click in the console window selects text and suspends the program
    at its next output, while OBS keeps recording. Yields whether the mode was changed; the original
    mode is always restored.
    """
    if sys.platform != "win32":
        yield False
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(_STD_INPUT_HANDLE)
    mode = wintypes.DWORD()
    if not handle or not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        yield False  # not a console (redirected input, IDE, tests)
        return
    original = mode.value
    changed = bool(
        kernel32.SetConsoleMode(handle, (original & ~_ENABLE_QUICK_EDIT_MODE) | _ENABLE_EXTENDED_FLAGS)
    )
    try:
        yield changed
    finally:
        if changed:
            kernel32.SetConsoleMode(handle, original)
