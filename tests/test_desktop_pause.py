"""Pausing the recording while Chrome isn't on the current virtual desktop."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from test_monitor import FakeCapture, FakeClock, Log, SimPlayer
from test_record_one import FakePage, patched, run  # noqa: F401 (patched is a fixture)

from vrec import recorder, vdesktop
from vrec.features import FeatureSet
from vrec.monitor import AWAY_MESSAGE, StopReason, WatchConfig, watch


@dataclass
class EventCapture(FakeCapture):
    events: list[str] = field(default_factory=list)

    def pause(self) -> None:
        self.events.append("pause")
        super().pause()

    def resume(self) -> None:
        self.events.append("resume")
        super().resume()


def away_between(clock: FakeClock, *spans: tuple[float, float]) -> Any:
    """A `visible` callable that says False during the given spans (seconds after the clock's start)."""
    start = clock.now

    def visible() -> bool | None:
        return not any(a <= clock.now - start < b for a, b in spans)

    return visible


def go(player: SimPlayer, capture: FakeCapture, visible: Any, **config: Any):
    log = Log()
    player.play()
    outcome = watch(
        player,
        capture,
        WatchConfig(duration=player.duration, **config),
        log.output(),
        player.clock,
        visible=visible,
    )
    return outcome, log


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def test_pauses_and_resumes_with_the_desktop(clock):
    player, capture = SimPlayer(clock, duration=60, rate=10), EventCapture()
    outcome, log = go(player, capture, away_between(clock, (10, 30)))
    assert outcome.reason == StopReason.ENDED
    assert capture.events == ["pause", "resume"]
    assert outcome.desktop_pauses == 1
    assert log.warnings.count(AWAY_MESSAGE) == 1
    assert capture.paused is False and player.paused is False


def test_time_away_is_excluded_from_the_playing_time(clock):
    # 30 s of test recording must still be 30 s of video when 20 s were spent on another desktop.
    start = clock.now
    player, capture = SimPlayer(clock, duration=600, rate=10), EventCapture()
    outcome, _ = go(player, capture, away_between(clock, (10, 30)), test_mode=True, test_duration_s=30)
    assert outcome.reason == StopReason.TEST_DONE
    assert player.t == pytest.approx(30, abs=3)
    assert clock.now - start == pytest.approx(50, abs=4)


def test_away_does_not_trigger_stall_frozen_or_black_stops(clock):
    # Far longer than the black (60 s) and frozen (20 s) limits: coming back must not count the time away.
    player = SimPlayer(clock, duration=60, rate=10)
    capture = EventCapture(black=[True, True, True, False])
    outcome, log = go(
        player,
        capture,
        away_between(clock, (5, 400)),
        abort_if_black_after_s=60,
        abort_if_frozen_after_s=20,
        max_wall_extra_s=10_000,
    )
    assert outcome.reason == StopReason.ENDED
    assert not any("isn't advancing" in w for w in log.warnings)


def test_no_double_pause_or_resume_with_buffering(clock):
    # A slow connection: buffering pauses happen all along, and the user leaves in the middle of them.
    player = SimPlayer(clock, duration=120, rate=0.6, initial_buffer=3)
    capture = EventCapture()
    outcome, _ = go(player, capture, away_between(clock, (6, 20), (45, 60), (80, 95)))
    assert outcome.reason == StopReason.ENDED
    assert outcome.buffering_pauses >= 1 and outcome.desktop_pauses >= 1
    for first, second in zip(capture.events, capture.events[1:], strict=False):
        assert first != second, capture.events  # strictly alternating pause / resume
    assert capture.events[-1] == "resume"
    assert capture.paused is False


def test_leaving_while_buffering_keeps_the_video_paused_until_it_is_buffered(clock):
    player = SimPlayer(clock, duration=120, rate=0.6, initial_buffer=1)
    capture = EventCapture()
    # Away right from the first check, longer than the buffer plateau: the video must not resume early.
    outcome, _ = go(player, capture, away_between(clock, (2, 30)))
    assert outcome.reason == StopReason.ENDED and outcome.buffering_pauses >= 1
    assert capture.events[:2] == ["pause", "resume"]


def test_unknown_is_treated_as_visible(clock):
    player, capture = SimPlayer(clock, duration=60, rate=10), EventCapture()
    outcome, log = go(player, capture, lambda: None)
    assert outcome.reason == StopReason.ENDED
    assert capture.events == [] and outcome.desktop_pauses == 0 and AWAY_MESSAGE not in log.warnings


def test_a_failing_check_is_treated_as_visible(clock):
    def boom() -> bool | None:
        raise OSError("COM")

    player, capture = SimPlayer(clock, duration=60, rate=10), EventCapture()
    outcome, _ = go(player, capture, boom)
    assert outcome.reason == StopReason.ENDED and capture.events == []


def test_obs_refusing_to_pause_gives_the_feature_up(clock):
    player, capture = SimPlayer(clock, duration=60, rate=10), EventCapture(refuse_pause=True)
    outcome, log = go(player, capture, away_between(clock, (10, 30)))
    assert outcome.reason == StopReason.ENDED
    assert capture.events == ["pause"] and outcome.desktop_pauses == 0
    assert any("OBS refuses to pause" in w for w in log.warnings)


def test_without_a_check_nothing_changes(clock):
    player, capture = SimPlayer(clock, duration=60, rate=10), EventCapture()
    outcome, _ = go(player, capture, None)
    assert outcome.reason == StopReason.ENDED and capture.events == []


# ---------- record_one wiring ----------


def test_record_one_pauses_obs_while_away(tmp_path: Path, patched, monkeypatch, capsys):  # noqa: F811
    page = FakePage(duration=10)
    monkeypatch.setattr(vdesktop, "find_chrome_hwnd", lambda p, allow_marker=False: 99)
    monkeypatch.setattr(
        vdesktop, "on_current_desktop", lambda hwnd: not (hwnd == 99 and 1004 <= page.now < 1008)
    )
    result, client = run(tmp_path, page)
    assert result.reason == "ended" and result.desktop_pauses == 1
    assert client.events == ["start", "pause", "resume", "stop"]
    assert "pause(s) while Chrome wasn't on the current virtual desktop" in capsys.readouterr().out


def test_record_one_inactive_without_a_window(tmp_path: Path, patched, monkeypatch):  # noqa: F811
    asked: list[int] = []
    monkeypatch.setattr(vdesktop, "find_chrome_hwnd", lambda p, allow_marker=False: None)
    monkeypatch.setattr(vdesktop, "on_current_desktop", lambda hwnd: asked.append(hwnd) or False)
    result, client = run(tmp_path, FakePage(duration=10))
    assert result.desktop_pauses == 0 and asked == []
    assert client.events == ["start", "stop"]


def test_record_one_feature_off_does_not_even_look(tmp_path: Path, patched, monkeypatch):  # noqa: F811
    looked: list[Any] = []
    monkeypatch.setattr(vdesktop, "find_chrome_hwnd", lambda p, allow_marker=False: looked.append(p) or 99)
    monkeypatch.setattr(vdesktop, "on_current_desktop", lambda hwnd: False)
    result, client = run(tmp_path, FakePage(duration=10), FeatureSet({"desktop_pause": False}))
    assert looked == [] and client.events == ["start", "stop"]
    assert recorder._desktop_probe  # still available for the default case
