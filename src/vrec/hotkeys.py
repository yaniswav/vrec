"""Keyboard controls during a batch: read keys without blocking and map them to commands.

Only this module touches the keyboard (`msvcrt`). The rest of vrec works with a `poll` callable that
returns the pending command, or None, so tests inject a fake key source and a later status bar can
reuse the same commands and texts.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import Protocol

PAUSE = "pause"
SKIP = "skip"
RESTART = "restart"
QUIT = "quit"
HELP = "help"

_KEYS = {"p": PAUSE, "s": SKIP, "r": RESTART, "q": QUIT, "h": HELP}

# Set by the scheduled-run launcher: nobody is at the keyboard, even if the task has a console window.
SCHEDULED_ENV = "VREC_SCHEDULED"

KEYS_LINE = "Keys: P pause, S skip, R restart, Q stop after this video, H help"
HELP_LINES = (
    "P  pause or resume the recording",
    "S  skip this video (its file is named SKIPPED, its history is left as it was)",
    "R  restart this video from the beginning (the partial file is deleted)",
    "Q  stop the batch after this video (press again to cancel)",
    "H  show this list",
)
STOP_MESSAGE = "Will stop after this video (Q again to cancel)."
CANCEL_MESSAGE = "Cancelled: the batch goes on."


class Reader(Protocol):
    def poll(self) -> str | None:
        """The next pending command (PAUSE, SKIP...), or None. Never blocks."""

    def flush(self) -> None:
        """Drop every key typed so far."""


class NullReader:
    """No keyboard: nothing is ever pending."""

    def poll(self) -> str | None:
        return None

    def flush(self) -> None:
        pass


class ConsoleReader:
    """Reads the Windows console without blocking. Keys that aren't commands are ignored."""

    def __init__(self) -> None:
        import msvcrt

        self._msvcrt = msvcrt

    def _next_char(self) -> str | None:
        """The next typed character, "" for a special key (its 2 chars are swallowed), None if none."""
        if not self._msvcrt.kbhit():
            return None
        char: str = self._msvcrt.getwch()
        if char in ("\x00", "\xe0"):  # arrows, F-keys...: a second char follows
            if self._msvcrt.kbhit():
                self._msvcrt.getwch()
            return ""
        return char

    def poll(self) -> str | None:
        while (char := self._next_char()) is not None:
            command = _KEYS.get(char.lower())
            if command:
                return command
        return None

    def flush(self) -> None:
        while self._next_char() is not None:
            pass


def available(feature_on: bool, isatty: bool | None = None, scheduled: bool | None = None) -> bool:
    """Whether keys can be used: the feature is on and vrec runs by hand in a console.

    The last two arguments exist for tests.
    """
    if not feature_on:
        return False
    if scheduled is None:
        scheduled = bool(os.environ.get(SCHEDULED_ENV))
    if scheduled:
        return False
    if isatty is None:
        isatty = sys.stdin is not None and sys.stdin.isatty()
    return isatty


class Controls:
    """The batch's key state: a reader plus the "stop after this video" toggle."""

    def __init__(self, reader: Reader) -> None:
        self.reader = reader
        self.quit_requested = False

    def poll(self, end_progress: Callable[[], None] = lambda: None) -> str | None:
        """The next command for the recording loop (PAUSE, SKIP or RESTART), or None.

        Q and H are answered here. `end_progress` closes the progress line first so they don't garble it.
        """
        command = self.reader.poll()
        if command in (QUIT, HELP):
            end_progress()
            for line in self.react(command):
                print(f"   {line}")
            return None
        return command

    def drain(self) -> None:
        """Between two videos: answer every pending Q and H, ignore the other keys."""
        while (command := self.reader.poll()) is not None:
            for line in self.react(command):
                print(f"   {line}")

    def react(self, command: str) -> list[str]:
        """The lines to print for Q (toggle) or H; nothing for the other commands."""
        if command == QUIT:
            self.quit_requested = not self.quit_requested
            return [STOP_MESSAGE if self.quit_requested else CANCEL_MESSAGE]
        if command == HELP:
            return list(HELP_LINES)
        return []


def make_controls(feature_on: bool) -> Controls | None:
    """Controls on the real keyboard, or None when keys can't be used."""
    if not available(feature_on):
        return None
    try:
        reader = ConsoleReader()
    except ImportError:
        return None
    reader.flush()  # e.g. the Enter that started the batch
    return Controls(reader)
