"""Small console-output helpers shared across the app."""

from __future__ import annotations

import math
import re


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
