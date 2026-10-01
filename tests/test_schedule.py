"""Tests for vrec.schedule: validation, schtasks argument building, and status parsing.

All subprocess calls go through a fake `Runner`, so these tests never touch the real
Windows Task Scheduler.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from vrec import schedule
from vrec.errors import VrecError


class FakeRunner:
    """Records the argv it was called with and returns a canned result."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, args: object) -> subprocess.CompletedProcess[str]:
        call = list(args)  # type: ignore[call-overload]
        self.calls.append(call)
        return subprocess.CompletedProcess(call, self.returncode, self.stdout, self.stderr)


@pytest.fixture(autouse=True)
def _local_appdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """All tests get a fake %LOCALAPPDATA% so the launcher never touches the real one.

    They also run as if on Windows (CI runs them on Linux too); the non-Windows tests
    below set their own platform.
    """
    monkeypatch.setattr(sys, "platform", "win32")
    appdata = tmp_path / "AppData" / "Local"
    appdata.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    return appdata


def _launcher(appdata: Path) -> Path:
    return appdata / "vrec" / "scheduled-run.cmd"


# --- time / day validation -------------------------------------------------


def test_invalid_time_rejected_before_touching_scheduler(_local_appdata: Path) -> None:
    runner = FakeRunner()
    with pytest.raises(VrecError, match="Invalid time"):
        schedule.schedule_on("25:00", None, Path("data"), None, runner=runner)
    assert runner.calls == []
    assert not _launcher(_local_appdata).exists()


def test_time_without_leading_zero_rejected(_local_appdata: Path) -> None:
    runner = FakeRunner()
    with pytest.raises(VrecError, match="Invalid time"):
        schedule.schedule_on("7:30", None, Path("data"), None, runner=runner)
    assert runner.calls == []


def test_valid_time_boundaries_accepted(_local_appdata: Path) -> None:
    runner = FakeRunner()
    schedule.schedule_on("00:00", None, Path("data"), None, runner=runner)
    schedule.schedule_on("23:59", None, Path("data"), None, runner=runner)
    assert len(runner.calls) == 2


def test_invalid_day_rejected(_local_appdata: Path) -> None:
    runner = FakeRunner()
    with pytest.raises(VrecError, match="Invalid day"):
        schedule.schedule_on("07:30", "MON,FUN", Path("data"), None, runner=runner)
    assert runner.calls == []


def test_empty_days_rejected(_local_appdata: Path) -> None:
    runner = FakeRunner()
    with pytest.raises(VrecError, match="Invalid --days"):
        schedule.schedule_on("07:30", "   ", Path("data"), None, runner=runner)
    assert runner.calls == []


# --- schedule on: exact schtasks arguments ---------------------------------


def test_schedule_on_daily_builds_expected_schtasks_args(_local_appdata: Path) -> None:
    runner = FakeRunner()
    schedule.schedule_on("07:05", None, Path("data"), None, runner=runner)

    launcher = _launcher(_local_appdata)
    assert runner.calls == [
        [
            "schtasks",
            "/Create",
            "/F",
            "/SC",
            "DAILY",
            "/ST",
            "07:05",
            "/TN",
            schedule.TASK_NAME,
            "/TR",
            f'"{launcher}"',
        ]
    ]
    assert schedule.TASK_NAME == r"\vrec\scheduled-run"


def test_schedule_on_weekly_builds_expected_schtasks_args(_local_appdata: Path) -> None:
    runner = FakeRunner()
    schedule.schedule_on("18:00", "mon, tue, tue", Path("data"), None, runner=runner)

    launcher = _launcher(_local_appdata)
    assert runner.calls == [
        [
            "schtasks",
            "/Create",
            "/F",
            "/SC",
            "WEEKLY",
            "/D",
            "MON,TUE",
            "/ST",
            "18:00",
            "/TN",
            schedule.TASK_NAME,
            "/TR",
            f'"{launcher}"',
        ]
    ]


def test_schedule_on_returns_summary_with_reminder(_local_appdata: Path) -> None:
    runner = FakeRunner()
    message = schedule.schedule_on("07:05", None, Path("data"), None, runner=runner)
    assert "07:05" in message
    assert "every day" in message
    assert "OBS and the recording Chrome" in message
    assert "PC must be awake" in message


