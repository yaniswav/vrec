"""Screens: find the virtual display, and optionally turn it on/off around a batch.

Listing screens uses the Windows API through ctypes (no extra dependency). Turning a display
device on or off needs administrator rights, so `install_helper()` (run once from an admin
terminal) registers two on-demand scheduled tasks that run with the highest privileges;
vrec then triggers them with `schtasks /Run`, which needs no elevation and shows no prompt.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from vrec.errors import VrecError

TASK_FOLDER = "vrec"
TASK_ON = rf"\{TASK_FOLDER}\display-on"
TASK_OFF = rf"\{TASK_FOLDER}\display-off"

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def _run(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), capture_output=True, text=True, check=False)


@dataclass(frozen=True)
class Screen:
    name: str
    left: int
    top: int
    width: int
    height: int
    primary: bool
    # The screen's real resolution in pixels (0 = unknown). `width`/`height` are desktop
    # coordinates, which Windows may scale for DPI.
    pixel_width: int = 0
    pixel_height: int = 0

    @property
    def pixels(self) -> tuple[int, int]:
        return (self.pixel_width, self.pixel_height) if self.pixel_width else (self.width, self.height)

    @property
    def center(self) -> tuple[int, int]:
        return self.left + self.width // 2, self.top + self.height // 2

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x < self.left + self.width and self.top <= y < self.top + self.height

    def describe(self) -> str:
        kind = "main screen" if self.primary else "screen"
        w, h = self.pixels
        return f"{self.name} ({w}x{h}, {kind})"


def list_screens() -> list[Screen]:
    """Screens currently attached to the desktop. Empty on non-Windows systems.

    The body lives entirely under the `sys.platform == "win32"` branch (rather than an early
    `return []` guard clause) so that mypy, checking under either `--platform win32` or
    `--platform linux`, statically excludes just the inapplicable branch instead of flagging the
    other one as unreachable code.
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class MONITORINFOEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32),
            ]

        class DEVMODEW(ctypes.Structure):
            _fields_ = [
                ("dmDeviceName", wintypes.WCHAR * 32),
                ("dmSpecVersion", wintypes.WORD),
                ("dmDriverVersion", wintypes.WORD),
                ("dmSize", wintypes.WORD),
                ("dmDriverExtra", wintypes.WORD),
                ("dmFields", wintypes.DWORD),
                ("dmPositionX", wintypes.LONG),
                ("dmPositionY", wintypes.LONG),
                ("dmDisplayOrientation", wintypes.DWORD),
                ("dmDisplayFixedOutput", wintypes.DWORD),
                ("dmColor", wintypes.SHORT),
                ("dmDuplex", wintypes.SHORT),
                ("dmYResolution", wintypes.SHORT),
                ("dmTTOption", wintypes.SHORT),
                ("dmCollate", wintypes.SHORT),
                ("dmFormName", wintypes.WCHAR * 32),
                ("dmLogPixels", wintypes.WORD),
                ("dmBitsPerPel", wintypes.DWORD),
                ("dmPelsWidth", wintypes.DWORD),
                ("dmPelsHeight", wintypes.DWORD),
                ("dmDisplayFlags", wintypes.DWORD),
                ("dmDisplayFrequency", wintypes.DWORD),
                ("dmICMMethod", wintypes.DWORD),
                ("dmICMIntent", wintypes.DWORD),
                ("dmMediaType", wintypes.DWORD),
                ("dmDitherType", wintypes.DWORD),
                ("dmReserved1", wintypes.DWORD),
                ("dmReserved2", wintypes.DWORD),
                ("dmPanningWidth", wintypes.DWORD),
                ("dmPanningHeight", wintypes.DWORD),
            ]

        user32 = ctypes.windll.user32
        screens: list[Screen] = []

        def pixel_size(device: str) -> tuple[int, int]:
            mode = DEVMODEW()
            mode.dmSize = ctypes.sizeof(mode)
            if user32.EnumDisplaySettingsW(device, -1, ctypes.byref(mode)):  # -1: current settings
                return int(mode.dmPelsWidth), int(mode.dmPelsHeight)
            return 0, 0

        def on_monitor(
            handle: wintypes.HMONITOR,
            _hdc: wintypes.HDC,
            _rect: ctypes._Pointer[wintypes.RECT],
            _data: wintypes.LPARAM,
        ) -> int:
            info = MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(info)
            if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
                r = info.rcMonitor
                pixel_width, pixel_height = pixel_size(info.szDevice)
                screens.append(
                    Screen(
                        name=info.szDevice.removeprefix("\\\\.\\"),
                        left=r.left,
                        top=r.top,
                        width=r.right - r.left,
                        height=r.bottom - r.top,
                        primary=bool(info.dwFlags & 1),
                        pixel_width=pixel_width,
                        pixel_height=pixel_height,
                    )
                )
            return 1

        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
        )
        user32.EnumDisplayMonitors(None, None, callback_type(on_monitor), 0)
        return screens
    else:
        return []


