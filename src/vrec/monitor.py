"""Playback monitoring: the loop that runs while a video is being recorded.

It watches the player, pauses OBS while the player buffers, and decides when to stop.
Everything it touches goes through two small interfaces (`Player` and `Capture`) and a
clock, so the whole loop can be exercised in tests with fakes, without Chrome or OBS.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, TypedDict

from vrec import hotkeys
from vrec.console import human_duration
from vrec.obs_control import changed_fraction

# While buffering, resume once the reserve has stopped growing for this long: some players
# cap their buffer by size, so at high bitrates the resume threshold may never be reached.
PLATEAU_S = 5.0
# Frozen image check: seconds between two screenshots, and the first playback second it may run.
FROZEN_INTERVAL_S = 10.0
FROZEN_START_S = 10.0
# A pair of screenshots counts as "changed" when at least this fraction of pixels moved.
FROZEN_MIN_CHANGE = 0.02
# Seconds between two checks of the player.
TICK_S = 0.5
# The page may pause the video on its own (focus loss, autoplay rules...): replay it at most
# this many times in a row without progress.
MAX_REPLAYS = 5
# Seconds (wall time) between two checks that the recorder is still recording.
LIVENESS_S = 30.0
# Seconds between two checks that Chrome is visible on the current virtual desktop.
DESKTOP_CHECK_S = 2.0
AWAY_MESSAGE = "Chrome isn't on the current virtual desktop: recording paused until it is visible again."
# Consecutive checks that couldn't tell before the recorder is considered lost.
MAX_LIVENESS_UNKNOWN = 2


class StopReason(StrEnum):
    ENDED = "ended"
    TEST_DONE = "test finished"
    TIME_LIMIT = "time limit reached"
    VIDEO_GONE = "video removed from page"
    STALLED = "loading stalled"
    BLACK = "black image"
    FROZEN = "frozen image"
    TOO_LONG = "took too long"
    OBS_LOST = "OBS stopped recording"
    SKIPPED = "skipped"  # S key
    RESTART = "restart requested"  # R key


class PlayerState(TypedDict):
    """The video element's state, as reported by js/state.js."""

    ended: bool
    t: float
    d: float
    paused: bool
    w: int
    h: int
    buffer: float


class Player(Protocol):
    """The video in the page."""

    def state(self) -> PlayerState | None:
        """The video's current state, or None if it left the page."""

    def play(self) -> None: ...

    def pause(self) -> None: ...

    def wait(self, seconds: float) -> None: ...


class Capture(Protocol):
    """The recorder (OBS) as seen by the loop."""

    def pause(self) -> None:
        """Pause the recording. Raises if the recorder refuses."""

    def resume(self) -> None: ...

    def is_black(self) -> bool | None:
        """Whether the captured image is black; None if it couldn't be checked."""

    def frame(self) -> bytes | None:
        """A small grayscale signature of the captured image; None if it couldn't be taken."""

    def audio_peak(self) -> float | None:
        """Loudest audio level since the recording started; None if unavailable."""

    def recording(self) -> bool | None:
        """Whether the recorder is still recording; None if it couldn't tell."""


@dataclass(frozen=True)
class WatchConfig:
    """Everything the loop needs to decide, computed once per video."""

    duration: float  # seconds, may be NaN/inf when unknown
    test_mode: bool = False
    test_duration_s: float = 30
    pause_below_s: float = 2
    resume_at_s: float = 10
    max_stall_s: float = 300
    abort_if_black_after_s: float = 60
    abort_if_frozen_after_s: float = 180
    audio_level: float = 0.003
    max_wall_factor: float = 3
    max_wall_extra_s: float = 600
    target_height: int = 0  # height asked from the player (0 = unknown): used for the quality warning
    buffer_pause: bool = True
    black_check: bool = True
    frozen_check: bool = True
    audio_check: bool = True
    wall_clock_cap: bool = True

    def soft_limit(self) -> float:
        """Playing time (pauses excluded) after which the end is considered missed."""
        if self.test_mode:
            return self.test_duration_s
        if _known(self.duration):
            return self.duration + 120
        return 4 * 3600

    def wall_limit(self) -> float:
        """Wall-clock seconds after which the video is abandoned, pauses included (inf = no cap)."""
        if not self.wall_clock_cap:
            return math.inf
        if self.test_mode:
            base = self.test_duration_s
        elif _known(self.duration) and self.duration > 0:
            base = self.duration * self.max_wall_factor
        else:
            base = 4 * 3600
        return base + self.max_wall_extra_s


@dataclass
class WatchOutcome:
    reason: StopReason
    image_ok: bool | None = False  # None: not checked (black_check off)
    max_resolution: tuple[int, int] = field(default=(0, 0))
    buffering_pauses: int = 0
    desktop_pauses: int = 0


def _say(message: str) -> None:
    print(f"   {message}")


