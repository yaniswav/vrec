"""vrec --display on/off/status/auto with a fake runner, fake lock, fake clock (nothing real is touched)."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import pytest

from vrec import __main__ as entry
from vrec import displayctl
from vrec.display import Screen
from vrec.errors import VrecError
from vrec.lock import InstanceLock

MAIN = Screen("DISPLAY1", 0, 0, 2560, 1440, primary=True)
VIRTUAL = Screen("DISPLAY3", 2560, 0, 3840, 2160, primary=False)
GAME = "VALORANT-Win64-Shipping.exe"
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


def test_running_processes_parses_csv_and_failure() -> None:
    run = FakeRunner(processes=[[GAME]])
    assert displayctl.running_processes(run) == ["System", GAME]
    run.tasklist_ok = False
    assert displayctl.running_processes(run) is None


def test_listed_running_is_case_insensitive() -> None:
    assert displayctl.listed_running(["valorant-win64-shipping.EXE"], ["a.exe", GAME]) == GAME
    assert displayctl.listed_running([GAME], ["a.exe"]) is None


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


class Clock:
    """Fake sleep: advances the runner's tick, runs a hook, raises Ctrl+C after `stop_after` ticks."""

    def __init__(self, run: FakeRunner, stop_after: int, on_tick=None) -> None:
        self.run, self.stop_after, self.on_tick = run, stop_after, on_tick

    def __call__(self, _s: float) -> None:
        self.run.tick += 1
        if self.on_tick:
            self.on_tick(self.run.tick)
        if self.run.tick >= self.stop_after:
            raise KeyboardInterrupt

    def now(self) -> datetime:
        return datetime(2026, 1, 1, 21, 4)


def test_auto_needs_a_list(capsys) -> None:
    assert displayctl.watch([], Path("x.lock"), FakeRunner()) == 1
    assert "off_while_running" in capsys.readouterr().out


def test_auto_needs_the_helper(tmp_path) -> None:
    with pytest.raises(VrecError, match="isn't installed"):
        displayctl.watch([GAME], tmp_path / "vrec.lock", FakeRunner())


def test_auto_off_then_on(tmp_path, capsys) -> None:
    install_files(tmp_path)
    run = FakeRunner(enabled=True, processes=[[], [GAME], [GAME], []])
    clock = Clock(run, stop_after=5)
    assert displayctl.watch([GAME.lower()], tmp_path / "vrec.lock", run, clock, clock.now) == 0
    out = capsys.readouterr().out
    assert f"[21:04] {GAME} started: virtual display off." in out
    assert "[21:04] No listed program running: virtual display on." in out
    assert run.tasks == [TASK_OFF, TASK_ON]


def test_auto_ctrl_c_leaves_it_off_while_the_game_runs(tmp_path) -> None:
    install_files(tmp_path)
    run = FakeRunner(enabled=True, processes=[[GAME]])
    clock = Clock(run, stop_after=2)
    assert displayctl.watch([GAME], tmp_path / "vrec.lock", run, clock, clock.now) == 0
    assert run.tasks == [TASK_OFF]


def test_auto_ctrl_c_puts_the_display_back_on(tmp_path, capsys) -> None:
    install_files(tmp_path)
    run = FakeRunner(enabled=True, processes=[[GAME]])

    def game_quits(tick: int) -> None:
        run.timeline = [[]]  # gone just before Ctrl+C, before the next look

    clock = Clock(run, stop_after=1, on_tick=game_quits)
    assert displayctl.watch([GAME], tmp_path / "vrec.lock", run, clock, clock.now) == 0
    assert run.tasks == [TASK_OFF, TASK_ON]
    assert "Stopped." in capsys.readouterr().out


def test_auto_waits_while_recording(tmp_path, capsys) -> None:
    install_files(tmp_path)
    lock = tmp_path / "vrec.lock"
    held = hold_lock(lock)
    run = FakeRunner(enabled=True, processes=[[GAME]])

    def release(tick: int) -> None:
        if tick == 3:
            held.__exit__(None, None, None)

    clock = Clock(run, stop_after=4, on_tick=release)
    assert displayctl.watch([GAME], lock, run, clock, clock.now) == 0
    out = capsys.readouterr().out
    assert out.count("vrec is recording: the virtual display stays on until it ends.") == 1
    assert f"{GAME} started: virtual display off." in out
    assert run.tasks == [TASK_OFF]


def test_auto_ignores_an_unreadable_process_list(tmp_path) -> None:
    install_files(tmp_path)
    run = FakeRunner(enabled=True, processes=[[GAME]])
    run.tasklist_ok = False
    clock = Clock(run, stop_after=2)
    assert displayctl.watch([GAME], tmp_path / "vrec.lock", run, clock, clock.now) == 0
    assert run.tasks == []


def test_auto_turns_the_display_on_at_start_when_nothing_runs(tmp_path) -> None:
    install_files(tmp_path)
    run = FakeRunner(enabled=False, processes=[[]])
    clock = Clock(run, stop_after=1)
    assert displayctl.watch([GAME], tmp_path / "vrec.lock", run, clock, clock.now) == 0
    assert run.tasks == [TASK_ON]


# ---------- CLI ----------


def test_cli_status_and_errors(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(entry, "_opened_by_double_click", lambda: False)
    monkeypatch.setattr(displayctl, "show_status", lambda: 0)
    assert entry.main(["--display", "status", "--data-dir", str(tmp_path)]) == 0
    assert entry.main(["--display", "on", "--data-dir", str(tmp_path)]) == 1  # helper not installed
    assert "--install-display-helper" in capsys.readouterr().out
    assert entry.main(["--display", "auto", "--data-dir", str(tmp_path)]) == 1  # empty list
    assert "off_while_running" in capsys.readouterr().out


def test_cli_auto_uses_the_config_list(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(entry, "_opened_by_double_click", lambda: False)
    (tmp_path / "config.toml").write_text('[display]\noff_while_running = ["a.exe"]\n', encoding="utf-8")
    seen: list[object] = []
    monkeypatch.setattr(displayctl, "watch", lambda names, lock: seen.append(names) or 0)
    assert entry.main(["--display", "auto", "--data-dir", str(tmp_path)]) == 0
    assert seen == [("a.exe",)]


def test_cli_ctrl_c(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(entry, "_opened_by_double_click", lambda: False)

    def boom() -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(displayctl, "show_status", boom)
    assert entry.main(["--display", "status", "--data-dir", str(tmp_path)]) == 130