def test_schedule_on_failure_raises_with_detail(_local_appdata: Path) -> None:
    runner = FakeRunner(returncode=1, stderr="ERROR: Access is denied.")
    with pytest.raises(VrecError, match="Access is denied"):
        schedule.schedule_on("07:05", None, Path("data"), None, runner=runner)


# --- launcher content: quoting of paths with spaces ------------------------


def test_launcher_content_quotes_paths_with_spaces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _local_appdata: Path
) -> None:
    cwd_dir = tmp_path / "My Project"
    cwd_dir.mkdir()
    monkeypatch.chdir(cwd_dir)

    data_dir = tmp_path / "My Data"
    config = tmp_path / "My Config" / "config.toml"

    runner = FakeRunner()
    schedule.schedule_on("06:30", None, data_dir, config, runner=runner)

    content = _launcher(_local_appdata).read_text(encoding="utf-8")
    assert content.startswith("@echo off\n")
    assert f'cd /d "{cwd_dir}"' in content
    assert f'"{sys.executable}"' in content
    assert "-m vrec --all" in content
    assert f'--data-dir "{data_dir.resolve()}"' in content
    assert f'--config "{config.resolve()}"' in content
    assert content.strip().endswith("exit /b %ERRORLEVEL%")


def test_launcher_omits_config_when_not_given(_local_appdata: Path, tmp_path: Path) -> None:
    runner = FakeRunner()
    schedule.schedule_on("06:30", None, tmp_path / "data", None, runner=runner)
    content = _launcher(_local_appdata).read_text(encoding="utf-8")
    assert "--config" not in content


# --- schedule off -----------------------------------------------------------


def test_schedule_off_when_present_removes_task_and_launcher(_local_appdata: Path) -> None:
    launcher = _launcher(_local_appdata)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("@echo off\n", encoding="utf-8")

    runner = FakeRunner(returncode=0)
    message = schedule.schedule_off(runner=runner)

    assert message == "Scheduled run removed."
    assert runner.calls == [["schtasks", "/Delete", "/TN", schedule.TASK_NAME, "/F"]]
    assert not launcher.exists()


def test_schedule_off_when_absent_is_not_an_error_english(_local_appdata: Path) -> None:
    runner = FakeRunner(returncode=1, stderr="ERROR: The system cannot find the file specified.\n")
    assert schedule.schedule_off(runner=runner) == "No scheduled run."


def test_schedule_off_when_absent_is_not_an_error_french(_local_appdata: Path) -> None:
    runner = FakeRunner(returncode=1, stderr="ERREUR : Le fichier spécifié est introuvable.\n")
    assert schedule.schedule_off(runner=runner) == "No scheduled run."


def test_schedule_off_real_failure_raises(_local_appdata: Path) -> None:
    runner = FakeRunner(returncode=1, stderr="ERROR: Access is denied.")
    with pytest.raises(VrecError, match="Access is denied"):
        schedule.schedule_off(runner=runner)


# --- schedule status: label parsing (English + French) ----------------------


def test_schedule_status_parses_english_output() -> None:
    output = (
        "Folder:                              \\vrec\n"
        "TaskName:                             \\vrec\\scheduled-run\n"
        "Next Run Time:                        9/28/2026 7:30:00 AM\n"
        "Status:                               Ready\n"
        "Last Run Time:                        9/27/2026 7:30:00 AM\n"
        "Last Result:                          0\n"
    )
    runner = FakeRunner(returncode=0, stdout=output)
    message = schedule.schedule_status(runner=runner)
    assert "Next run: 9/28/2026 7:30:00 AM" in message
    assert "Last result: 0 (success)" in message


def test_schedule_status_parses_french_output() -> None:
    output = (
        "Dossier :                             \\vrec\n"
        "Nom de la tâche :                     \\vrec\\scheduled-run\n"
        "Prochaine exécution:                  28/09/2026 07:30:00\n"
        "État :                                Prêt\n"
        "Dernier résultat:                     267009\n"
    )
    runner = FakeRunner(returncode=0, stdout=output)
    message = schedule.schedule_status(runner=runner)
    assert "Next run: 28/09/2026 07:30:00" in message
    assert "Last result: 267009 (error)" in message


def test_schedule_status_unparsable_output_falls_back_to_raw_lines() -> None:
    output = "Some unexpected: schtasks output\nAnother line here\n"
    runner = FakeRunner(returncode=0, stdout=output)
    message = schedule.schedule_status(runner=runner)
    assert "couldn't parse" in message.lower()
    assert "Some unexpected: schtasks output" in message
    assert "Another line here" in message