def _area(screen: Screen) -> int:
    """Real pixel count: desktop sizes are scaled differently per screen with DPI settings."""
    w, h = screen.pixels
    return w * h


def smaller_than(screen: Screen, canvas: tuple[int, int]) -> bool:
    """Whether the screen has fewer pixels than OBS's canvas in either direction (upscaled video)."""
    w, h = screen.pixels
    return w < canvas[0] or h < canvas[1]


def pick_screen(screens: Sequence[Screen], wanted: str = "auto") -> Screen | None:
    """The screen to record on.

    "auto": the largest screen that isn't the main one, or the main screen when it is the only
    one. Otherwise a 1-based index ("2") or part of the screen name ("DISPLAY3"), case-insensitive.
    """
    wanted = wanted.strip()
    if not screens:
        return None
    if wanted.lower() in ("", "auto"):
        others = [s for s in screens if not s.primary]
        if others:
            return max(others, key=_area)
        return screens[0] if len(screens) == 1 else None
    if wanted.isdigit():
        index = int(wanted) - 1
        return screens[index] if 0 <= index < len(screens) else None
    return next((s for s in screens if wanted.lower() in s.name.lower()), None)


def wait_for_new_screen(
    before: Sequence[Screen],
    timeout_s: float = 15,
    lister: Callable[[], list[Screen]] = list_screens,
    sleep: Callable[[float], None] = time.sleep,
) -> Screen | None:
    """Poll until a screen that wasn't in `before` shows up: the display that was just turned on."""
    known = set(before)
    waited = 0.0
    while True:
        new = [s for s in lister() if s not in known]
        if new or waited >= timeout_s:
            return max(new, key=_area) if new else None
        sleep(0.5)
        waited += 0.5


# ---------- Turning the virtual display on/off (optional) ----------


# The elevated tasks run this script, so it must not be writable without admin rights: it lives in
# %ProgramData%\vrec with an ACL that only lets Administrators and SYSTEM change it.
_ADMIN_ONLY_ACL = (
    "/inheritance:r",
    "/grant:r",
    "*S-1-5-32-544:(OI)(CI)F",  # Administrators: full control
    "/grant:r",
    "*S-1-5-18:(OI)(CI)F",  # SYSTEM: full control
    "/grant:r",
    "*S-1-5-32-545:(OI)(CI)RX",  # Users: read and run only
)
# Adapter names are matched with PowerShell's -like: letters, digits, spaces and a few symbols are
# enough, and anything else (quotes, typographic quotes, `;`, `$`...) could break out of the string.
_PATTERN_RE = re.compile(r"[\w .()\-*?\[\]]{1,100}")


def helper_dir() -> Path:
    return Path(os.environ.get("PROGRAMDATA") or "C:/ProgramData") / "vrec"


def helper_script_path() -> Path:
    return helper_dir() / "virtual-display.ps1"


def legacy_helper_dir() -> Path:
    """Where vrec 0.1 betas wrote the helper (user-writable): cleaned up on install/uninstall."""
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "vrec"


def check_pattern(device_pattern: str) -> str:
    """The pattern, if it is safe to put in the helper script; VrecError otherwise."""
    if not _PATTERN_RE.fullmatch(device_pattern):
        raise VrecError(
            f"Unsupported adapter pattern: {device_pattern!r}. "
            "Use letters, digits, spaces and * ? . ( ) - [ ] only."
        )
    return device_pattern


def helper_pattern_path() -> Path:
    """Where the adapter name pattern chosen at install time is kept (to read the device state)."""
    return helper_script_path().with_name("virtual-display-pattern.txt")


