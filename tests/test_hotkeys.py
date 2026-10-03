"""Keyboard controls: the reader, the recording loop with a fake key source, and the batch."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_app import make_batch, make_result
from test_desktop_pause import EventCapture, away_between
from test_monitor import FakeClock, SimPlayer
from test_record_one import patched, run_with_outcome  # noqa: F401 (patched is a fixture)

from vrec import app, hotkeys, naming, recorder, schedule
from vrec.monitor import Output, StopReason, WatchConfig, watch
from vrec.playlist import url_key

# ---------- the reader (fake msvcrt) ----------


class FakeMsvcrt:
    def __init__(self, chars: str) -> None:
        self.chars = list(chars)

    def kbhit(self) -> bool:
        return bool(self.chars)

    def getwch(self) -> str:
        return self.chars.pop(0)


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch):
    def make(chars: str) -> tuple[hotkeys.ConsoleReader, FakeMsvcrt]:
        fake = FakeMsvcrt(chars)
        monkeypatch.setitem(sys.modules, "msvcrt", fake)
        return hotkeys.ConsoleReader(), fake

    return make


def test_reader_maps_keys_in_any_case(reader):
    r, _ = reader("pPsSrRqQhH")
    assert [r.poll() for _ in range(10)] == [
        hotkeys.PAUSE,
        hotkeys.PAUSE,
        hotkeys.SKIP,
        hotkeys.SKIP,
        hotkeys.RESTART,
        hotkeys.RESTART,
        hotkeys.QUIT,
        hotkeys.QUIT,
        hotkeys.HELP,
        hotkeys.HELP,
    ]
    assert r.poll() is None


def test_reader_never_blocks_and_ignores_other_keys(reader):
    r, fake = reader("xyz\r 1")
    assert r.poll() is None
    assert fake.chars == []


@pytest.mark.parametrize("prefix", ["\x00", "\xe0"])
def test_reader_swallows_special_key_sequences(reader, prefix):
    # An arrow key is two characters; its second one (here "p", "s") must not act as a command.
    r, fake = reader(f"{prefix}p{prefix}sq")
    assert r.poll() == hotkeys.QUIT
    assert fake.chars == []


def test_reader_flush_drops_pending_keys(reader):
    r, fake = reader("p\r\xe0Hs")
    r.flush()
    assert fake.chars == [] and r.poll() is None


def test_null_reader_has_nothing_pending():
    r = hotkeys.NullReader()
    r.flush()
    assert r.poll() is None


# ---------- when keys are active ----------


def test_available_needs_feature_a_tty_and_no_scheduled_run():
    assert hotkeys.available(True, isatty=True, scheduled=False)
    assert not hotkeys.available(False, isatty=True, scheduled=False)
    assert not hotkeys.available(True, isatty=False, scheduled=False)
    assert not hotkeys.available(True, isatty=True, scheduled=True)


def test_make_controls_inactive_cases(monkeypatch):
    monkeypatch.setitem(sys.modules, "msvcrt", FakeMsvcrt(""))
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.delenv(hotkeys.SCHEDULED_ENV, raising=False)
    assert hotkeys.make_controls(True) is not None
    assert hotkeys.make_controls(False) is None  # feature off
    monkeypatch.setenv(hotkeys.SCHEDULED_ENV, "1")
    assert hotkeys.make_controls(True) is None  # scheduled run
    monkeypatch.delenv(hotkeys.SCHEDULED_ENV)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: False))
    assert hotkeys.make_controls(True) is None  # not a console


def test_make_controls_drops_keys_typed_before_the_batch(monkeypatch):
    fake = FakeMsvcrt("\r")
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.delenv(hotkeys.SCHEDULED_ENV, raising=False)
    assert hotkeys.make_controls(True) is not None
    assert fake.chars == []


def test_the_scheduled_launcher_marks_the_run():
    script = schedule._launcher_script(Path("C:/vrec"), "vrec --all")
    assert "set VREC_SCHEDULED=1" in script


def test_controls_toggle_quit_and_close_the_progress_line_first(capsys):
    events: list[str] = []

    class Keys:
        def __init__(self) -> None:
            self.queue = [hotkeys.QUIT, hotkeys.QUIT, hotkeys.HELP, hotkeys.PAUSE]

        def poll(self) -> str | None:
            return self.queue.pop(0) if self.queue else None

        def flush(self) -> None:
            pass

    controls = hotkeys.Controls(Keys())
    assert controls.poll(lambda: events.append("end")) is None
    assert controls.quit_requested and events == ["end"]
    assert hotkeys.STOP_MESSAGE in capsys.readouterr().out
    controls.poll()
    assert not controls.quit_requested
    assert hotkeys.CANCEL_MESSAGE in capsys.readouterr().out
    controls.poll()
    assert "S  skip this video" in capsys.readouterr().out
    assert controls.poll() == hotkeys.PAUSE


# ---------- the recording loop ----------


class Notes:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warnings: list[str] = []

    def output(self) -> Output:
        return Output(
            warn=self.warnings.append,
            progress=lambda _t: None,
            end_progress=lambda: None,
            info=self.infos.append,
        )


def keys_at(clock: FakeClock, *presses: tuple[float, str]) -> Callable[[], str | None]:
    """A key source: each command is delivered once, at its time (seconds after the clock's start)."""
    start = clock.now
    pending = list(presses)

    def keys() -> str | None:
        if pending and clock.now - start >= pending[0][0]:
            return pending.pop(0)[1]
        return None

    return keys


def go(player: SimPlayer, capture: EventCapture, keys: Any, visible: Any = None, **config: Any):
    notes = Notes()
    player.play()
    outcome = watch(
        player,
        capture,
        WatchConfig(duration=player.duration, **config),
        notes.output(),
        player.clock,
        visible=visible,
        keys=keys,
    )
    return outcome, notes


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def alternates(events: list[str]) -> bool:
    return events == ["pause", "resume"] * (len(events) // 2)


def test_p_pauses_and_resumes_video_and_recording(clock):
    player, capture = SimPlayer(clock, duration=60, rate=10), EventCapture()
    keys = keys_at(clock, (10, hotkeys.PAUSE), (30, hotkeys.PAUSE))
    outcome, notes = go(player, capture, keys)
    assert outcome.reason == StopReason.ENDED
    assert capture.events == ["pause", "resume"]
    assert notes.infos == ["Paused (P to resume).", "Resumed."]
    assert capture.paused is False and player.paused is False


def test_time_paused_is_excluded_from_every_timer(clock):
    # 30 s of test recording is still 30 s of video after 40 s of pause, and the 10 s wall cap
    # (extra time 10 s) isn't hit by the pause.
    start = clock.now
    player, capture = SimPlayer(clock, duration=600, rate=10), EventCapture()
    keys = keys_at(clock, (10, hotkeys.PAUSE), (50, hotkeys.PAUSE))
    outcome, _ = go(
        player,
        capture,
        keys,
        test_mode=True,
        test_duration_s=30,
        max_wall_extra_s=20,
        abort_if_black_after_s=5,
        abort_if_frozen_after_s=5,
    )
    assert outcome.reason == StopReason.TEST_DONE
    assert player.t == pytest.approx(30, abs=3)
    assert clock.now - start == pytest.approx(70, abs=4)


def test_p_during_a_desktop_pause_resumes_only_when_both_are_over(clock):
    player, capture = SimPlayer(clock, duration=90, rate=10), EventCapture()
    # Away 10-30; P pressed at 15 (while away) and again at 40 (after coming back).
    keys = keys_at(clock, (15, hotkeys.PAUSE), (40, hotkeys.PAUSE))
    outcome, _ = go(player, capture, keys, visible=away_between(clock, (10, 30)))
    assert outcome.reason == StopReason.ENDED
    assert capture.events == ["pause", "resume"]  # one pause, one resume: no doubles
    assert player.paused is False and capture.paused is False
    assert outcome.desktop_pauses == 1


def test_resuming_with_p_while_still_away_keeps_the_recording_paused(clock):
    player, capture = SimPlayer(clock, duration=90, rate=10), EventCapture()
    keys = keys_at(clock, (5, hotkeys.PAUSE), (15, hotkeys.PAUSE))
    outcome, _ = go(player, capture, keys, visible=away_between(clock, (10, 30)))
    assert outcome.reason == StopReason.ENDED
    assert capture.events == ["pause", "resume"]
    assert capture.resume_calls == 1 and player.pauses == 1


def test_p_during_buffering_never_double_pauses_or_resumes(clock):
    player = SimPlayer(clock, duration=120, rate=0.6, initial_buffer=3)
    capture = EventCapture()
    keys = keys_at(clock, (8, hotkeys.PAUSE), (40, hotkeys.PAUSE), (50, hotkeys.PAUSE), (70, hotkeys.PAUSE))
    outcome, _ = go(player, capture, keys)
    assert outcome.reason == StopReason.ENDED
    assert outcome.buffering_pauses >= 1
    assert alternates(capture.events)
    assert capture.paused is False and player.paused is False


def test_obs_refusing_to_pause_cancels_the_manual_pause(clock):
    player, capture = SimPlayer(clock, duration=60, rate=10), EventCapture(refuse_pause=True)
    outcome, notes = go(player, capture, keys_at(clock, (10, hotkeys.PAUSE)))
    assert outcome.reason == StopReason.ENDED
    assert notes.warnings == ["OBS refuses to pause: can't pause the recording."]
    assert notes.infos == [] and player.paused is False


def test_s_stops_with_the_skip_reason(clock):
    player, capture = SimPlayer(clock, duration=600, rate=10), EventCapture()
    outcome, _ = go(player, capture, keys_at(clock, (10, hotkeys.SKIP)))
    assert outcome.reason == StopReason.SKIPPED
    assert player.t < 20


def test_r_stops_with_the_restart_reason(clock):
    player, capture = SimPlayer(clock, duration=600, rate=10), EventCapture()
    outcome, _ = go(player, capture, keys_at(clock, (10, hotkeys.RESTART)))
    assert outcome.reason == StopReason.RESTART


def test_a_broken_key_source_is_ignored(clock):
    def boom() -> str | None:
        raise OSError("no console")

    player, capture = SimPlayer(clock, duration=30, rate=10), EventCapture()
    outcome, _ = go(player, capture, boom)
    assert outcome.reason == StopReason.ENDED


# ---------- record_one: naming and deleting ----------


@pytest.mark.usefixtures("patched")
def test_skipped_recording_is_named_skipped(tmp_path, monkeypatch):
    result, _, _ = run_with_outcome(tmp_path, monkeypatch, StopReason.SKIPPED)
    assert result.file == tmp_path / "SKIPPED - My Video.mkv"
    assert result.file.exists()
    assert recorder.status_text(result) == "SKIPPED"
    assert naming.find_existing_recording(tmp_path, "My Video") is None


@pytest.mark.usefixtures("patched")
def test_restart_deletes_only_the_partial_file(tmp_path, monkeypatch):
    other = tmp_path / "My Video.mkv"  # an earlier, finished recording: must stay
    other.write_bytes(b"keep")
    result, _, _ = run_with_outcome(tmp_path, monkeypatch, StopReason.RESTART)
    assert result.file is None
    assert not (tmp_path / "2026-09-27 10-00-00.mkv").exists()
    assert other.read_bytes() == b"keep"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["My Video.mkv"]


@pytest.mark.usefixtures("patched")
@pytest.mark.parametrize("name", ["SKIPPED", "RESTART"])
def test_no_tail_wait_after_skip_or_restart(tmp_path, monkeypatch, name):
    _, _, page = run_with_outcome(tmp_path, monkeypatch, StopReason[name], tail_s=7)
    assert 7000 not in page.waits


def test_delete_recording_gives_up_with_false(tmp_path, monkeypatch):
    monkeypatch.setattr(naming.time, "sleep", lambda s: None)
    monkeypatch.setattr(Path, "unlink", lambda self, missing_ok=False: (_ for _ in ()).throw(OSError("busy")))
    assert naming.delete_recording(tmp_path / "x.mkv") is False


def test_skip_and_restart_are_not_incomplete_or_quality_retried():
    for reason in (StopReason.SKIPPED, StopReason.RESTART):
        assert reason not in recorder.INCOMPLETE_REASONS
        result = make_result(reason=reason.value, target_height=1080)
        assert recorder.lower_quality_retry_cap(result, False) == 0


# ---------- the batch ----------


class QueueReader:
    def __init__(self) -> None:
        self.queue: list[str] = []

    def poll(self) -> str | None:
        return self.queue.pop(0) if self.queue else None

    def flush(self) -> None:
        pass


def controlled_batch(tmp_path: Path, **overrides: Any):
    keyboard = QueueReader()
    batch = make_batch(tmp_path, controls=hotkeys.Controls(keyboard), **overrides)
    return batch, keyboard


def make_record(behaviors: list[Any], on_call: Callable[[int], None] = lambda n: None):
    calls: list[dict[str, Any]] = []
    remaining = list(behaviors)

    def record(page, client, meter, scene, settings, features, number, total, url, title, test, height, **kw):
        calls.append({"url": url, "max_height": height, **kw})
        on_call(len(calls))
        behavior = remaining.pop(0)
        if isinstance(behavior, BaseException):
            raise behavior
        return behavior

    record.calls = calls  # type: ignore[attr-defined]
    return record


def test_keys_line_is_printed_once_before_the_first_video(tmp_path, capsys):
    batch, _ = controlled_batch(tmp_path)
    record = make_record([make_result(), make_result()])
    app._record_batch(batch, [("https://x/1", "A"), ("https://x/2", "B")], record=record)
    out = capsys.readouterr().out
    assert out.count(hotkeys.KEYS_LINE) == 1
    assert out.index(hotkeys.KEYS_LINE) < out.index("->")
    assert all(call["controls"] is batch.controls for call in record.calls)


def test_no_keys_line_and_no_controls_without_hotkeys(tmp_path, capsys):
    batch = make_batch(tmp_path)
    record = make_record([make_result()])
    app._record_batch(batch, [("https://x/1", "A")], record=record)
    assert hotkeys.KEYS_LINE not in capsys.readouterr().out
    assert "controls" not in record.calls[0]


def test_skip_leaves_history_alone_and_is_not_a_failure(tmp_path, capsys):
    batch, _ = controlled_batch(tmp_path)
    skipped = make_result(reason=StopReason.SKIPPED.value, image_ok=False, audio_ok=None)
    record = make_record([skipped, make_result(title="B")])
    app._record_batch(batch, [("https://x/1", "A"), ("https://x/2", "B")], record=record)
    assert url_key("https://x/1") not in batch.videos_history
    assert url_key("https://x/2") in batch.videos_history
    assert "-> SKIPPED" in capsys.readouterr().out
    assert not batch.stopped_early
    assert app.exit_code(batch) == 0
    assert "1 skipped" in app.summary_line(batch.results, 0)


def test_skip_keeps_a_previous_status(tmp_path):
    batch, _ = controlled_batch(tmp_path)
    batch.videos_history[url_key("https://x/1")] = {"status": "failed", "title": "A"}
    record = make_record([make_result(reason=StopReason.SKIPPED.value)])
    app._record_batch(batch, [("https://x/1", "A")], record=record)
    assert batch.videos_history[url_key("https://x/1")]["status"] == "failed"


def test_skip_does_not_count_in_the_error_streak(tmp_path):
    batch, _ = controlled_batch(tmp_path)
    record = make_record(
        [RuntimeError("a"), make_result(reason=StopReason.SKIPPED.value), RuntimeError("b"), make_result()]
    )
    selection = [(f"https://x/{n}", f"V{n}") for n in range(4)]
    app._record_batch(batch, selection, record=record, chrome_alive=lambda b: True, obs_alive=lambda c: True)
    assert len(batch.results) == 4  # two errors around a skip never reach 3 in a row


def test_restart_records_the_same_video_again(tmp_path, capsys):
    batch, _ = controlled_batch(tmp_path)
    restart = make_result(reason=StopReason.RESTART.value)
    record = make_record([restart, restart, make_result()])
    app._record_batch(batch, [("https://x/1", "A")], record=record)
    assert [c["url"] for c in record.calls] == ["https://x/1"] * 3
    assert len(batch.results) == 1
    assert batch.videos_history[url_key("https://x/1")]["status"] == "done"
    assert capsys.readouterr().out.count("Restarting this video from the beginning...") == 2


def test_restart_is_not_the_quality_retry(tmp_path):
    batch, _ = controlled_batch(tmp_path)
    record = make_record([make_result(reason=StopReason.RESTART.value), make_result()])
    app._record_batch(batch, [("https://x/1", "A")], record=record)
    assert [c["max_height"] for c in record.calls] == [batch.settings.max_height] * 2


def test_q_stops_after_the_current_video(tmp_path, capsys):
    batch, keyboard = controlled_batch(tmp_path)
    record = make_record(
        [make_result(), make_result(), make_result()],
        on_call=lambda n: keyboard.queue.append(hotkeys.QUIT) if n == 1 else None,
    )
    selection = [(f"https://x/{n}", f"V{n}") for n in range(3)]
    app._record_batch(batch, selection, record=record)
    assert len(record.calls) == 1
    assert batch.stopped_early
    assert app.exit_code(batch) == 1
    assert "Stopping the batch as requested." in capsys.readouterr().out


def test_q_twice_cancels(tmp_path):
    batch, keyboard = controlled_batch(tmp_path)
    record = make_record(
        [make_result(), make_result()],
        on_call=lambda n: keyboard.queue.extend([hotkeys.QUIT, hotkeys.QUIT]) if n == 1 else None,
    )
    app._record_batch(batch, [("https://x/1", "A"), ("https://x/2", "B")], record=record)
    assert len(record.calls) == 2
    assert not batch.stopped_early


def test_q_on_the_last_video_is_not_an_early_stop(tmp_path):
    batch, keyboard = controlled_batch(tmp_path)
    record = make_record([make_result()], on_call=lambda n: keyboard.queue.append(hotkeys.QUIT))
    app._record_batch(batch, [("https://x/1", "A")], record=record)
    assert not batch.stopped_early
    assert app.exit_code(batch) == 0