def test_schedule_status_when_absent() -> None:
    runner = FakeRunner(returncode=1, stderr="ERROR: The system cannot find the file specified.\n")
    assert schedule.schedule_status(runner=runner) == "No scheduled run."


def test_schedule_status_real_failure_raises() -> None:
    runner = FakeRunner(returncode=1, stderr="ERROR: Access is denied.")
    with pytest.raises(VrecError, match="Access is denied"):
        schedule.schedule_status(runner=runner)


# --- non-Windows platforms --------------------------------------------------


def test_schedule_on_raises_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(VrecError, match="Windows"):
        schedule.schedule_on("07:30", None, Path("data"), None, runner=FakeRunner())


def test_schedule_off_raises_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(VrecError, match="Windows"):
        schedule.schedule_off(runner=FakeRunner())


def test_schedule_status_raises_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(VrecError, match="Windows"):
        schedule.schedule_status(runner=FakeRunner())


def test_launcher_command_of_the_packaged_exe_has_no_module_flag(monkeypatch) -> None:
    monkeypatch.setattr(schedule.sys, "frozen", True, raising=False)
    command = schedule._launcher_command(Path("data"), None)
    assert "-m vrec" not in command
    assert "--all" in command


# --- launcher: code page, % escaping, accented paths -----------------------


def test_launcher_sets_utf8_code_page_and_uses_crlf(_local_appdata: Path, tmp_path: Path) -> None:
    schedule.schedule_on("06:30", None, tmp_path / "data", None, runner=FakeRunner())
    raw = _launcher(_local_appdata).read_bytes()
    assert raw.startswith(b"@echo off\r\nchcp 65001 >nul\r\n")
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_launcher_escapes_percent_in_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _local_appdata: Path
) -> None:
    cwd_dir = tmp_path / "100%done"
    cwd_dir.mkdir()
    monkeypatch.chdir(cwd_dir)
    data_dir = tmp_path / "%TEMP%data"

    schedule.schedule_on("06:30", None, data_dir, None, runner=FakeRunner())

    content = _launcher(_local_appdata).read_text(encoding="utf-8")
    assert f'cd /d "{cwd_dir.parent}{chr(92)}100%%done"' in content
    assert "%%TEMP%%data" in content
    assert "%TEMP%data" not in content.replace("%%TEMP%%data", "")
    assert content.strip().endswith("exit /b %ERRORLEVEL%")


def test_launcher_keeps_accented_paths_as_utf8(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _local_appdata: Path
) -> None:
    cwd_dir = tmp_path / "Vidéos été"
    cwd_dir.mkdir()
    monkeypatch.chdir(cwd_dir)

    schedule.schedule_on("06:30", None, tmp_path / "données", None, runner=FakeRunner())

    raw = _launcher(_local_appdata).read_bytes()
    assert "Vidéos été".encode() in raw
    assert "données".encode() in raw


# --- schtasks output decoding ------------------------------------------------


def test_decode_console_uses_the_oem_code_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schedule, "_oem_encoding", lambda: "cp850")
    data = "Prochaine exécution: 01/01/2026\nDernier résultat: 0".encode("cp850")
    assert schedule._decode_console(data) == "Prochaine exécution: 01/01/2026\nDernier résultat: 0"
    assert schedule._decode_console(None) == ""
    assert schedule._decode_console("déjà str") == "déjà str"


def test_decoded_oem_output_parses_french_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schedule, "_oem_encoding", lambda: "cp850")
    raw = "Prochaine exécution: 02/10/2026 07:30:00\nDernier résultat: 0\n".encode("cp850")
    runner = FakeRunner(stdout=schedule._decode_console(raw))
    out = schedule.schedule_status(runner=runner)
    assert "Next run: 02/10/2026 07:30:00" in out
    assert "Last result: 0 (success)" in out


def test_oem_encoding_is_a_known_codec() -> None:
    import codecs

    codecs.lookup(schedule._oem_encoding())


def test_default_runner_decodes_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schedule, "_oem_encoding", lambda: "cp850")

    def fake_run(args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert "text" not in kwargs
        return subprocess.CompletedProcess(args, 0, "résultat".encode("cp850"), b"")  # type: ignore[arg-type]

    monkeypatch.setattr(schedule.subprocess, "run", fake_run)
    result = schedule._default_runner(["schtasks"])
    assert result.stdout == "résultat"
    assert result.stderr == ""