@dataclass
class Output:
    """Where the loop reports: one-off warnings, a progress line rewritten in place, and plain notes."""

    warn: Callable[[str], None]
    progress: Callable[[str], None]
    end_progress: Callable[[], None]
    info: Callable[[str], None] = _say


class _Holds:
    """Why the video and the recording are paused (buffering, away, manual): paused while any is active.

    Tracks the total time spent paused so it can be left out of the playing time.
    """

    def __init__(self) -> None:
        self.active: set[str] = set()
        self.since = 0.0
        self.total = 0.0

    def begin(self, reason: str, now: float) -> None:
        if not self.active:
            self.since = now
        self.active.add(reason)

    def end(self, reason: str, now: float) -> bool:
        """Drop a reason. True when it was the last one, so the recording must resume."""
        self.active.discard(reason)
        if self.active:
            return False
        self.total += now - self.since
        return True

    def held(self, now: float) -> float:
        return self.total + (now - self.since if self.active else 0.0)


def _known(value: float | None) -> bool:
    return bool(value) and math.isfinite(value)  # type: ignore[arg-type]


def watch(
    player: Player,
    capture: Capture,
    config: WatchConfig,
    output: Output,
    clock: Callable[[], float] = time.time,
    visible: Callable[[], bool | None] | None = None,
    keys: Callable[[], str | None] | None = None,
) -> WatchOutcome:
    """Follow playback until the video ends or a stop condition is met.

    `visible` tells whether Chrome is shown on the current virtual desktop (None = can't tell, which
    counts as visible). While it isn't, the video and the recording are paused, like when buffering.
    `keys` returns the pending keyboard command (hotkeys.PAUSE, SKIP, RESTART) or None. P pauses the
    video and the recording too. They resume only when no pause reason (buffering, away, P) is left.
    """
    outcome = WatchOutcome(reason=StopReason.ENDED, image_ok=False if config.black_check else None)
    start = clock()
    soft_limit = config.soft_limit()
    wall_deadline = start + config.wall_limit()

    last_t, last_progress = -1.0, start
    audio_warned = stall_warned = quality_warned = False
    replays = 0
    next_black_check = start + 3
    next_frame_check = start + FROZEN_START_S
    prev_frame: bytes | None = None
    frame_t, frame_elapsed, frozen_s = 0.0, 0.0, 0.0
    buffering, buffering_start = False, 0.0
    holds = _Holds()
    pause_allowed = config.buffer_pause
    best_buffer, last_growth = 0.0, 0.0
    next_liveness, liveness_unknown = start + LIVENESS_S, 0
    away, away_start = False, 0.0  # Chrome isn't on the current desktop
    manual, manual_start = False, 0.0  # paused with P
    next_desktop_check = start + DESKTOP_CHECK_S

    def hold(reason: str, now: float) -> bool:
        """Pause the video and the recording for a reason. False if OBS refuses (nothing changed)."""
        if not holds.active:
            player.pause()
            try:
                capture.pause()
            except Exception:
                player.play()
                return False
        holds.begin(reason, now)
        return True

    def release(reason: str, now: float) -> None:
        if holds.end(reason, now):
            capture.resume()
            player.play()

    def resumed_from_pause(now: float, span_start: float) -> None:
        """Bookkeeping after an away or manual pause: it must not count as a stall or as buffering time."""
        nonlocal last_progress, last_growth, buffering_start
        last_progress = now  # the video didn't advance meanwhile: not a stall
        if buffering:
            last_growth = now  # the buffer's plateau clock must not count that time
            buffering_start += now - span_start

    while True:
        player.wait(TICK_S)
        now = clock()
        elapsed = now - start - holds.held(now)
        state = player.state()
        if state is None:
            outcome.reason = StopReason.VIDEO_GONE
            break
        if state["ended"]:
            outcome.reason = StopReason.ENDED
            break
        if elapsed > soft_limit:
            outcome.reason = StopReason.TEST_DONE if config.test_mode else StopReason.TIME_LIMIT
            break
        if now >= wall_deadline + (now - manual_start if manual else 0):
            # Stops even mid-buffering: OBS can stop a paused recording just fine.
            outcome.reason = StopReason.TOO_LONG
            break

        command = None
        if keys is not None:
            try:
                command = keys()
            except Exception:
                command = None
        if command == hotkeys.SKIP:
            outcome.reason = StopReason.SKIPPED
            break
        if command == hotkeys.RESTART:
            outcome.reason = StopReason.RESTART
            break
        if command == hotkeys.PAUSE:
            output.end_progress()
            if manual:
                release("manual", now)
                manual = False
                wall_deadline += now - manual_start  # the user's pause doesn't count against the cap
                resumed_from_pause(now, manual_start)
                output.info("Resumed.")
            elif hold("manual", now):
                manual, manual_start = True, now
                output.info("Paused (P to resume).")
            else:
                output.warn("OBS refuses to pause: can't pause the recording.")

        if now >= next_liveness:
            next_liveness = now + LIVENESS_S
            try:
                alive = capture.recording()
            except Exception:
                alive = None
            liveness_unknown = liveness_unknown + 1 if alive is None else 0
            if alive is False or liveness_unknown >= MAX_LIVENESS_UNKNOWN:
                outcome.reason = StopReason.OBS_LOST
                break

        if visible is not None and now >= next_desktop_check:
            next_desktop_check = now + DESKTOP_CHECK_S
            try:
                shown = visible()
            except Exception:
                shown = None
            if shown is False and not away:
                if hold("away", now):
                    away, away_start = True, now
                    outcome.desktop_pauses += 1
                    output.end_progress()
                    output.warn(AWAY_MESSAGE)
                else:
                    visible = None  # OBS can't pause: give the feature up for this video
                    output.warn("OBS refuses to pause: switching desktops will be recorded.")
            elif shown is not False and away:
                release("away", now)
                away = False
                resumed_from_pause(now, away_start)
                output.progress("Chrome is visible again: recording resumed.")
                output.end_progress()
        if away:
            output.progress("Chrome isn't on the current virtual desktop (recording paused)")
            continue
        if manual:
            output.progress("Paused (P to resume)")
            continue

        video_duration = state["d"]
        remaining = video_duration - state["t"] if _known(video_duration) else math.inf
        buffer = state["buffer"]

        # Buffering: both the video and the recording are paused.
        if buffering:
            waited = now - buffering_start
            if buffer > best_buffer + 0.5:
                best_buffer, last_growth = buffer, now
            enough = buffer >= min(config.resume_at_s, remaining - 0.5)
            plateaued = now - last_growth >= PLATEAU_S and buffer >= config.pause_below_s + 2
            output.progress(f"Buffering... {buffer:4.1f} s in reserve (recording paused)")
            if enough or plateaued:
                release("buffering", now)
                buffering = False
            elif waited > config.max_stall_s:
                outcome.reason = StopReason.STALLED
                break
            continue

        # Buffer almost empty: pause everything BEFORE the image freezes.
        if pause_allowed and buffer < config.pause_below_s and remaining > config.pause_below_s + 1:
            if not hold("buffering", now):
                pause_allowed = False
                output.warn("OBS refuses to pause: buffering will be recorded (frozen image).")
            else:
                buffering, buffering_start = True, now
                best_buffer, last_growth = buffer, now
                outcome.buffering_pauses += 1
                output.end_progress()
            continue

        if _known(video_duration):
            output.progress(
                f"Playing {state['t'] / video_duration:6.1%}  "
                f"({human_duration(state['t'])} / {human_duration(video_duration)})"
            )

        if state["t"] != last_t:
            last_t, last_progress = state["t"], now
            replays = 0
        elif now - last_progress > 15 and not stall_warned:
            output.warn("The video isn't advancing (loading?). Recording continues.")
            stall_warned = True

        if state["paused"] and replays < MAX_REPLAYS:  # the page paused on its own
            player.play()
            replays += 1

        if state["h"] > outcome.max_resolution[1]:
            outcome.max_resolution = (state["w"], state["h"])
        if (
            config.target_height
            and not quality_warned
            and elapsed > 20
            and outcome.max_resolution[1] < config.target_height * 0.9
        ):
            w, h = outcome.max_resolution
            output.warn(f"Quality lower than expected ({w}x{h}). Connection too slow?")
            quality_warned = True

        if config.black_check and not outcome.image_ok and now >= next_black_check:
            black = capture.is_black()
            if black is None:
                next_black_check = now + 5  # screenshot failed: retry later, don't abort
            elif black:
                next_black_check = now + 5
                if elapsed > config.abort_if_black_after_s:
                    outcome.reason = StopReason.BLACK
                    break
            else:
                outcome.image_ok = True

        if config.frozen_check and now >= next_frame_check:
            next_frame_check = now + FROZEN_INTERVAL_S
            frame = capture.frame()
            if frame is not None:
                # Only real playback counts: the time must have advanced and the video not be paused.
                playing = not state["paused"] and state["t"] - frame_t > FROZEN_INTERVAL_S / 4
                if playing and prev_frame is not None:
                    if changed_fraction(prev_frame, frame) >= FROZEN_MIN_CHANGE:
                        frozen_s = 0.0
                    else:
                        frozen_s += elapsed - frame_elapsed
                        if frozen_s >= config.abort_if_frozen_after_s:
                            outcome.reason = StopReason.FROZEN
                            break
                prev_frame, frame_t, frame_elapsed = frame, state["t"], elapsed

        if config.audio_check and not audio_warned and elapsed > 20:
            peak = capture.audio_peak()
            if peak is not None and peak < config.audio_level:
                output.warn("No audio detected. Check that Chrome is routed to CABLE Input.")
                audio_warned = True

    output.end_progress()
    return outcome
