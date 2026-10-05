"""The fixed status bar: rendering, escape sequences, restoration, the log and the gating."""

from __future__ import annotations

import atexit
import io
import sys
import time
from pathlib import Path

import pytest
from test_app import make_batch, make_result
from test_desktop_pause import EventCapture, away_between
from test_monitor import FakeClock, SimPlayer

from vrec import app, logs, statusbar
from vrec.console import ProgressLine
from vrec.features import FeatureSet, feature_names
from vrec.monitor import Output, WatchConfig, watch
from vrec.statusbar import BatchCounts, StatusBar

ESC = "\x1b"


class Size:
    def __init__(self, cols: int = 120, rows: int = 30) -> None:
        self.value = (cols, rows)

    def __call__(self) -> tuple[int, int]:
        return self.value


class Ticker:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_bar(
    hotkeys: bool = True,
    stopping: bool = False,
    unicode: bool = True,
    size: Size | None = None,
    clock: Ticker | None = None,
) -> tuple[StatusBar, io.StringIO, Size, Ticker]:
    out, size, clock = io.StringIO(), size or Size(), clock or Ticker()
    bar = StatusBar(out, hotkeys, lambda: stopping, size, clock, unicode)
    return bar, out, size, clock


def started(**kwargs):
    bar, out, size, clock = make_bar(**kwargs)
    assert bar.start(59)
    bar.begin_video(3, 59, BatchCounts(ok=2, durations=[100.0, 200.0]))
    return bar, out, size, clock


# ---------- rendering ----------


def test_playing_line():
    bar, *_ = started()
    bar.set_state("playing", t=1409, d=2767, res=(3840, 2160))
    one, two = bar.lines()
    assert one.startswith(
        "▶ 3/59  Playing 50.9% (23:29 / 46:07) │ 3840x2160 │ Batch: 2 OK, 0 failed, 0 skipped │ ETA "
    )
    assert two == "[P] Pause  [S] Skip  [R] Restart  [Q] Stop after this video  [H] Help"


@pytest.mark.parametrize(
    ("kind", "extra", "expected"),
    [
        ("paused", {"t": 5, "d": 60}, "⏸ 3/59  Paused"),
        ("buffering", {"t": 5, "d": 60, "buffer": 2.04}, "⏳ 3/59  Buffering 2.0 s"),
        ("away", {"t": 5, "d": 60}, "! 3/59  Away (Chrome not on this desktop)"),
        ("loading", {}, "▶ 3/59  Preparing"),
    ],
)
def test_other_states(kind, extra, expected):
    bar, *_ = started()
    bar.set_state(kind, **extra)
    assert bar.lines()[0].startswith(expected)


def test_stopping_after_this_video_when_q_is_armed():
    bar, *_ = started(stopping=True)
    bar.set_state("playing", t=1, d=60)
    one, two = bar.lines()
    assert "Playing 1.7% (0:01 / 1:00) │ Stopping after this video │ Batch" in one
    assert "[Q] Cancel the stop" in two


def test_without_hotkeys_the_second_line_is_a_hint():
    bar, *_ = started(hotkeys=False)
    assert bar.lines()[1] == "Ctrl+C to stop"


def test_ascii_fallback_has_no_unicode():
    bar, *_ = started(unicode=False)
    bar.set_state("buffering", t=1, d=60, buffer=1.0)
    one, two = bar.lines()
    assert one.startswith("... 3/59  Buffering 1.0 s | Batch")
    assert (one + two).isascii()


def test_unicode_is_chosen_from_the_stream_encoding():
    class Stream(io.StringIO):
        encoding = "cp1252"  # can't encode the symbols

    assert StatusBar(Stream(), True, size=Size(), clock=Ticker())._sym is not statusbar._UNICODE
    Stream.encoding = "utf-8"
    assert StatusBar(Stream(), True, size=Size(), clock=Ticker())._sym is statusbar._UNICODE
    assert statusbar.can_encode("▶", None) is False
    assert statusbar.can_encode("abc", "nonsense-codec") is False


