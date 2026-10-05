"""Small console-output helpers shared across the app."""

from __future__ import annotations

import math
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from vrec.logs import log_only

if TYPE_CHECKING:
    from vrec.statusbar import StatusBar


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

    def __init__(self, bar: StatusBar | None = None) -> None:
        self._width = 0
        self._bar = bar  # while it is active, the bar shows the progress and only the log gets the line

    def _quiet(self) -> bool:
        return self._bar is not None and self._bar.active

    def show(self, text: str) -> None:
        line = f"   {text}"
        if self._quiet():
            log_only(f"\r{line.ljust(self._width)}")
        else:
            print(f"\r{line.ljust(self._width)}", end="", flush=True)
        self._width = max(self._width, len(line))

    def end(self) -> None:
        if self._width:
            if self._quiet():
                log_only("\n")
            else:
                print()
            self._width = 0


def console_process_count() -> int | None:
    """How many processes are attached to this console (Windows only), or None if unknown."""
    if sys.platform != "win32":
        return None
    import ctypes

    pids = (ctypes.c_ulong * 4)()
    count = int(ctypes.windll.kernel32.GetConsoleProcessList(pids, len(pids)))
    return count or None  # 0 means the call failed (no console)


def opened_by_double_click(
    frozen: bool | None = None, count: int | None = None, interactive: bool | None = None
) -> bool:
    """True when vrec.exe was started by a double-click, so its console closes with it.

    Windows creates a console for the program alone in that case: it is then the only process attached
    to it. Only the packaged exe asks (from a terminal or a script the count is higher), and only with
    a keyboard to answer (a scheduled run has none). The arguments exist for tests.
    """
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if not frozen:
        return False
    if interactive is None:
        interactive = sys.stdin is not None and sys.stdin.isatty()
    if not interactive:
        return False
    if count is None:
        count = console_process_count()
    return count == 1


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
