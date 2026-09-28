"""Screen selection and the virtual display helper (no real screens or scheduled tasks touched)."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence

import pytest

from vrec import display
from vrec.display import Screen, pick_screen
from vrec.errors import VrecError

MAIN = Screen("DISPLAY1", 0, 0, 2560, 1440, primary=True)
SIDE = Screen("DISPLAY2", -1920, 0, 1920, 1080, primary=False)
VIRTUAL = Screen("DISPLAY3", 2560, 0, 3840, 2160, primary=False)


class FakeRunner:
    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.calls: list[list[str]] = []
        self.returncode, self.stdout = returncode, stdout

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        return subprocess.CompletedProcess(list(args), self.returncode, self.stdout, "boom")


def test_screen_geometry():
    assert VIRTUAL.center == (2560 + 1920, 1080)
    assert VIRTUAL.contains(3000, 100)
    assert not VIRTUAL.contains(100, 100)
    assert "3840x2160" in VIRTUAL.describe()
    assert "main screen" in MAIN.describe()


@pytest.mark.parametrize(
    ("wanted", "expected"),
    [
        ("auto", VIRTUAL),  # largest non-primary screen
        ("", VIRTUAL),
        ("1", MAIN),
        ("3", VIRTUAL),
        ("9", None),
        ("display2", SIDE),
        ("nope", None),
    ],
)
def test_pick_screen(wanted, expected):
    assert pick_screen([MAIN, SIDE, VIRTUAL], wanted) == expected


def test_pick_screen_auto_needs_a_second_screen():
    assert pick_screen([MAIN], "auto") is None
    assert pick_screen([], "auto") is None


def test_helper_script_escapes_the_pattern():
    script = display.helper_script("*Virtual*'s")
    assert "-like '*Virtual*''s'" in script
    assert "Enable-PnpDevice" in script and "Disable-PnpDevice" in script
    assert "\r\n" in script


def test_install_helper_needs_admin(monkeypatch):
    monkeypatch.setattr(display.sys, "platform", "win32")
    monkeypatch.setattr(display, "is_admin", lambda: False)
    with pytest.raises(VrecError, match="administrator"):
        display.install_helper("*Virtual*", run=FakeRunner())


def test_install_helper_registers_two_elevated_tasks(monkeypatch, tmp_path):
    monkeypatch.setattr(display.sys, "platform", "win32")
    monkeypatch.setattr(display, "is_admin", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    run = FakeRunner()
    script = display.install_helper("*Virtual Display*", run=run)
    assert script == tmp_path / "vrec" / "virtual-display.ps1"
    assert "*Virtual Display*" in script.read_text(encoding="utf-8")
    assert [c[:6] for c in run.calls] == [
        ["schtasks", "/Create", "/F", "/TN", display.TASK_ON, "/TR"],
        ["schtasks", "/Create", "/F", "/TN", display.TASK_OFF, "/TR"],
    ]
    for call, state in zip(run.calls, ("on", "off"), strict=True):
        assert call[6].endswith(f'"{script}" {state}')
        assert "/RL" in call and call[call.index("/RL") + 1] == "HIGHEST"


def test_install_helper_reports_schtasks_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(display.sys, "platform", "win32")
    monkeypatch.setattr(display, "is_admin", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    with pytest.raises(VrecError, match="scheduled task"):
        display.install_helper("*Virtual*", run=FakeRunner(returncode=1))


def test_install_helper_is_windows_only(monkeypatch):
    monkeypatch.setattr(display.sys, "platform", "linux")
    with pytest.raises(VrecError, match="only supported on Windows"):
        display.install_helper("*Virtual*", run=FakeRunner())


def test_uninstall_helper(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    script = display.helper_script_path()
    script.parent.mkdir(parents=True)
    script.write_text("x", encoding="utf-8")
    run = FakeRunner()
    display.uninstall_helper(run=run)
    assert not script.exists()
    assert [c[:2] for c in run.calls] == [["schtasks", "/Delete"], ["schtasks", "/Delete"]]


def test_set_virtual_display_runs_the_right_task():
    run = FakeRunner()
    display.set_virtual_display(True, run=run)
    display.set_virtual_display(False, run=run)
    assert run.calls == [
        ["schtasks", "/Run", "/TN", display.TASK_ON],
        ["schtasks", "/Run", "/TN", display.TASK_OFF],
    ]


def test_set_virtual_display_without_helper():
    with pytest.raises(VrecError, match="install-display-helper"):
        display.set_virtual_display(True, run=FakeRunner(returncode=1))


def test_list_display_devices():
    run = FakeRunner(stdout="Intel(R) UHD Graphics\r\n\r\nVirtual Display Driver\r\n")
    assert display.list_display_devices(run=run) == ["Intel(R) UHD Graphics", "Virtual Display Driver"]


def test_list_screens_on_this_machine():
    screens = display.list_screens()
    if display.sys.platform == "win32":
        assert sum(s.primary for s in screens) == 1
    else:
        assert screens == []


def test_wait_for_new_screen_returns_the_screen_that_appeared():
    answers = iter([[MAIN, SIDE], [MAIN, SIDE, VIRTUAL]])
    sleeps: list[float] = []
    new = display.wait_for_new_screen([MAIN, SIDE], lister=lambda: next(answers), sleep=sleeps.append)
    assert new == VIRTUAL
    assert sleeps == [0.5]


def test_wait_for_new_screen_gives_up():
    assert (
        display.wait_for_new_screen([MAIN], timeout_s=1, lister=lambda: [MAIN], sleep=lambda s: None) is None
    )


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [("OK\r\n", True), ("Error\r\n", False), ("Unknown\r\nOK\r\n", True), ("", None)],
)
def test_virtual_display_enabled(monkeypatch, tmp_path, stdout, expected):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    display.helper_pattern_path().parent.mkdir(parents=True)
    display.helper_pattern_path().write_text("*Virtual*", encoding="utf-8")
    run = FakeRunner(stdout=stdout)
    assert display.virtual_display_enabled(run=run) is expected
    assert "-like '*Virtual*'" in run.calls[0][-1]


def test_virtual_display_state_unknown_without_helper(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    run = FakeRunner(stdout="OK")
    assert display.virtual_display_enabled(run=run) is None
    assert run.calls == []


def test_install_writes_and_uninstall_removes_the_pattern(monkeypatch, tmp_path):
    monkeypatch.setattr(display.sys, "platform", "win32")
    monkeypatch.setattr(display, "is_admin", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    display.install_helper("*Virtual Display*", run=FakeRunner())
    assert display.helper_pattern_path().read_text(encoding="utf-8") == "*Virtual Display*"
    display.uninstall_helper(run=FakeRunner())
    assert not display.helper_pattern_path().exists()
