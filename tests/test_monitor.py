"""The playback loop, driven by a simulated player and recorder (no Chrome, no OBS)."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from vrec.monitor import MAX_REPLAYS, Output, StopReason, WatchConfig, watch


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class SimPlayer:
    """A video that plays in real (fake) time and downloads at `rate` seconds of video per second.

    `buffer_cap` models players that limit their buffer (e.g. by size): the reserve never grows
    beyond it. `self_pauses` makes the page pause the video by itself at those playback times.
    """

    clock: FakeClock
    duration: float = 60.0
    rate: float = 10.0
    buffer_cap: float = 30.0
    initial_buffer: float = 10.0
    height: int = 2160
    vanish_at: float | None = None
    self_pauses: list[float] = field(default_factory=list)
    t: float = 0.0
    paused: bool = True
    buffer: float = 0.0
    plays: int = 0
    pauses: int = 0

    def __post_init__(self) -> None:
        self.buffer = self.initial_buffer

    def wait(self, seconds: float) -> None:
        self.clock.now += seconds
        self.buffer = min(self.buffer + self.rate * seconds, self.buffer_cap, self.duration - self.t)
        if not self.paused:
            step = min(seconds, self.buffer)
            self.t += step
            self.buffer -= step
            if self.self_pauses and self.t >= self.self_pauses[0]:
                self.self_pauses.pop(0)
                self.paused = True

    def state(self) -> dict[str, Any] | None:
        if self.vanish_at is not None and self.t >= self.vanish_at:
            return None
        return {
            "ended": self.t >= self.duration,
            "t": self.t,
            "d": self.duration,
            "paused": self.paused,
            "w": self.height * 2,
            "h": self.height,
            "buffer": self.buffer,
        }

    def play(self) -> None:
        self.plays += 1
        self.paused = False

    def pause(self) -> None:
        self.pauses += 1
        self.paused = True


@dataclass
class FakeCapture:
    black: list[bool | None] = field(default_factory=lambda: [False])
    peak: float | None = 0.5
    refuse_pause: bool = False
    paused: bool = False
    pause_calls: int = 0
    resume_calls: int = 0
    black_checks: int = 0
    alive: list[bool | None] = field(default_factory=lambda: [True])
    alive_checks: int = 0
    # The picture after n screenshots: by default it changes every time (a playing video).
    picture: Callable[[int], bytes | None] = lambda n: bytes([(n * 97) % 256]) * 100  # noqa: E731
    frame_checks: int = 0

    def pause(self) -> None:
        self.pause_calls += 1
        if self.refuse_pause:
            raise RuntimeError("OBS refused")
        self.paused = True

    def resume(self) -> None:
        self.resume_calls += 1
        self.paused = False

    def is_black(self) -> bool | None:
        self.black_checks += 1
        return self.black[min(self.black_checks - 1, len(self.black) - 1)]

    def frame(self) -> bytes | None:
        self.frame_checks += 1
        return self.picture(self.frame_checks)

    def audio_peak(self) -> float | None:
        return self.peak

    def recording(self) -> bool | None:
        self.alive_checks += 1
        return self.alive[min(self.alive_checks - 1, len(self.alive) - 1)]


class Log:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def output(self) -> Output:
        return Output(warn=self.warnings.append, progress=lambda _text: None, end_progress=lambda: None)


def run(player: SimPlayer, capture: FakeCapture | None = None, **config: Any):
    capture = capture or FakeCapture()
    log = Log()
    player.play()  # record_one starts playback right before watching
    outcome = watch(
        player, capture, WatchConfig(duration=player.duration, **config), log.output(), player.clock
    )
    return outcome, capture, log


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def test_fast_connection_plays_to_the_end(clock):
    outcome, capture, log = run(SimPlayer(clock, duration=60, rate=10))
    assert outcome.reason == StopReason.ENDED
    assert outcome.buffering_pauses == 0
    assert outcome.image_ok is True
    assert outcome.max_resolution == (4320, 2160)
    assert capture.pause_calls == 0
    assert log.warnings == []


def test_slow_connection_pauses_and_resumes_the_recording(clock):
    player = SimPlayer(clock, duration=120, rate=0.6, initial_buffer=3)
    outcome, capture, _ = run(player)
    assert outcome.reason == StopReason.ENDED
    assert outcome.buffering_pauses >= 1
    assert capture.pause_calls == capture.resume_calls == outcome.buffering_pauses
    assert not capture.paused


def test_buffer_plateau_resumes_instead_of_stalling(clock):
    # The player never buffers more than 5 s: resume_at (10 s) can't be reached.
    player = SimPlayer(clock, duration=120, rate=0.8, buffer_cap=5, initial_buffer=1)
    outcome, capture, _ = run(player, max_stall_s=60)
    assert outcome.reason == StopReason.ENDED
    assert capture.resume_calls >= 1


def test_buffer_plateau_below_the_minimum_is_a_stall(clock):
    # Capped at 3 s (< pause_below + 2): resuming would pause again at once, so it stalls.
    player = SimPlayer(clock, duration=120, rate=0.8, buffer_cap=3, initial_buffer=1)
    outcome, _, _ = run(player, max_stall_s=60)
    assert outcome.reason == StopReason.STALLED


def test_no_download_at_all_stalls_after_max_stall(clock):
    player = SimPlayer(clock, duration=120, rate=0.0, initial_buffer=1)
    start = clock.now
    outcome, _, _ = run(player, max_stall_s=30)
    assert outcome.reason == StopReason.STALLED
    assert 30 < clock.now - start < 40


def test_buffer_pause_feature_off_never_pauses_obs(clock):
    player = SimPlayer(clock, duration=60, rate=0.5, initial_buffer=1)
    outcome, capture, _ = run(player, buffer_pause=False)
    assert capture.pause_calls == 0
    assert outcome.buffering_pauses == 0


def test_obs_refusing_to_pause_disables_pausing_with_a_warning(clock):
    player = SimPlayer(clock, duration=60, rate=0.5, initial_buffer=1)
    outcome, capture, log = run(player, FakeCapture(refuse_pause=True))
    assert capture.pause_calls == 1
    assert outcome.buffering_pauses == 0
    assert any("refuses to pause" in w for w in log.warnings)
    assert outcome.reason == StopReason.ENDED


def test_video_leaving_the_page(clock):
    outcome, _, _ = run(SimPlayer(clock, duration=60, vanish_at=10))
    assert outcome.reason == StopReason.VIDEO_GONE


def test_test_mode_stops_after_the_test_duration(clock):
    outcome, _, _ = run(SimPlayer(clock, duration=600), test_mode=True, test_duration_s=30)
    assert outcome.reason == StopReason.TEST_DONE


def test_missed_end_hits_the_soft_limit(clock):
    player = SimPlayer(clock, duration=math.inf)
    outcome, _, _ = run(player, max_wall_extra_s=10**9)
    assert outcome.reason == StopReason.TIME_LIMIT


def test_wall_clock_cap_fires_even_while_buffering(clock):
    player = SimPlayer(clock, duration=60, rate=0.0, initial_buffer=1)
    start = clock.now
    outcome, capture, _ = run(player, max_stall_s=10**6, max_wall_factor=1, max_wall_extra_s=30)
    assert outcome.reason == StopReason.TOO_LONG
    assert capture.paused  # stopped while paused for buffering
    assert clock.now - start == pytest.approx(90, abs=1)


def test_wall_clock_cap_feature_off(clock):
    config = WatchConfig(duration=60, wall_clock_cap=False)
    assert config.wall_limit() == math.inf


def test_black_image_is_abandoned_after_the_delay(clock):
    capture = FakeCapture(black=[True])
    outcome, _, _ = run(SimPlayer(clock, duration=600), capture, abort_if_black_after_s=60)
    assert outcome.reason == StopReason.BLACK
    assert outcome.image_ok is False


def test_image_seen_after_a_black_start(clock):
    capture = FakeCapture(black=[True, True, False])
    outcome, _, _ = run(SimPlayer(clock, duration=60), capture)
    assert outcome.reason == StopReason.ENDED
    assert outcome.image_ok is True


def test_failed_screenshots_never_abort_nor_validate(clock):
    capture = FakeCapture(black=[None])
    outcome, _, _ = run(SimPlayer(clock, duration=120), capture, abort_if_black_after_s=10)
    assert outcome.reason == StopReason.ENDED
    assert outcome.image_ok is False


def test_black_check_feature_off(clock):
    capture = FakeCapture(black=[True])
    outcome, _, _ = run(SimPlayer(clock, duration=120), capture, black_check=False, abort_if_black_after_s=10)
    assert outcome.reason == StopReason.ENDED
    assert outcome.image_ok is None
    assert capture.black_checks == 0


STILL = bytes(100)


def test_frozen_picture_stops_after_threshold(clock):
    capture = FakeCapture(picture=lambda n: STILL)
    outcome, capture, _ = run(SimPlayer(clock, duration=600), capture, abort_if_frozen_after_s=60)
    assert outcome.reason == StopReason.FROZEN
    assert StopReason.FROZEN.value == "frozen image"
    assert 70 <= clock.now - 1000 <= 90  # 10 s grace + first sample + 60 s of identical pairs


def test_moving_picture_never_stops(clock):
    outcome, _, _ = run(SimPlayer(clock, duration=600), FakeCapture(), abort_if_frozen_after_s=60)
    assert outcome.reason == StopReason.ENDED


def test_a_change_resets_the_frozen_counter(clock):
    def picture(n: int) -> bytes:
        return bytes(100) if n % 5 else b"\xff" * 100  # a real change every 5th sample (~50 s)

    capture = FakeCapture(picture=picture)
    outcome, _, _ = run(SimPlayer(clock, duration=600), capture, abort_if_frozen_after_s=100)
    assert outcome.reason == StopReason.ENDED


def test_small_noise_still_counts_as_frozen(clock):
    def picture(n: int) -> bytes:
        return bytes([n % 3] * 100)  # tiny flicker, below the tolerance

    outcome, _, _ = run(
        SimPlayer(clock, duration=600), FakeCapture(picture=picture), abort_if_frozen_after_s=60
    )
    assert outcome.reason == StopReason.FROZEN


def test_failed_screenshots_are_ignored_by_frozen_check(clock):
    capture = FakeCapture(picture=lambda n: None)
    outcome, capture, _ = run(SimPlayer(clock, duration=300), capture, abort_if_frozen_after_s=30)
    assert outcome.reason == StopReason.ENDED
    assert capture.frame_checks > 0


def test_frozen_check_feature_off(clock):
    capture = FakeCapture(picture=lambda n: STILL)
    outcome, capture, _ = run(
        SimPlayer(clock, duration=300), capture, frozen_check=False, abort_if_frozen_after_s=30
    )
    assert outcome.reason == StopReason.ENDED
    assert capture.frame_checks == 0


def test_frozen_not_counted_while_buffering(clock):
    # Slow download: the player pauses to buffer (recording paused), which is not frozen playback.
    player = SimPlayer(clock, duration=200, rate=0.5, initial_buffer=3, buffer_cap=30)
    capture = FakeCapture(picture=lambda n: STILL)
    outcome, capture, _ = run(player, capture, abort_if_frozen_after_s=40, resume_at_s=10)
    assert outcome.buffering_pauses > 0
    # It stops only after enough real playback time, never early on wall time spent buffering.
    assert outcome.reason == StopReason.FROZEN
    assert capture.pause_calls > 0


def test_frozen_not_counted_while_paused_by_the_page(clock):
    class StuckPlayer(SimPlayer):
        def play(self) -> None:  # the page keeps pausing: the video never advances
            self.plays += 1

    player = StuckPlayer(clock, duration=600)
    capture = FakeCapture(picture=lambda n: STILL)
    outcome, _, _ = run(player, capture, abort_if_frozen_after_s=30, max_wall_extra_s=200, max_wall_factor=0)
    assert outcome.reason == StopReason.TOO_LONG


def test_no_audio_warns_once(clock):
    outcome, _, log = run(SimPlayer(clock, duration=60), FakeCapture(peak=0.0))
    assert [w for w in log.warnings if "No audio" in w] == [
        "No audio detected. Check that Chrome is routed to CABLE Input."
    ]
    assert outcome.reason == StopReason.ENDED


def test_audio_check_feature_off_or_unavailable(clock):
    _, _, log = run(SimPlayer(clock, duration=60), FakeCapture(peak=0.0), audio_check=False)
    assert not any("No audio" in w for w in log.warnings)
    _, _, log = run(SimPlayer(FakeClock(), duration=60), FakeCapture(peak=None))
    assert not any("No audio" in w for w in log.warnings)


def test_page_pausing_by_itself_is_replayed(clock):
    player = SimPlayer(clock, duration=60, self_pauses=[10, 20, 30])
    outcome, _, _ = run(player)
    assert outcome.reason == StopReason.ENDED
    assert player.plays >= 4


def test_replays_are_capped_without_progress(clock):
    class StuckPlayer(SimPlayer):
        def play(self) -> None:  # the page refuses to play
            self.plays += 1

    player = StuckPlayer(clock, duration=60)
    outcome, _, log = run(player, max_wall_extra_s=0, max_wall_factor=1)
    assert player.plays == 1 + MAX_REPLAYS  # initial play + capped replays
    assert any("isn't advancing" in w for w in log.warnings)
    assert outcome.reason == StopReason.TOO_LONG


def test_low_quality_warning(clock):
    player = SimPlayer(clock, duration=60, height=1080)
    _, _, log = run(player, target_height=2160)
    assert any("Quality lower than expected (2160x1080)" in w for w in log.warnings)


def test_limits():
    assert WatchConfig(duration=100).soft_limit() == 220
    assert WatchConfig(duration=math.nan).soft_limit() == 4 * 3600
    assert WatchConfig(duration=100).wall_limit() == 100 * 3 + 600
    assert WatchConfig(duration=math.inf).wall_limit() == 4 * 3600 + 600
    assert WatchConfig(duration=100, test_mode=True).wall_limit() == 30 + 600


def test_obs_lost_when_the_recorder_stops_recording(clock):
    player = SimPlayer(clock, duration=600)
    outcome, capture, _ = run(player, FakeCapture(alive=[True, False]))
    assert outcome.reason == StopReason.OBS_LOST
    assert capture.alive_checks == 2
    assert player.t < 80


def test_obs_lost_after_two_unknown_checks_in_a_row(clock):
    outcome, capture, _ = run(SimPlayer(clock, duration=600), FakeCapture(alive=[None, None]))
    assert outcome.reason == StopReason.OBS_LOST
    assert capture.alive_checks == 2


def test_one_unknown_liveness_check_is_forgiven(clock):
    outcome, _, _ = run(SimPlayer(clock, duration=100), FakeCapture(alive=[None, True, None, True]))
    assert outcome.reason == StopReason.ENDED


def test_liveness_check_exception_counts_as_unknown(clock):
    class Boom(FakeCapture):
        def recording(self) -> bool | None:
            raise RuntimeError("socket closed")

    outcome, _, _ = run(SimPlayer(clock, duration=600), Boom())
    assert outcome.reason == StopReason.OBS_LOST


def test_obs_lost_is_detected_while_buffering(clock):
    player = SimPlayer(clock, duration=600, rate=0.0, initial_buffer=1)
    outcome, _, _ = run(player, FakeCapture(alive=[False]), max_stall_s=10**6)
    assert outcome.reason == StopReason.OBS_LOST


def test_liveness_is_checked_only_every_30_seconds(clock):
    start = clock.now
    outcome, capture, _ = run(SimPlayer(clock, duration=100))
    assert outcome.reason == StopReason.ENDED
    assert capture.alive_checks == int((clock.now - start) // 30)