def test_lines_are_truncated_to_the_width_on_the_terminal():
    bar, out, size, _ = make_bar(size=Size(40, 30))
    bar.start(5)
    out.truncate(0)
    out.seek(0)
    bar.begin_video(1, 5, BatchCounts())
    painted = out.getvalue()
    assert f"{ESC}[29;1H" in painted and f"{ESC}[30;1H" in painted
    first = painted.split(f"{ESC}[29;1H")[1].split(f"{ESC}[K")[0]
    second = painted.split(f"{ESC}[30;1H")[1].split(f"{ESC}[K")[0]
    assert len(first) == 39 and len(second) == 39


# ---------- ETA ----------


def test_estimate_end_cases():
    assert statusbar.estimate_end(0, None, 3, [10]) is None  # current length unknown
    assert statusbar.estimate_end(0, 30, 0, []) == 30  # last video: only what is left of it
    assert statusbar.estimate_end(0, 30, 2, []) is None  # no data for the others
    assert statusbar.estimate_end(0, 30, 2, [100, 200]) == 330  # 30 + 2 * average(150)


def test_eta_shown_as_a_clock_time_or_unknown():
    clock = Ticker(time.mktime((2026, 1, 1, 20, 0, 0, 0, 0, -1)))
    bar, *_ = started(clock=clock)
    assert "ETA ?" in bar.lines()[0]  # duration of the current video not known yet
    # 40 s left + 56 videos * average of (100, 200, 100) = 133.33 s -> 7506 s later
    bar.set_state("playing", t=60, d=100)
    assert bar.lines()[0].endswith("ETA 22:05")


def test_eta_on_the_last_video_needs_no_average():
    clock = Ticker(time.mktime((2026, 1, 1, 20, 0, 0, 0, 0, -1)))
    bar, *_ = make_bar(clock=clock)
    bar.start(1)
    bar.begin_video(1, 1, BatchCounts())
    bar.set_state("playing", t=0, d=600)
    assert bar.lines()[0].endswith("ETA 20:10")


# ---------- escape sequences ----------


def test_start_reserves_the_last_two_lines_and_stop_gives_them_back():
    bar, out, _, _ = make_bar(size=Size(100, 30))
    assert bar.start(3)
    text = out.getvalue()
    assert text.startswith(f"\n\n{ESC}[2A{ESC}7{ESC}[1;28r{ESC}8")
    assert f"{ESC}[29;1H" in text and f"{ESC}[30;1H" in text
    out.truncate(0)
    out.seek(0)
    bar.stop()
    assert out.getvalue() == f"{ESC}7{ESC}[r{ESC}[29;1H{ESC}[2K{ESC}[30;1H{ESC}[2K{ESC}8"
    assert not bar.active
    out.truncate(0)
    bar.stop()  # idempotent
    bar.refresh(force=True)  # and silent once stopped
    assert out.getvalue() == ""


def test_too_small_a_terminal_is_left_alone():
    bar, out, *_ = make_bar(size=Size(100, 5))
    assert not bar.start(3)
    assert out.getvalue() == "" and not bar.active
    bar, out, *_ = make_bar(size=Size(10, 30))
    assert not bar.start(3) and out.getvalue() == ""


def test_updates_are_throttled_unless_the_state_changes():
    bar, out, _, clock = started()
    bar.set_state("playing", t=1, d=60)
    out.truncate(0)
    out.seek(0)
    bar.set_state("playing", t=2, d=60)
    assert out.getvalue() == ""  # same state, too soon
    clock.now += 0.3
    bar.set_state("playing", t=3, d=60)
    assert "Playing 5.0%" in out.getvalue()
    out.truncate(0)
    out.seek(0)
    bar.set_state("paused", t=3, d=60)  # a new kind of state is drawn at once
    assert "Paused" in out.getvalue()


def test_resize_reapplies_the_scroll_region_and_redraws():
    bar, out, size, _ = started()
    out.truncate(0)
    out.seek(0)
    size.value = (80, 20)
    bar.refresh()
    text = out.getvalue()
    assert f"{ESC}[r" in text and f"{ESC}[1;18r" in text
    assert f"{ESC}[19;1H" in text and f"{ESC}[20;1H" in text
    out.truncate(0)
    out.seek(0)
    bar.stop()
    assert f"{ESC}[19;1H{ESC}[2K" in out.getvalue()


