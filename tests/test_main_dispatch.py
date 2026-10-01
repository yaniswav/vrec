"""vrec.__main__.main: dispatch to each command and the exit codes it returns."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import pytest

from vrec import __main__ as entry
from vrec.errors import VrecError
from vrec.features import feature_names, load_features


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    """No pause prompt, no console mode change, no data dir from the environment."""
    monkeypatch.setattr(entry, "_opened_by_double_click", lambda: False)
    monkeypatch.setattr("vrec.console.no_quick_edit", contextlib.nullcontext)
    monkeypatch.delenv("VREC_DATA_DIR", raising=False)


def _data(tmp_path: Path) -> list[str]:
    return ["--data-dir", str(tmp_path)]


def _set_run_logs(tmp_path: Path, on: bool) -> None:
    (tmp_path / "features.toml").write_text(f"[features]\nrun_logs = {str(on).lower()}\n", encoding="utf-8")


# ---------- --features / --enable / --disable ----------


def test_features_lists_every_feature(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert entry.main(["--features", *_data(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert all(name in out for name in feature_names())


def test_enable_and_disable_are_saved(tmp_path: Path) -> None:
    assert entry.main(["--disable", "black_check", "audio_check", *_data(tmp_path)]) == 0
    assert entry.main(["--enable", "manage_virtual_display", *_data(tmp_path)]) == 0
    features, _ = load_features(tmp_path / "features.toml")
    assert not features.enabled("black_check")
    assert not features.enabled("audio_check")
    assert features.enabled("manage_virtual_display")


def test_unknown_feature_name_returns_1_and_saves_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert entry.main(["--enable", "black_check", "nope", *_data(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "Unknown feature: nope" in out
    assert "Valid names:" in out
    assert not (tmp_path / "features.toml").exists()


def test_unknown_name_in_disable_returns_1(tmp_path: Path) -> None:
    assert entry.main(["--disable", "bogus", *_data(tmp_path)]) == 1


def test_broken_features_file_returns_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "features.toml").write_text("[features\nbroken", encoding="utf-8")
    assert entry.main(["--features", *_data(tmp_path)]) == 1
    assert capsys.readouterr().out.strip()


def test_features_file_warnings_are_printed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "features.toml").write_text("[features]\nnot_a_feature = true\n", encoding="utf-8")
    assert entry.main(["--features", *_data(tmp_path)]) == 0
    assert "not_a_feature" in capsys.readouterr().out


# ---------- --schedule ----------


@pytest.mark.parametrize(
    "words",
    [["on"], ["on", "08:00", "extra"], ["off", "now"], ["status", "x"], ["bogus"]],
)
def test_schedule_usage_errors_return_1(words: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert entry.main(["--schedule", *words]) == 1
    assert "Usage: vrec --schedule" in capsys.readouterr().out


def test_schedule_actions_are_dispatched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from vrec import schedule

    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        schedule, "schedule_on", lambda at, days, data_dir, config: calls.append(("on", at, days)) or "ON"
    )
    monkeypatch.setattr(schedule, "schedule_off", lambda: calls.append(("off",)) or "OFF")
    monkeypatch.setattr(schedule, "schedule_status", lambda: calls.append(("status",)) or "STATUS")
    assert entry.main(["--schedule", "on", "08:30", "--days", "MON,TUE", *_data(tmp_path)]) == 0
    assert entry.main(["--schedule", "off"]) == 0
    assert entry.main(["--schedule", "status"]) == 0
    assert calls == [("on", "08:30", "MON,TUE"), ("off",), ("status",)]
    assert capsys.readouterr().out.split() == ["ON", "OFF", "STATUS"]


def test_schedule_error_returns_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from vrec import schedule

    def boom(*args: Any) -> str:
        raise VrecError("bad time")

    monkeypatch.setattr(schedule, "schedule_on", boom)
    assert entry.main(["--schedule", "on", "99:99"]) == 1
    assert "bad time" in capsys.readouterr().out


# ---------- --install-display-helper / --uninstall-display-helper ----------


def test_install_display_helper_without_matching_adapter(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from vrec import display

    monkeypatch.setattr(display, "list_display_devices", lambda: ["Intel UHD", "NVIDIA GeForce"])
    monkeypatch.setattr(display, "install_helper", lambda pattern: pytest.fail("must not install"))
    assert entry.main(["--install-display-helper"]) == 1
    out = capsys.readouterr().out
    assert "No display adapter matches '*Virtual*'" in out
    assert "- Intel UHD" in out


def test_install_display_helper_with_matching_adapter(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from vrec import display

    patterns: list[str] = []
    monkeypatch.setattr(display, "list_display_devices", lambda: ["Intel UHD", "Virtual Display Driver"])
    monkeypatch.setattr(display, "install_helper", lambda pattern: patterns.append(pattern) or "helper.ps1")
    assert entry.main(["--install-display-helper"]) == 0
    assert patterns == ["*Virtual*"]
    out = capsys.readouterr().out
    assert "helper.ps1" in out and "Virtual Display Driver" in out


def test_install_display_helper_error_returns_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from vrec import display

    def boom() -> list[str]:
        raise VrecError("needs administrator")

    monkeypatch.setattr(display, "list_display_devices", boom)
    assert entry.main(["--install-display-helper", "*x*"]) == 1
    assert "needs administrator" in capsys.readouterr().out


def test_uninstall_display_helper(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from vrec import display

    monkeypatch.setattr(display, "uninstall_helper", lambda: None)
    assert entry.main(["--uninstall-display-helper"]) == 0
    assert "removed" in capsys.readouterr().out


def test_like_is_case_insensitive_wildcard() -> None:
    assert entry._like("Virtual Display", "*virtual*")
    assert not entry._like("Intel UHD", "*virtual*")


# ---------- app.run results ----------


def _fake_run(monkeypatch: pytest.MonkeyPatch, behaviour: Any) -> list[tuple[Any, ...]]:
    from vrec import app

    calls: list[tuple[Any, ...]] = []

    def run(
        data_dir: Path, config: Path, test: bool, all_videos: bool = False, only: str | None = None
    ) -> int:
        calls.append((data_dir, config, test, all_videos, only))
        if isinstance(behaviour, BaseException):
            raise behaviour
        return behaviour

    monkeypatch.setattr(app, "run", run)
    return calls


def test_exit_code_of_app_run_is_passed_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_run(monkeypatch, 1)
    assert entry.main(["--test", "--only", "2", *_data(tmp_path)]) == 1
    assert calls == [(tmp_path, tmp_path / "config.toml", True, False, "2")]


def test_exit_code_zero_and_all_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_run(monkeypatch, 0)
    assert entry.main(["--all", *_data(tmp_path)]) == 0
    assert calls[0][3] is True


def test_data_dir_comes_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_run(monkeypatch, 0)
    monkeypatch.setenv("VREC_DATA_DIR", str(tmp_path / "env"))
    assert entry.main([]) == 0
    assert calls[0][0] == tmp_path / "env"
    assert (tmp_path / "env").is_dir()


@pytest.mark.parametrize("run_logs", [True, False])
def test_vrec_error_prints_its_message_and_returns_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], run_logs: bool
) -> None:
    _fake_run(monkeypatch, VrecError("OBS is not reachable"))
    _set_run_logs(tmp_path, run_logs)
    assert entry.main(_data(tmp_path)) == 1
    assert "OBS is not reachable" in capsys.readouterr().out


@pytest.mark.parametrize("run_logs", [True, False])
def test_ctrl_c_returns_130(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], run_logs: bool
) -> None:
    _fake_run(monkeypatch, KeyboardInterrupt())
    _set_run_logs(tmp_path, run_logs)
    assert entry.main(_data(tmp_path)) == 130
    assert "Stopped." in capsys.readouterr().out


def test_unexpected_error_with_run_logs_points_to_the_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_run(monkeypatch, RuntimeError("kaboom\nsecond line"))
    _set_run_logs(tmp_path, True)
    assert entry.main(_data(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "Unexpected error: kaboom" in out
    assert "second line" not in out
    logs = list((tmp_path / "logs").glob("vrec-*.log"))
    assert len(logs) == 1
    assert str(logs[0]) in out
    assert "RuntimeError: kaboom" in logs[0].read_text(encoding="utf-8")  # the traceback is in the log


def test_unexpected_error_without_run_logs_prints_the_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_run(monkeypatch, RuntimeError("kaboom"))
    _set_run_logs(tmp_path, False)
    assert entry.main(_data(tmp_path)) == 1
    captured = capsys.readouterr()
    assert "Traceback" in captured.err and "RuntimeError: kaboom" in captured.err
    assert not (tmp_path / "logs").exists()


def test_broken_features_file_does_not_stop_a_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run(monkeypatch, 0)
    (tmp_path / "features.toml").write_text("[features\nbroken", encoding="utf-8")
    assert entry.main(_data(tmp_path)) == 0


def test_doctor_is_dispatched_with_its_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from vrec import doctor

    seen: list[str] = []
    monkeypatch.setattr(doctor, "run_doctor", lambda settings, paths, features: seen.append("doctor") or 3)
    assert entry.main(["--doctor", *_data(tmp_path)]) == 3
    assert seen == ["doctor"]