def helper_script(device_pattern: str) -> str:
    """PowerShell run by the scheduled tasks: enable/disable the display adapters matching the pattern."""
    pattern = check_pattern(device_pattern)
    return (
        "param([ValidateSet('on', 'off')][string]$State)\r\n"
        "# Created by vrec --install-display-helper. Removed by vrec --uninstall-display-helper.\r\n"
        f"$devices = Get-PnpDevice -Class Display | Where-Object {{ $_.FriendlyName -like '{pattern}' }}\r\n"
        "if ($State -eq 'on') { $devices | Enable-PnpDevice -Confirm:$false }\r\n"
        "else { $devices | Disable-PnpDevice -Confirm:$false }\r\n"
    )


def list_display_devices(run: Runner = _run) -> list[str]:
    """Friendly names of the display adapters Windows knows about (no admin needed)."""
    result = run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-PnpDevice -Class Display | ForEach-Object { $_.FriendlyName }",
        ]
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def virtual_display_enabled(run: Runner = _run) -> bool | None:
    """Whether the virtual display adapter is currently enabled (no admin needed).

    None when it can't be told: helper not installed, or no adapter matches its pattern.
    """
    try:
        quoted = check_pattern(helper_pattern_path().read_text(encoding="utf-8").strip())
    except (OSError, VrecError):
        return None
    result = run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"Get-PnpDevice -Class Display | Where-Object {{ $_.FriendlyName -like '{quoted}' }} "
            "| ForEach-Object { $_.Status }",
        ]
    )
    statuses = [line.strip().lower() for line in result.stdout.splitlines() if line.strip()]
    if not statuses:
        return None
    return "ok" in statuses


def is_admin() -> bool:
    if sys.platform == "win32":
        import ctypes

        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except OSError:
            return False
    else:
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False


def _task_command(script: Path, state: str) -> str:
    return f'powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{script}" {state}'


def install_helper(device_pattern: str, run: Runner = _run) -> Path:
    """Write the helper script and register the two elevated on-demand tasks (needs admin)."""
    if sys.platform != "win32":
        raise VrecError("Turning the virtual display on/off is only supported on Windows.")
    else:
        # Delegated to a helper with no `sys.platform` check of its own: mypy narrows the
        # branch above per --platform, and would otherwise consider everything after it
        # (in this same function) unreachable when checking under a non-Windows platform.
        return _install_helper(device_pattern, run)


def _install_helper(device_pattern: str, run: Runner) -> Path:
    if not is_admin():
        raise VrecError(
            "This needs administrator rights once: open a terminal with 'Run as administrator' and run "
            "vrec --install-display-helper again."
        )
    content = helper_script(device_pattern)  # validates the pattern before anything is written
    folder = helper_dir()
    folder.mkdir(parents=True, exist_ok=True)
    locked = run(["icacls", str(folder), *_ADMIN_ONLY_ACL])
    if locked.returncode != 0:
        raise VrecError(f"Couldn't protect {folder}: {locked.stderr.strip() or locked.stdout.strip()}")
    script = helper_script_path()
    script.write_text(content, encoding="utf-8", newline="")
    helper_pattern_path().write_text(device_pattern, encoding="utf-8")
    _remove_legacy_files()
    for task, state in ((TASK_ON, "on"), (TASK_OFF, "off")):
        # A one-time trigger in the past: the task never runs by itself, only on demand.
        result = run(
            [
                "schtasks", "/Create", "/F", "/TN", task, "/TR", _task_command(script, state),
                "/SC", "ONCE", "/SD", "01/01/2000", "/ST", "00:00", "/RL", "HIGHEST",
            ]
        )  # fmt: skip
        if result.returncode != 0:
            raise VrecError(
                f"Couldn't create the scheduled task {task}: {result.stderr.strip() or result.stdout}"
            )
    return script


def uninstall_helper(run: Runner = _run) -> None:
    for task in (TASK_ON, TASK_OFF):
        run(["schtasks", "/Delete", "/F", "/TN", task])
    helper_script_path().unlink(missing_ok=True)
    helper_pattern_path().unlink(missing_ok=True)
    _remove_legacy_files()


def _remove_legacy_files() -> None:
    for name in ("virtual-display.ps1", "virtual-display-pattern.txt"):
        (legacy_helper_dir() / name).unlink(missing_ok=True)


def set_virtual_display(on: bool, run: Runner = _run) -> None:
    """Trigger the helper task. Raises VrecError if it isn't installed or can't start."""
    task = TASK_ON if on else TASK_OFF
    result = run(["schtasks", "/Run", "/TN", task])
    if result.returncode != 0:
        raise VrecError(
            "The virtual display helper isn't installed (run vrec --install-display-helper as admin), "
            "or it couldn't start."
        )
