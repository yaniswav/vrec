"""A fixed 2-line status bar at the bottom of the terminal during a batch.

The last two terminal lines are reserved with a scroll region (VT escape sequences), so normal output
scrolls above them while the bar stays put. Only this module writes escape sequences, and only to the
real console stream, never to the run log (see logs._Tee.raw). When the bar can't be used (not a
terminal, VT unsupported, feature off, scheduled run) nothing changes: the in-place progress line stays.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TextIO

from vrec.console import human_duration
from vrec.hotkeys import SCHEDULED_ENV

BAR_LINES = 2
MIN_ROWS = 8
MIN_COLS = 30
REFRESH_S = 0.25  # at most a few redraws per second while the state kind stays the same

KEYS_HOTKEYS = "[P] Pause  [S] Skip  [R] Restart  [Q] Stop after this video  [H] Help"
KEYS_HOTKEYS_ARMED = "[P] Pause  [S] Skip  [R] Restart  [Q] Cancel the stop  [H] Help"
KEYS_NONE = "Ctrl+C to stop"

LOADING, PLAYING, PAUSED, BUFFERING, AWAY = "loading", "playing", "paused", "buffering", "away"
LOADING_PAGE, WAITING_VIDEO = "loading_page", "waiting_video"

_UNICODE = {"sep": "│", PLAYING: "▶", PAUSED: "⏸", BUFFERING: "⏳"}
_ASCII = {"sep": "|", PLAYING: ">", PAUSED: "||", BUFFERING: "..."}

_ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
_STD_OUTPUT_HANDLE = -11

ESC = "\x1b"


@dataclass
class BatchCounts:
    """What the batch has done so far: outcomes and the known durations (seconds) of finished videos."""

    ok: int = 0
    failed: int = 0
    skipped: int = 0
    durations: list[float] = field(default_factory=list)


def estimate_end(
    now: float, current_left: float | None, left_videos: int, durations: list[float]
) -> float | None:
    """Estimated end time (epoch seconds) of the whole batch, or None while there is no data.

    `current_left`: seconds left in the current video (None = unknown). The videos not started yet
    count for the average of `durations` (the videos known so far, the current one included).
    """
    if current_left is None:
        return None
    if left_videos <= 0:
        return now + current_left
    if not durations:
        return None
    return now + current_left + left_videos * sum(durations) / len(durations)


def enable_vt() -> bool:
    """Turn on VT escape sequences for the console's output (Windows). False if it can't be done."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(_STD_OUTPUT_HANDLE)
    mode = wintypes.DWORD()
    if not handle or not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return False
    if mode.value & _ENABLE_VIRTUAL_TERMINAL_PROCESSING:
        return True
    return bool(kernel32.SetConsoleMode(handle, mode.value | _ENABLE_VIRTUAL_TERMINAL_PROCESSING))


def can_encode(text: str, encoding: str | None) -> bool:
    try:
        text.encode(encoding or "ascii")
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def _terminal_size() -> tuple[int, int]:
    size = shutil.get_terminal_size()
    return size.columns, size.lines


