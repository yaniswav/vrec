"""Screen selection and the virtual display helper (no real screens or scheduled tasks touched)."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence

import pytest

from vrec import display
from vrec.display import Screen, pick_screen
from vrec.errors import VrecError


@pytest.fixture(autouse=True)
def _isolated_folders(monkeypatch, tmp_path):
    """Helper files go to temp folders, never to the real %ProgramData% or %LOCALAPPDATA%."""
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "ProgramData"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))


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


def test_pick_screen_auto_with_a_single_screen_uses_it():
    assert pick_screen([MAIN], "auto") == MAIN
    assert pick_screen([], "auto") is None


def test_helper_script_uses_the_pattern():
    script = display.helper_script("*Virtual Display (1)*")
    assert "-like '*Virtual Display (1)*'" in script
    assert "Enable-PnpDevice" in script and "Disable-PnpDevice" in script
    assert "\r\n" in script


@pytest.mark.parametrize("pattern", ["*Virtual*'s", "a\u2019; calc; \u2019", "a$b", "x`y", "", "a" * 101])
def test_unsafe_patterns_are_refused(pattern):
    with pytest.raises(VrecError, match="Unsupported adapter pattern"):
        display.helper_script(pattern)


def test_install_helper_needs_admin(monkeypatch):
    monkeypatch.setattr(display.sys, "platform", "win32")
    monkeypatch.setattr(display, "is_admin", lambda: False)
    with pytest.raises(VrecError, match="administrator"):
        display.install_helper("*Virtual*", run=FakeRunner())


def test_install_helper_registers_two_elevated_tasks(monkeypatch, tmp_path):
    monkeypatch.setattr(display.sys, "platform", "win32")
    monkeypatch.setattr(display, "is_admin", lambda: True)
    run = FakeRunner()
    script = display.install_helper("*Virtual Display*", run=run)
    folder = tmp_path / "ProgramData" / "vrec"
    assert script == folder / "virtual-display.ps1"
    assert "*Virtual Display*" in script.read_text(encoding="utf-8")
    # The folder is locked down (admins and SYSTEM only can write) before the script is written.
    icacls = run.calls[0]
    assert icacls[:2] == ["icacls", str(folder)] and "/inheritance:r" in icacls
    assert "*S-1-5-32-545:(OI)(CI)RX" in icacls  # users can only read and run
    assert [c[:6] for c in run.calls[1:]] == [
        ["schtasks", "/Create", "/F", "/TN", display.TASK_ON, "/TR"],
        ["schtasks", "/Create", "/F", "/TN", display.TASK_OFF, "/TR"],
    ]
    for call, state in zip(run.calls[1:], ("on", "off"), strict=True):
        assert call[6].endswith(f'"{script}" {state}')
        assert "/RL" in call and call[call.index("/RL") + 1] == "HIGHEST"


def test_install_helper_reports_schtasks_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(display.sys, "platform", "win32")
    monkeypatch.setattr(display, "is_admin", lambda: True)

    class SchtasksFails(FakeRunner):
        def __call__(self, args):  # type: ignore[no-untyped-def]
            result = super().__call__(args)
            result.returncode = 0 if args[0] == "icacls" else 1
            return result

    with pytest.raises(VrecError, match="scheduled task"):
        display.install_helper("*Virtual*", run=SchtasksFails())


def test_install_helper_stops_if_the_folder_cant_be_protected(monkeypatch, tmp_path):
    monkeypatch.setattr(display.sys, "platform", "win32")
    monkeypatch.setattr(display, "is_admin", lambda: True)
    with pytest.raises(VrecError, match="Couldn't protect"):
        display.install_helper("*Virtual*", run=FakeRunner(returncode=1))
    assert not display.helper_script_path().exists()


def test_install_refuses_an_unsafe_pattern_before_writing(monkeypatch):
    monkeypatch.setattr(display.sys, "platform", "win32")
    monkeypatch.setattr(display, "is_admin", lambda: True)
    run = FakeRunner()
    with pytest.raises(VrecError, match="Unsupported adapter pattern"):
        display.install_helper("x'; Remove-Item C:\\ -Recurse; '", run=run)
    assert run.calls == [] and not display.helper_dir().exists()


def test_old_user_writable_helper_files_are_removed(monkeypatch, tmp_path):
    monkeypatch.setattr(display.sys, "platform", "win32")
    monkeypatch.setattr(display, "is_admin", lambda: True)
    legacy = display.legacy_helper_dir()
    legacy.mkdir(parents=True)
    (legacy / "virtual-display.ps1").write_text("old", encoding="utf-8")
    (legacy / "chrome-profile").mkdir()  # not ours to delete
    display.install_helper("*Virtual*", run=FakeRunner())
    assert not (legacy / "virtual-display.ps1").exists()
    assert (legacy / "chrome-profile").exists()


def test_install_helper_is_windows_only(monkeypatch):
    monkeypatch.setattr(display.sys, "platform", "linux")
    with pytest.raises(VrecError, match="only supported on Windows"):
        display.install_helper("*Virtual*", run=FakeRunner())


def test_uninstall_helper(monkeypatch, tmp_path):
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
    display.helper_pattern_path().parent.mkdir(parents=True)
    display.helper_pattern_path().write_text("*Virtual*", encoding="utf-8")
    run = FakeRunner(stdout=stdout)
    assert display.virtual_display_enabled(run=run) is expected
    assert "-like '*Virtual*'" in run.calls[0][-1]


def test_virtual_display_state_unknown_without_helper(monkeypatch, tmp_path):
    run = FakeRunner(stdout="OK")
    assert display.virtual_display_enabled(run=run) is None
    assert run.calls == []


def test_virtual_display_state_ignores_a_tampered_pattern(monkeypatch, tmp_path):
    display.helper_pattern_path().parent.mkdir(parents=True)
    display.helper_pattern_path().write_text("x'; calc; '", encoding="utf-8")
    run = FakeRunner(stdout="OK")
    assert display.virtual_display_enabled(run=run) is None
    assert run.calls == []


def test_install_writes_and_uninstall_removes_the_pattern(monkeypatch, tmp_path):
    monkeypatch.setattr(display.sys, "platform", "win32")
    monkeypatch.setattr(display, "is_admin", lambda: True)
    display.install_helper("*Virtual Display*", run=FakeRunner())
    assert display.helper_pattern_path().read_text(encoding="utf-8") == "*Virtual Display*"
    display.uninstall_helper(run=FakeRunner())
    assert not display.helper_pattern_path().exists()


# ---------- real pixels vs desktop coordinates (DPI scaling) ----------

SCALED_4K = Screen("DISPLAY12", 1920, 0, 1920, 1080, primary=False, pixel_width=3840, pixel_height=2160)
REAL_1440 = Screen("DISPLAY2", -2560, 0, 2560, 1440, primary=False, pixel_width=2560, pixel_height=1440)


def test_pixels_fall_back_to_desktop_size():
    assert SCALED_4K.pixels == (3840, 2160)
    assert MAIN.pixels == (MAIN.width, MAIN.height)
    assert "3840x2160" in SCALED_4K.describe()


def test_auto_picks_the_largest_screen_in_real_pixels():
    # The 4K screen at 200% looks smaller on the desktop than the 1440p one, but has more pixels.
    assert pick_screen([MAIN, REAL_1440, SCALED_4K], "auto") == SCALED_4K


def test_smaller_than_the_obs_canvas():
    assert not display.smaller_than(SCALED_4K, (3840, 2160))
    assert display.smaller_than(REAL_1440, (3840, 2160))
    assert display.smaller_than(REAL_1440, (2560, 1600))
