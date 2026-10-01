"""Exit codes, Ctrl+C history, history save failures, and window restore ordering."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from vrec import app, history
from vrec.config import Paths, Settings
from vrec.features import FeatureSet
from vrec.playlist import url_key
from vrec.recorder import RecordingResult, StopReason


class FakePage:
    def is_closed(self) -> bool:
        return False

    def wait_for_timeout(self, ms: int) -> None:
        pass


class FakeBrowser:
    def is_connected(self) -> bool:
        return True


class FakeClient:
    def __init__(self, output_active: bool = False, output_path: str = "", alive: bool = True) -> None:
        self.output_active = output_active
        self.output_path = output_path
        self.alive = alive

    def get_record_status(self) -> SimpleNamespace:
        return SimpleNamespace(output_active=self.output_active)

    def stop_record(self) -> SimpleNamespace:
        self.output_active = False
        return SimpleNamespace(output_path=self.output_path)

    def get_version(self) -> None:
        if not self.alive:
            raise RuntimeError("OBS gone")

    def get_record_directory(self) -> SimpleNamespace:
        return SimpleNamespace(record_directory="C:/rec")


def make_batch(tmp_path: Path, **overrides: object) -> app.Batch:
    kwargs: dict[str, object] = dict(
        paths=Paths(data_dir=tmp_path, config=tmp_path / "config.toml"),
        settings=Settings(),
        features=FeatureSet(),
        test_mode=False,
        client=FakeClient(),
        page=FakePage(),
        browser=FakeBrowser(),
    )
    kwargs.update(overrides)
    return app.Batch(**kwargs)  # type: ignore[arg-type]


def make_record(behaviors: list[object]):  # type: ignore[no-untyped-def]
    remaining = list(behaviors)

    def record(*args: object) -> RecordingResult:
        behavior = remaining.pop(0)
        if isinstance(behavior, BaseException):
            raise behavior
        return behavior  # type: ignore[return-value]

    return record


def make_result(**kwargs: object) -> RecordingResult:
    defaults = dict(number=1, title="Video", reason=StopReason.ENDED.value, image_ok=True, audio_ok=True)
    defaults.update(kwargs)
    return RecordingResult(**defaults)  # type: ignore[arg-type]


def one(i: int) -> tuple[str, str]:
    return (f"https://x/{i}", f"V{i}")


# ---------- exit codes ----------


def test_all_ok_is_zero(tmp_path: Path) -> None:
    batch = make_batch(tmp_path)
    app._record_batch(batch, [one(1)], record=make_record([make_result()]))
    assert app._final_report(batch, send=lambda t, x: True) == 0


def test_nothing_recorded_is_zero(tmp_path: Path) -> None:
    assert app._final_report(make_batch(tmp_path)) == 0


def test_one_video_not_ok_is_one(tmp_path: Path) -> None:
    batch = make_batch(tmp_path)
    record = make_record([make_result(), make_result(reason=StopReason.STALLED.value)])
    app._record_batch(batch, [one(1), one(2)], record=record)
    assert not batch.stopped_early
    assert app._final_report(batch, send=lambda t, x: True) == 1


def test_lost_connection_is_one(tmp_path: Path) -> None:
    batch = make_batch(tmp_path, client=FakeClient(alive=False))
    app._record_batch(batch, [one(1), one(2)], record=make_record([RuntimeError("x")]))
    assert batch.stopped_early
    assert app._final_report(batch, send=lambda t, x: True) == 1


def test_three_errors_is_one(tmp_path: Path) -> None:
    batch = make_batch(tmp_path)
    record = make_record([RuntimeError("1"), RuntimeError("2"), RuntimeError("3")])
    app._record_batch(batch, [one(i) for i in range(1, 5)], record=record)
    assert batch.stopped_early
    assert app.exit_code(batch) == 1


def test_disk_guard_is_one_even_without_results(tmp_path: Path) -> None:
    batch = make_batch(tmp_path)
    app._record_batch(batch, [one(1)], record=make_record([]), free_bytes=lambda folder: 0)
    assert not batch.results
    assert app._final_report(batch) == 1


def test_interrupted_is_130(tmp_path: Path) -> None:
    batch = make_batch(tmp_path)
    app._handle_keyboard_interrupt(batch)
    assert app._final_report(batch) == 130


def test_test_mode_codes(tmp_path: Path) -> None:
    ok = make_batch(tmp_path, test_mode=True)
    ok.results.append(make_result())
    assert app.exit_code(ok) == 0
    bad = make_batch(tmp_path, test_mode=True)
    bad.results.append(make_result(reason="ERROR: boom"))
    assert app.exit_code(bad) == 1


# ---------- Ctrl+C ----------


def test_ctrl_c_marks_video_failed_in_history(tmp_path: Path) -> None:
    rec_file = tmp_path / "output.mkv"
    rec_file.write_bytes(b"data")
    batch = make_batch(tmp_path, client=FakeClient(output_active=True, output_path=str(rec_file)))
    with pytest.raises(KeyboardInterrupt):
        app._record_batch(batch, [("https://x/1", "My Video")], record=make_record([KeyboardInterrupt()]))
    app._handle_keyboard_interrupt(batch)

    entry = history.load(batch.paths.history)[url_key("https://x/1")]
    assert entry["status"] == history.STATUS_FAILED
    assert entry["detail"] == "interrupted"
    assert entry["file"].endswith("INTERRUPTED - My Video.mkv")


def test_ctrl_c_with_no_video_in_progress_leaves_history(tmp_path: Path) -> None:
    batch = make_batch(tmp_path)
    app._handle_keyboard_interrupt(batch)
    assert batch.videos_history == {}


def test_ctrl_c_in_test_mode_leaves_history(tmp_path: Path) -> None:
    batch = make_batch(tmp_path, test_mode=True)
    with pytest.raises(KeyboardInterrupt):
        app._record_batch(batch, [one(1)], record=make_record([KeyboardInterrupt()]))
    app._handle_keyboard_interrupt(batch)
    assert batch.videos_history == {}


def test_interrupted_rename_uses_a_clean_title(tmp_path: Path) -> None:
    rec_file = tmp_path / "output.mkv"
    rec_file.write_bytes(b"data")
    batch = make_batch(tmp_path, client=FakeClient(output_active=True, output_path=str(rec_file)))
    app._record_batch(batch, [("https://x/con", None)], record=make_record([RuntimeError("boom")]))
    assert batch.results[0].file is not None
    assert batch.results[0].file.name == "INTERRUPTED - con_.mkv"


# ---------- history save failure ----------


def test_history_save_failure_warns_and_batch_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken_save(*args: object, **kwargs: object) -> None:
        raise PermissionError("locked by antivirus\nsecond line")

    monkeypatch.setattr(history, "save", broken_save)
    batch = make_batch(tmp_path)
    app._record_batch(batch, [one(1), one(2)], record=make_record([make_result(), make_result()]))
    assert len(batch.results) == 2
    assert batch.videos_history[url_key("https://x/2")]["status"] == history.STATUS_DONE
    out = capsys.readouterr()
    text = out.out + out.err
    assert (
        "Couldn't save history.json (locked by antivirus): this video's status is kept in memory only."
        in text
    )


# ---------- window restore ----------


def test_prepare_window_runs_inside_the_restore_try(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    @contextmanager
    def connect(port: int, quality_filter: bool = True, audio_sink: bool = True):  # type: ignore[no-untyped-def]
        yield FakeBrowser(), FakePage()

    def prepare(batch: app.Batch) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(app, "_load_inputs", lambda paths: ([("https://x/1", "One")], {}))
    monkeypatch.setattr(app, "_connect_obs", lambda *a: (FakeClient(), "pw"))
    monkeypatch.setattr(app.obs_control, "current_scene", lambda c: "Main")
    for step in (
        "_virtual_display_on",
        "_start_chrome_if_needed",
        "_place_chrome_early",
        "_prepare_scene",
        "_prepare_audio",
        "_cleanup_audio",
        "_restore_scene",
        "_virtual_display_off",
    ):
        monkeypatch.setattr(app, step, lambda batch: None)
    monkeypatch.setattr(app, "_choose_selection", lambda *a: ([("https://x/1", "One")], False))
    monkeypatch.setattr(app, "connect_browser", connect)
    monkeypatch.setattr(app, "_prepare_window", prepare)
    monkeypatch.setattr(app, "_restore_window", lambda batch: calls.append("restore"))
    paths = Paths(data_dir=tmp_path, config=tmp_path / "config.toml")
    code = app._run_locked(paths, Settings(), FeatureSet({"keep_awake": False}), False, True, None)
    assert calls == ["restore"]
    assert code == 130