def test_a_broken_console_never_raises():
    class Broken(io.StringIO):
        def write(self, s: str) -> int:
            raise OSError("gone")

    bar = StatusBar(Broken(), True, size=Size(), clock=Ticker(), use_unicode=True)
    assert bar.start(2)
    bar.set_state("paused")
    bar.stop()


# ---------- restoration ----------


def test_running_restores_on_exception_and_on_ctrl_c():
    for error in (RuntimeError("boom"), KeyboardInterrupt()):
        bar, out, *_ = make_bar()
        with pytest.raises(type(error)), bar.running(3):
            assert bar.active
            raise error
        assert not bar.active
        assert out.getvalue().endswith(f"{ESC}[2K{ESC}8")
        assert f"{ESC}[r" in out.getvalue()


def test_running_stops_normally_too():
    bar, out, *_ = make_bar()
    with bar.running(3):
        pass
    assert not bar.active and f"{ESC}[r" in out.getvalue()


def test_an_atexit_safety_net_is_registered_and_removed(monkeypatch):
    registered: list[object] = []
    monkeypatch.setattr(atexit, "register", lambda fn: registered.append(fn))
    monkeypatch.setattr(atexit, "unregister", lambda fn: registered.remove(fn))
    bar, out, *_ = make_bar()
    bar.start(2)
    assert registered == [bar.stop]
    bar.stop()
    assert registered == []


def test_batch_restores_the_terminal_when_a_video_crashes(tmp_path):
    bar, out, *_ = make_bar()
    batch = make_batch(tmp_path, bar=bar)

    def record(*args, **kwargs):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt), bar.running(1):
        app._record_batch(batch, [("https://x/1", "A")], record=record)
    assert not bar.active and f"{ESC}[r" in out.getvalue()


# ---------- fed by the batch and the watch loop ----------


def test_batch_counts_and_the_bar_kwarg(tmp_path, capsys):
    bar, out, *_ = make_bar()
    batch = make_batch(tmp_path, bar=bar)
    seen: list[object] = []

    def record(page, client, meter, scene, settings, features, number, total, url, title, test, h, **kw):
        seen.append(kw["bar"])
        return make_result(number=number, reason="skipped" if number == 2 else "ended", duration_s=60.0)

    bar.start(2)
    app._record_batch(batch, [("https://x/1", "A"), ("https://x/2", "B")], record=record)
    assert seen == [bar, bar]
    counts = app._batch_counts(batch)
    assert (counts.ok, counts.skipped, counts.failed) == (1, 1, 0)
    assert counts.durations == [60.0, 60.0]
    assert "Batch: 1 OK, 0 failed, 1 skipped" in bar.lines()[0]
    assert "Keys:" not in capsys.readouterr().out


def test_no_bar_means_no_bar_kwarg(tmp_path):
    batch = make_batch(tmp_path)
    kwargs: list[dict[str, object]] = []

    def record(page, client, meter, scene, settings, features, number, total, url, title, test, h, **kw):
        kwargs.append(kw)
        return make_result()

    app._record_batch(batch, [("https://x/1", "A")], record=record)
    assert "bar" not in kwargs[0]


def test_the_watch_loop_feeds_the_bar_states():
    clock = FakeClock()
    player, capture = SimPlayer(clock, duration=60, rate=10), EventCapture()
    bar, out, _, _ = make_bar(clock=clock)
    bar.start(1)
    kinds: list[str] = []
    real = bar.set_state

    def spy(kind, **kw):
        kinds.append(kind)
        real(kind, **kw)

    player.play()
    watch(
        player,
        capture,
        WatchConfig(duration=60),
        Output(warn=lambda m: None, progress=lambda m: None, end_progress=lambda: None, status=spy),
        clock,
        visible=away_between(clock, (10, 20)),
    )
    assert "playing" in kinds and "away" in kinds
    assert "3840x2160" in bar.lines()[0] or "x2160" in bar.lines()[0]


