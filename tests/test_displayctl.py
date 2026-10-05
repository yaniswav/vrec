"""vrec --display on/off/status with a fake runner, fake lock (nothing real is touched)."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from vrec import __main__ as entry
from vrec import displayctl
from vrec.display import Screen
from vrec.errors import VrecError
from vrec.lock import InstanceLock

MAIN = Screen("DISPLAY1", 0, 0, 2560, 1440, primary=True)
VIRTUAL = Screen("DISPLAY3", 2560, 0, 3840, 2160, primary=False)
TASK_ON = r"\vrec\display-on"
TASK_OFF = r"\vrec\display-off"


@pytest.fixture(autouse=True)
def _isolated_folders(monkeypatch, tmp_path):
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "ProgramData"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))


def install_files(tmp_path: Path, pattern: str = "*Virtual*") -> None:
    folder = tmp_path / "ProgramData" / "vrec"
    folder.mkdir(parents=True)
    (folder / "virtual-display.ps1").write_text("x", encoding="utf-8")
    (folder / "virtual-display-pattern.txt").write_text(pattern, encoding="utf-8")


class FakeRunner:
    """Fakes tasklist, schtasks and the PowerShell device queries."""

    def __init__(self, enabled: bool = True, processes: Sequence[Sequence[str]] = ()) -> None:
        self.enabled = enabled
        self.timeline = [list(p) for p in processes]
        self.tick = 0
        self.tasks: list[str] = []
        self.tasklist_ok = True

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        args = list(args)
        out = ""
        code = 0
        if args[0] == "tasklist":
            procs = self.timeline[min(self.tick, len(self.timeline) - 1)] if self.timeline else []
            out = "".join(f'"{p}","123","Console","1","10,000 K"\n' for p in ["System", *procs])
            code = 0 if self.tasklist_ok else 1
        elif args[0] == "schtasks":
            self.tasks.append(args[-1])
            self.enabled = args[-1].endswith("display-on")
        elif "Where-Object" in args[-1]:
            out = "OK\n" if self.enabled else "Error\n"
        else:
            out = "Virtual Display Driver\nNVIDIA GeForce\n"
        return subprocess.CompletedProcess(args, code, out, "")


def hold_lock(path: Path) -> InstanceLock:
    return InstanceLock(path).__enter__()


# ---------- helpers ----------


def test_batch_running(tmp_path: Path) -> None:
    lock = tmp_path / "vrec.lock"
    assert not displayctl.batch_running(lock)
    held = hold_lock(lock)
    assert displayctl.batch_running(lock)
    held.__exit__(None, None, None)
    assert not displayctl.batch_running(lock)


# ---------- status ----------


def test_status_not_installed(capsys) -> None:
    assert displayctl.show_status(FakeRunner(), lambda: [MAIN]) == 0
    out = capsys.readouterr().out
    assert "Helper: not installed" in out
    assert "vrec --install-display-helper" in out
    assert "DISPLAY1 (2560x1440, main screen) at 0,0" in out


def test_status_installed(tmp_path, capsys) -> None:
    install_files(tmp_path)
    assert displayctl.show_status(FakeRunner(enabled=True), lambda: [MAIN, VIRTUAL]) == 0
    out = capsys.readouterr().out
    assert "installed, it switches: Virtual Display Driver" in out
    assert "Virtual display: on" in out
    assert "DISPLAY3 (3840x2160, screen) at 2560,0" in out


# ---------- on / off ----------


def test_switch_without_helper(tmp_path) -> None:
    with pytest.raises(VrecError, match="Run once, as administrator: vrec --install-display-helper"):
        displayctl.switch(True, tmp_path / "vrec.lock", FakeRunner())


def test_off_refused_while_recording(tmp_path, capsys) -> None:
    install_files(tmp_path)
    held = hold_lock(tmp_path / "vrec.lock")
    run = FakeRunner(enabled=True)
    assert displayctl.switch(False, tmp_path / "vrec.lock", run) == 1
    held.__exit__(None, None, None)
    assert "A vrec batch is recording: not turning the virtual display off." in capsys.readouterr().out
    assert run.tasks == []


def test_off_and_already_off(tmp_path, capsys) -> None:
    install_files(tmp_path)
    run = FakeRunner(enabled=True)
    assert displayctl.switch(False, tmp_path / "vrec.lock", run) == 0
    assert run.tasks == [TASK_OFF]
    assert displayctl.switch(False, tmp_path / "vrec.lock", run) == 0
    assert run.tasks == [TASK_OFF]
    assert "already off" in capsys.readouterr().out


def test_on_reports_the_new_screen(tmp_path, capsys) -> None:
    install_files(tmp_path)
    run = FakeRunner(enabled=False)
    shown = iter([[MAIN], [MAIN], [MAIN, VIRTUAL]])
    last = [MAIN, VIRTUAL]
    code = displayctl.switch(True, tmp_path / "vrec.lock", run, lambda: next(shown, last), lambda s: None)
    assert code == 0
    assert "Virtual display turned on: DISPLAY3 (3840x2160, screen)." in capsys.readouterr().out


def test_on_when_no_screen_shows_up(tmp_path, capsys) -> None:
    install_files(tmp_path)
    run = FakeRunner(enabled=False)
    assert displayctl.switch(True, tmp_path / "vrec.lock", run, lambda: [MAIN], lambda s: None) == 0
    assert "no new screen showed up" in capsys.readouterr().out


# ---------- auto ----------


# ---------- CLI ----------


def test_cli_status_and_errors(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(entry, "_opened_by_double_click", lambda: False)
    monkeypatch.setattr(displayctl, "show_status", lambda: 0)
    assert entry.main(["--display", "status", "--data-dir", str(tmp_path)]) == 0
    assert entry.main(["--display", "on", "--data-dir", str(tmp_path)]) == 1  # helper not installed
    assert "--install-display-helper" in capsys.readouterr().out


def test_cli_ctrl_c(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(entry, "_opened_by_double_click", lambda: False)

    def boom() -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(displayctl, "show_status", boom)
    assert entry.main(["--display", "status", "--data-dir", str(tmp_path)]) == 130