class StatusBar:
    """The bar: fed with state by the batch and the watch loop, draws itself on the real stream."""

    def __init__(
        self,
        out: TextIO,
        hotkeys_active: bool,
        stopping: Callable[[], bool] = lambda: False,
        size: Callable[[], tuple[int, int]] = _terminal_size,
        clock: Callable[[], float] = time.time,
        use_unicode: bool | None = None,
    ) -> None:
        self._out = out
        self._hotkeys = hotkeys_active
        self._stopping = stopping
        self._size = size  # (columns, rows)
        self._clock = clock
        if use_unicode is None:
            use_unicode = all(can_encode(s, getattr(out, "encoding", None)) for s in _UNICODE.values())
        self._sym = _UNICODE if use_unicode else _ASCII
        self.active = False
        self._applied: tuple[int, int] | None = None  # (rows, cols) the region was set for
        self._last_draw = 0.0
        self._number, self._total = 0, 0
        self._counts = BatchCounts()
        self._kind = LOADING
        self._t: float | None = None
        self._d: float | None = None
        self._buffer: float | None = None
        self._res: tuple[int, int] | None = None

    # ----- rendering -----

    def lines(self) -> tuple[str, str]:
        """The two bar lines, not yet truncated to the terminal width."""
        sym = self._sym
        state, symbol = {
            PLAYING: (self._playing_text(), sym[PLAYING]),
            PAUSED: ("Paused", sym[PAUSED]),
            BUFFERING: (f"Buffering {self._buffer or 0:.1f} s", sym[BUFFERING]),
            AWAY: ("Away (Chrome not on this desktop)", "!"),
            LOADING_PAGE: ("Preparing (loading the page)", sym[PLAYING]),
            WAITING_VIDEO: ("Preparing (waiting for the video)", sym[PLAYING]),
        }.get(self._kind, ("Preparing", sym[PLAYING]))
        stopping = self._stopping()
        parts = [f"{symbol} {self._number}/{self._total}  {state}"]
        if stopping:
            parts.append("Stopping after this video")
        if self._res and self._res[0] and self._res[1]:
            parts.append(f"{self._res[0]}x{self._res[1]}")
        c = self._counts
        parts.append(f"Batch: {c.ok} OK, {c.failed} failed, {c.skipped} skipped")
        parts.append(f"ETA {self._eta_text()}")
        keys = (KEYS_HOTKEYS_ARMED if stopping else KEYS_HOTKEYS) if self._hotkeys else KEYS_NONE
        return f" {sym['sep']} ".join(parts), keys

    def _playing_text(self) -> str:
        t, d = self._t or 0.0, self._d
        if d and d > 0:
            return f"Playing {t / d:.1%} ({human_duration(t)} / {human_duration(d)})"
        return f"Playing {human_duration(t)}"

    def _eta_text(self) -> str:
        known = bool(self._d and self._d > 0)
        left = max(self._d - (self._t or 0.0), 0.0) if known and self._d else None
        durations = list(self._counts.durations)
        if known and self._d:
            durations.append(self._d)
        end = estimate_end(self._clock(), left, self._total - self._number, durations)
        return "?" if end is None else time.strftime("%H:%M", time.localtime(end))

    # ----- terminal output -----

    def _write(self, text: str) -> None:
        try:
            self._out.write(text)
            self._out.flush()
        except (OSError, ValueError):
            pass  # a closed or broken console must never abort the batch

    def _paint(self, rows: int, cols: int) -> str:
        width = max(cols - 1, 0)  # the last column stays free: no wrap, no scroll
        one, two = (line[:width] for line in self.lines())
        return f"{ESC}7{ESC}[{rows - 1};1H{one}{ESC}[K{ESC}[{rows};1H{two}{ESC}[K{ESC}8"

    def _current_size(self) -> tuple[int, int]:
        cols, rows = self._size()
        return rows, cols

    def start(self, total: int) -> bool:
        """Reserve the two lines. False (and nothing written) if the terminal is too small."""
        rows, cols = self._current_size()
        if rows < MIN_ROWS or cols < MIN_COLS:
            return False
        self._total = total
        self.active = True
        atexit.register(self.stop)
        # Scroll the screen up to make room, step back into the region, then reserve the lines.
        self._write(f"\n\n{ESC}[2A{ESC}7{ESC}[1;{rows - BAR_LINES}r{ESC}8" + self._paint(rows, cols))
        self._applied = (rows, cols)
        self._last_draw = self._clock()
        return True

    def stop(self) -> None:
        """Give the terminal back: scroll region reset, bar lines cleared, cursor where the output ended."""
        if not self.active:
            return
        self.active = False
        atexit.unregister(self.stop)
        rows, _ = self._current_size()
        old_rows = self._applied[0] if self._applied else rows
        bar_rows = sorted({old_rows - 1, old_rows, rows - 1, rows})
        clear = "".join(f"{ESC}[{r};1H{ESC}[2K" for r in bar_rows if 0 < r <= rows)
        self._write(f"{ESC}7{ESC}[r{clear}{ESC}8")

    @contextmanager
    def running(self, total: int) -> Iterator[None]:
        """Show the bar for the block; the terminal is always given back, even on an error or Ctrl+C."""
        self.start(total)
        try:
            yield
        finally:
            self.stop()

    def refresh(self, force: bool = False) -> None:
        """Redraw if the terminal was resized, the state changed, or enough time passed."""
        if not self.active:
            return
        rows, cols = self._current_size()
        now = self._clock()
        if (rows, cols) != self._applied:
            self._resize(rows)
            self._applied = (rows, cols)
        elif not force and now - self._last_draw < REFRESH_S:
            return
        self._last_draw = now
        self._write(self._paint(rows, cols))

    def _resize(self, rows: int) -> None:
        old_rows = self._applied[0] if self._applied else rows
        wipe = "".join(f"{ESC}[{r};1H{ESC}[2K" for r in sorted({old_rows - 1, old_rows}) if 0 < r <= rows)
        self._write(f"{ESC}7{ESC}[r{wipe}{ESC}[1;{max(rows - BAR_LINES, 1)}r{ESC}8")

    # ----- what the batch and the watch loop feed -----

    def begin_video(self, number: int, total: int, counts: BatchCounts) -> None:
        self._number, self._total, self._counts = number, total, counts
        self._kind, self._t, self._d, self._buffer, self._res = LOADING, None, None, None, None
        self.refresh(force=True)

    def update_counts(self, counts: BatchCounts) -> None:
        self._counts = counts
        self.refresh(force=True)

    def set_state(
        self,
        kind: str,
        t: float | None = None,
        d: float | None = None,
        buffer: float | None = None,
        res: tuple[int, int] | None = None,
    ) -> None:
        changed = kind != self._kind
        self._kind, self._t, self._buffer = kind, t, buffer
        if d is not None:
            self._d = d
        if res is not None:
            self._res = res
        self.refresh(force=changed)


def available(feature_on: bool, isatty: bool | None = None, scheduled: bool | None = None) -> bool:
    """Whether the bar can be used: feature on, vrec run by hand, stdout is a terminal.

    The last two arguments exist for tests.
    """
    if not feature_on:
        return False
    if scheduled is None:
        scheduled = bool(os.environ.get(SCHEDULED_ENV))
    if scheduled:
        return False
    if isatty is None:
        isatty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    return isatty


def make_bar(
    feature_on: bool, hotkeys_active: bool, stopping: Callable[[], bool] = lambda: False
) -> StatusBar | None:
    """A bar on the real console, or None when it can't be used (today's progress line stays)."""
    if not available(feature_on) or not enable_vt():
        return None
    out: TextIO = getattr(sys.stdout, "raw", sys.stdout)
    return StatusBar(out, hotkeys_active, stopping)
