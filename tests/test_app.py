"""Tests for vrec.app: the batch loop and its steps, exercised with fakes (no Chrome/OBS)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vrec import app, history
from vrec.config import Paths, Settings
from vrec.errors import VrecError
from vrec.features import FeatureSet
from vrec.playlist import url_key
from vrec.recorder import RecordingResult, StopReason


class FakePage:
    def __init__(self, closed: bool = False) -> None:
        self._closed = closed
        self.waits: list[int] = []

    def is_closed(self) -> bool:
        return self._closed

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


class FakeBrowser:
    def __init__(self, connected: bool = True) -> None:
        self._connected = connected

    def is_connected(self) -> bool:
        return self._connected


class FakeClient:
    """Enough of obsws_python.ReqClient for stop_if_recording/_obs_alive/restore_mutes."""

    def __init__(self, output_active: bool = False, output_path: str = "", alive: bool = True) -> None:
        self.output_active = output_active
        self.output_path = output_path
        self.alive = alive
        self.stopped = False
        self.mutes_restored: dict[str, bool] = {}

    def get_record_status(self) -> SimpleNamespace:
        return SimpleNamespace(output_active=self.output_active)

    def stop_record(self) -> SimpleNamespace:
        self.stopped = True
        self.output_active = False
        return SimpleNamespace(output_path=self.output_path)

    def get_version(self) -> None:
        if not self.alive:
            raise RuntimeError("OBS gone")

    def set_input_mute(self, name: str, muted: bool) -> None:
        self.mutes_restored[name] = muted


def make_batch(tmp_path: Path, **overrides: object) -> app.Batch:
    paths = Paths(data_dir=tmp_path, config=tmp_path / "config.toml")
    kwargs: dict[str, object] = dict(
        paths=paths,
        settings=Settings(),
        features=FeatureSet(),
        test_mode=False,
        client=FakeClient(),
        page=FakePage(),
        browser=FakeBrowser(),
    )
    kwargs.update(overrides)
    return app.Batch(**kwargs)  # type: ignore[arg-type]


def make_record(behaviors: list[object]):
    """A `record` fake: pops one behavior per call (a RecordingResult, or an exception to raise)."""
    calls: list[dict[str, object]] = []
    remaining = list(behaviors)

    def record(
        page, client, meter, scene, settings, features, number, total, url, title, test_mode, max_height
    ):
        calls.append({"number": number, "url": url, "title": title, "max_height": max_height})
        behavior = remaining.pop(0)
        if isinstance(behavior, BaseException):
            raise behavior
        return behavior

    record.calls = calls  # type: ignore[attr-defined]
    return record


def make_result(**kwargs: object) -> RecordingResult:
    defaults = dict(number=1, title="Video", reason=StopReason.ENDED.value, image_ok=True, audio_ok=True)
    defaults.update(kwargs)
    return RecordingResult(**defaults)  # type: ignore[arg-type]


# ---------- _connect_obs (lot N: starts OBS itself via vrec.launcher) ----------


def test_connect_obs_uses_launcher_ensure_obs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = Paths(data_dir=tmp_path, config=tmp_path / "config.toml")
    settings = Settings()
    features = FeatureSet()
    client = FakeClient()
    calls: list[tuple[object, object, object]] = []

    def fake_ensure_obs(s: object, p: object, f: object) -> tuple[FakeClient, str]:
        calls.append((s, p, f))
        return client, "pw"

    monkeypatch.setattr(app.launcher, "ensure_obs", fake_ensure_obs)

    result_client, password = app._connect_obs(paths, settings, features, [], {})

    assert result_client is client
    assert password == "pw"
    assert calls == [(settings, paths, features)]


# ---------- _start_chrome_if_needed (lot O: starts Chrome itself via vrec.launcher) ----------


def test_start_chrome_if_needed_uses_launcher_ensure_chrome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch = make_batch(tmp_path)
    calls: list[tuple[object, object]] = []
    monkeypatch.setattr(
        app.launcher, "ensure_chrome", lambda settings, features: calls.append((settings, features))
    )

    app._start_chrome_if_needed(batch)

    assert calls == [(batch.settings, batch.features)]


# ---------- _record_batch ----------


def test_normal_batch_updates_history(tmp_path: Path) -> None:
    batch = make_batch(tmp_path)
    record = make_record([make_result(title="Video 1"), make_result(title="Video 2")])
    selection = [("https://x/1", "Video 1"), ("https://x/2", "Video 2")]

    app._record_batch(batch, selection, record=record)

    assert len(batch.results) == 2
    assert batch.videos_history[url_key("https://x/1")]["status"] == history.STATUS_DONE
    assert batch.videos_history[url_key("https://x/2")]["status"] == history.STATUS_DONE
    assert len(record.calls) == 2  # type: ignore[attr-defined]


def test_check_result_marks_review(tmp_path: Path) -> None:
    batch = make_batch(tmp_path)
    record = make_record([make_result(image_ok=False)])
    selection = [("https://x/1", "Video 1")]

    app._record_batch(batch, selection, record=record)

    entry = batch.videos_history[url_key("https://x/1")]
    assert entry["status"] == history.STATUS_REVIEW


def test_exception_stops_obs_and_renames_interrupted(tmp_path: Path) -> None:
    rec_file = tmp_path / "output.mkv"
    rec_file.write_bytes(b"data")
    client = FakeClient(output_active=True, output_path=str(rec_file))
    batch = make_batch(tmp_path, client=client)
    record = make_record([RuntimeError("boom")])
    selection = [("https://x/1", "My Video")]

    app._record_batch(batch, selection, record=record)

    assert client.stopped
    failed = batch.results[0]
    assert failed.file is not None
    assert failed.file.name == "INTERRUPTED - My Video.mkv"
    assert failed.file.exists()
    entry = batch.videos_history[url_key("https://x/1")]
    assert entry["status"] == history.STATUS_FAILED


def test_lost_chrome_stops_batch(tmp_path: Path) -> None:
    page = FakePage(closed=True)
    batch = make_batch(tmp_path, page=page)
    record = make_record([RuntimeError("boom"), make_result(), make_result()])
    selection = [("https://x/1", "V1"), ("https://x/2", "V2"), ("https://x/3", "V3")]

    app._record_batch(batch, selection, record=record)

    assert len(record.calls) == 1  # type: ignore[attr-defined]
    assert url_key("https://x/2") not in batch.videos_history
    assert url_key("https://x/3") not in batch.videos_history


def test_lost_obs_stops_batch(tmp_path: Path) -> None:
    client = FakeClient(alive=False)
    batch = make_batch(tmp_path, client=client)
    record = make_record([RuntimeError("boom"), make_result(), make_result()])
    selection = [("https://x/1", "V1"), ("https://x/2", "V2"), ("https://x/3", "V3")]

    app._record_batch(batch, selection, record=record)

    assert len(record.calls) == 1  # type: ignore[attr-defined]


def test_three_errors_in_a_row_stops(tmp_path: Path) -> None:
    batch = make_batch(tmp_path)
    record = make_record([RuntimeError("1"), RuntimeError("2"), RuntimeError("3"), make_result()])
    selection = [(f"https://x/{i}", f"V{i}") for i in range(1, 5)]

    app._record_batch(batch, selection, record=record)

    assert len(record.calls) == 3  # type: ignore[attr-defined]


def test_circuit_breaker_off_continues_through_errors_and_lost_connections(tmp_path: Path) -> None:
    page = FakePage(closed=True)
    client = FakeClient(alive=False)
    batch = make_batch(tmp_path, page=page, client=client, features=FeatureSet({"circuit_breaker": False}))
    record = make_record([RuntimeError("1"), RuntimeError("2"), RuntimeError("3"), RuntimeError("4")])
    selection = [(f"https://x/{i}", f"V{i}") for i in range(1, 5)]

    app._record_batch(batch, selection, record=record)

    assert len(record.calls) == 4  # type: ignore[attr-defined]


def test_stalled_retries_once_below_target_height(tmp_path: Path) -> None:
    batch = make_batch(tmp_path)
    stalled = make_result(reason=StopReason.STALLED.value, target_height=720)
    ok = make_result(reason=StopReason.ENDED.value)
    record = make_record([stalled, ok])
    selection = [("https://x/1", "V1")]

    app._record_batch(batch, selection, record=record)

    calls = record.calls  # type: ignore[attr-defined]
    assert len(calls) == 2
    assert calls[1]["max_height"] == 719
    assert batch.results == [ok]
    entry = batch.videos_history[url_key("https://x/1")]
    assert entry["status"] == history.STATUS_DONE


def test_quality_retry_off_skips_retry(tmp_path: Path) -> None:
    batch = make_batch(tmp_path, features=FeatureSet({"quality_retry": False}))
    stalled = make_result(reason=StopReason.STALLED.value, target_height=720)
    record = make_record([stalled])
    selection = [("https://x/1", "V1")]

    app._record_batch(batch, selection, record=record)

    assert len(record.calls) == 1  # type: ignore[attr-defined]
    entry = batch.videos_history[url_key("https://x/1")]
    assert entry["status"] == history.STATUS_FAILED


def test_test_mode_does_not_touch_history(tmp_path: Path) -> None:
    batch = make_batch(tmp_path, test_mode=True)
    record = make_record([make_result()])
    selection = [("https://x/1", "V1")]

    app._record_batch(batch, selection, record=record)

    assert batch.videos_history == {}
    assert len(batch.results) == 1


def test_keyboard_interrupt_stops_obs_renames_and_restores_mutes(tmp_path: Path) -> None:
    rec_file = tmp_path / "output.mkv"
    rec_file.write_bytes(b"data")
    client = FakeClient(output_active=True, output_path=str(rec_file))
    batch = make_batch(tmp_path, client=client, mutes={"Mic": False})
    record = make_record([KeyboardInterrupt()])
    selection = [("https://x/1", "My Video")]

    try:
        app._record_batch(batch, selection, record=record)
    except KeyboardInterrupt:
        app._handle_keyboard_interrupt(batch)
    finally:
        app._cleanup_audio(batch)

    assert client.stopped
    assert batch.videos_history[url_key("https://x/1")]["detail"] == "interrupted"
    assert not batch.results
    assert client.mutes_restored == {"Mic": False}


# ---------- _choose_selection (lot E: --all / --only) ----------


def test_choose_selection_all_returns_todo_in_playlist_order(tmp_path: Path) -> None:
    paths = Paths(data_dir=tmp_path, config=tmp_path / "config.toml")
    videos = [("https://x/1", "V1"), ("https://x/2", "V2"), ("https://x/3", "V3")]
    videos_history: history.Videos = {}
    history.record(paths.history, videos_history, "https://x/2", "V2", history.STATUS_DONE)

    selection, interactive = app._choose_selection(
        videos, videos_history, paths, Settings(), FeatureSet(), False, True, None
    )

    assert selection == [("https://x/1", "V1"), ("https://x/3", "V3")]
    assert interactive is False


def test_choose_selection_all_nothing_to_do(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths = Paths(data_dir=tmp_path, config=tmp_path / "config.toml")
    videos = [("https://x/1", "V1")]
    videos_history: history.Videos = {}
    history.record(paths.history, videos_history, "https://x/1", "V1", history.STATUS_DONE)

    selection, interactive = app._choose_selection(
        videos, videos_history, paths, Settings(), FeatureSet(), False, True, None
    )

    assert selection == []
    assert interactive is False
    assert "Nothing to record." in capsys.readouterr().out


def test_choose_selection_only_parses_and_orders(tmp_path: Path) -> None:
    paths = Paths(data_dir=tmp_path, config=tmp_path / "config.toml")
    videos = [("https://x/1", "V1"), ("https://x/2", "V2"), ("https://x/3", "V3")]

    selection, interactive = app._choose_selection(
        videos, {}, paths, Settings(), FeatureSet(), False, False, "3,1"
    )

    assert selection == [("https://x/3", "V3"), ("https://x/1", "V1")]
    assert interactive is False


def test_choose_selection_only_invalid_raises_clear_error(tmp_path: Path) -> None:
    paths = Paths(data_dir=tmp_path, config=tmp_path / "config.toml")
    videos = [("https://x/1", "V1")]

    with pytest.raises(VrecError, match="not valid in --only"):
        app._choose_selection(videos, {}, paths, Settings(), FeatureSet(), False, False, "99")


def test_choose_selection_test_and_only_skips_menu(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = Paths(data_dir=tmp_path, config=tmp_path / "config.toml")
    videos = [("https://x/1", "V1"), ("https://x/2", "V2")]

    selection, interactive = app._choose_selection(
        videos, {}, paths, Settings(), FeatureSet(), True, False, "2"
    )

    assert selection == [("https://x/2", "V2")]
    assert interactive is False
    assert "TEST MODE" in capsys.readouterr().out


def test_run_rejects_test_combined_with_all(tmp_path: Path) -> None:
    with pytest.raises(VrecError, match="--test"):
        app.run(tmp_path, tmp_path / "config.toml", test_mode=True, all_videos=True)


# ---------- disk_space_guard ----------


class _ClientWithFolder(FakeClient):
    def get_record_directory(self) -> SimpleNamespace:
        return SimpleNamespace(record_directory="D:/Videos")


def test_batch_stops_before_a_video_when_the_disk_is_almost_full(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch = make_batch(tmp_path, client=_ClientWithFolder())
    record = make_record([make_result(title="Video 1"), make_result(title="Video 2")])
    free = iter([50e9, 3e9])  # enough for the first video, not for the second
    selection = [("https://x/1", "Video 1"), ("https://x/2", "Video 2")]

    app._record_batch(batch, selection, record=record, free_bytes=lambda folder: int(next(free)))

    assert len(record.calls) == 1  # type: ignore[attr-defined]
    assert url_key("https://x/2") not in batch.videos_history  # left untouched for next time
    assert "Only 3.0 GB free in D:/Videos: batch stopped before 'Video 2'" in capsys.readouterr().out


def test_disk_space_guard_off(tmp_path: Path) -> None:
    batch = make_batch(tmp_path, client=_ClientWithFolder(), features=FeatureSet({"disk_space_guard": False}))
    record = make_record([make_result(title="Video 1")])
    app._record_batch(batch, [("https://x/1", "Video 1")], record=record, free_bytes=lambda folder: 0)
    assert len(record.calls) == 1  # type: ignore[attr-defined]


def test_disk_space_unknown_never_blocks(tmp_path: Path) -> None:
    batch = make_batch(tmp_path, client=_ClientWithFolder())
    record = make_record([make_result(title="Video 1")])

    def broken(folder: str) -> int:
        raise OSError("drive gone")

    app._record_batch(batch, [("https://x/1", "Video 1")], record=record, free_bytes=broken)
    assert len(record.calls) == 1  # type: ignore[attr-defined]


# ---------- end-of-batch totals ----------


def test_summary_line_totals(tmp_path: Path) -> None:
    a = tmp_path / "a.mkv"
    a.write_bytes(b"x" * 1000)
    ok = make_result(title="A")
    ok.duration_s, ok.file = 600.0, a
    failed = make_result(title="B")
    failed.reason = "ERROR: boom"
    failed.duration_s = 300.0
    line = app.summary_line([ok, failed], elapsed_s=3725)
    assert line.startswith("1/2 OK - 10:00 of video")
    assert line.endswith("done in 1:02:05")


def test_summary_line_minimal() -> None:
    assert app.summary_line([], 0) == "0/0 OK"