# ---------- the log stays plain ----------


def test_log_has_no_escape_codes_and_keeps_progress_lines(tmp_path, monkeypatch):
    console = io.StringIO()
    monkeypatch.setattr(sys, "stdout", console)
    with logs.capture(tmp_path, ["vrec"], FeatureSet()) as run_log:
        bar = StatusBar(
            getattr(sys.stdout, "raw", sys.stdout), True, size=Size(), clock=Ticker(), use_unicode=True
        )
        with bar.running(2):
            bar.begin_video(1, 2, BatchCounts())
            line = ProgressLine(bar)
            print("[1/2] A")
            line.show("Playing  10.0%  (0:06 / 1:00)")
            bar.set_state("playing", t=6, d=60)
            line.show("Playing  50.0%  (0:30 / 1:00)")
            line.end()
            print("   -> OK")
        print("done")
    text = run_log.path.read_text(encoding="utf-8")  # type: ignore[union-attr]
    assert ESC not in text and "Batch:" not in text
    body = text.split("-" * 60 + "\n", 1)[1]
    assert body == "[1/2] A\n   Playing  50.0%  (0:30 / 1:00)\n   -> OK\ndone\n"
    shown = console.getvalue()
    assert ESC in shown  # the bar did reach the console...
    assert "Playing  50.0%" not in shown  # ...which does not get the in-place line


def test_progress_line_is_exactly_as_before_without_an_active_bar(capsys):
    for bar in (None, StatusBar(io.StringIO(), True, size=Size(), clock=Ticker())):  # inactive
        line = ProgressLine(bar)
        line.show("Playing  10.0%")
        line.show("Buffering")
        line.end()
        line.end()
        assert capsys.readouterr().out == "\r   Playing  10.0%\r   Buffering     \n"


# ---------- gating ----------


def test_the_feature_exists_and_is_on_by_default():
    assert "status_bar" in feature_names()
    assert FeatureSet().enabled("status_bar")


def test_available_gating(monkeypatch):
    monkeypatch.delenv(statusbar.SCHEDULED_ENV, raising=False)
    assert statusbar.available(True, isatty=True)
    assert not statusbar.available(False, isatty=True)  # feature off
    assert not statusbar.available(True, isatty=False)  # output redirected
    assert not statusbar.available(True, isatty=True, scheduled=True)
    monkeypatch.setenv(statusbar.SCHEDULED_ENV, "1")
    assert not statusbar.available(True, isatty=True)
    monkeypatch.delenv(statusbar.SCHEDULED_ENV)
    monkeypatch.setattr(sys, "stdout", io.StringIO())  # not a tty
    assert not statusbar.available(True)


def test_make_bar_needs_vt_support(monkeypatch):
    monkeypatch.setattr(statusbar, "available", lambda on: on)
    monkeypatch.setattr(statusbar, "enable_vt", lambda: False)
    assert statusbar.make_bar(True, True) is None
    monkeypatch.setattr(statusbar, "enable_vt", lambda: True)
    assert statusbar.make_bar(False, True) is None
    assert isinstance(statusbar.make_bar(True, True), StatusBar)


def test_enable_vt_is_false_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert statusbar.enable_vt() is False


def test_run_does_not_make_a_bar_in_a_test_run(tmp_path, monkeypatch):
    # Under pytest stdout is not a terminal: the real gate says no.
    monkeypatch.delenv(statusbar.SCHEDULED_ENV, raising=False)
    assert statusbar.make_bar(True, True) is None


def test_no_bar_changes_the_hotkeys_line(tmp_path, capsys):
    from test_hotkeys import controlled_batch
    from test_hotkeys import make_record as hotkeys_record

    batch, _ = controlled_batch(tmp_path)
    app._record_batch(batch, [("https://x/1", "A")], record=hotkeys_record([make_result()]))
    assert "Keys: P pause" in capsys.readouterr().out  # inactive bar: today's line
    assert Path(tmp_path).exists()
