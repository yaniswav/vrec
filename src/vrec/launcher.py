"""Starting OBS and the recording Chrome automatically when they aren't already open.

Both are optional conveniences (features `auto_start_obs`/`auto_start_chrome`, on by default): if
OBS or Chrome is already reachable, vrec never touches them; otherwise it locates the executable,
starts it detached (so it keeps running after vrec exits, and survives even if vrec is killed),
and waits for it to become reachable. Every OS interaction (registry reads, `tasklist`, `Popen`,
the debug-port HTTP check, sleeping/the clock) is an injectable parameter so tests never start a
real program.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import obsws_python as obs

from vrec import obs_control
from vrec.config import Paths, Settings
from vrec.console import ProgressLine
from vrec.display import Runner
from vrec.errors import VrecError
from vrec.features import FeatureSet

RegistryReader = Callable[[str], "str | None"]
PopenFn = Callable[..., "subprocess.Popen[bytes]"]
UrlOpener = Callable[..., Any]
SleepFn = Callable[[float], None]
ClockFn = Callable[[], float]

ConnectFn = Callable[[Settings, Paths], "tuple[obs.ReqClient, str]"]
FindObsFn = Callable[[Settings], "Path | None"]
RunningFn = Callable[[], bool]
StartObsFn = Callable[[Path], None]

FindChromeFn = Callable[[Settings], "Path | None"]
PortOpenFn = Callable[[int], bool]
StartChromeFn = Callable[[Path, int, Path], None]

_OBS_REGISTRY_KEYS = (r"SOFTWARE\OBS Studio", r"SOFTWARE\WOW6432Node\OBS Studio")
_CHROME_APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"

_START_POLL_S = 0.5

ReadyFn = Callable[[obs.ReqClient, float], None]
ProbeFn = Callable[[str, int], bool]


def port_listening(host: str, port: int, timeout: float = 0.3) -> bool:
    """Quick local check that something listens on the port.

    On Windows a refused connection takes about 2 s (the connection is retried), while a local
    listening port answers in well under a millisecond: a short timeout tells them apart at once.
    """
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def _read_registry_default(key_path: str) -> str | None:
    """Read the default (unnamed) string value of an HKEY_LOCAL_MACHINE key. None if missing.

    The body lives entirely under the `sys.platform == "win32"` branch (rather than an early
    `return None` guard clause) so that mypy, checking under either `--platform win32` or
    `--platform linux`, statically excludes just the inapplicable branch instead of flagging the
    other one as unreachable code (see the same pattern in display.list_screens()).
    """
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                value, _kind = winreg.QueryValueEx(key, "")
            return str(value) if value else None
        except OSError:
            return None
    else:
        return None


def _run(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), capture_output=True, text=True, check=False)


# ---------- OBS ----------


def _default_obs_path() -> Path:
    program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    return Path(program_files) / "obs-studio" / "bin" / "64bit" / "obs64.exe"


def find_obs(settings: Settings, read_registry: RegistryReader = _read_registry_default) -> Path | None:
    """The obs64.exe to launch: `[obs] path` if it exists, else the registry, else the default path."""
    if settings.obs_path:
        configured = Path(settings.obs_path)
        if configured.exists():
            return configured
    for key_path in _OBS_REGISTRY_KEYS:
        install_dir = read_registry(key_path)
        if install_dir:
            exe = Path(install_dir) / "bin" / "64bit" / "obs64.exe"
            if exe.exists():
                return exe
    default = _default_obs_path()
    return default if default.exists() else None


def obs_running(run: Runner = _run) -> bool:
    """Whether an obs64.exe process is currently running."""
    result = run(["tasklist", "/FI", "IMAGENAME eq obs64.exe", "/NH"])
    return "obs64.exe" in result.stdout.lower()


def start_obs(exe: Path, popen: PopenFn = subprocess.Popen) -> None:
    """Start OBS, detached from vrec so it keeps running after vrec exits (or is killed).

    Uses `cwd=exe.parent`: OBS fails to find its own files otherwise. `--disable-shutdown-check`
    skips the "safe mode" dialog OBS shows after a crash, which would otherwise block it starting.
    """
    popen(
        [str(exe), "--disable-shutdown-check"],
        cwd=exe.parent,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def ensure_obs(
    settings: Settings,
    paths: Paths,
    features: FeatureSet,
    connect: ConnectFn = obs_control.connect,
    find: FindObsFn = find_obs,
    running: RunningFn = obs_running,
    start: StartObsFn = start_obs,
    sleep: SleepFn = time.sleep,
    clock: ClockFn = time.time,
    ready: ReadyFn = obs_control.wait_until_ready,
    probe: ProbeFn = port_listening,
) -> tuple[obs.ReqClient, str]:
    """Connect to OBS, starting it first if it isn't open (feature `auto_start_obs`).

    A wrong-password error (a plain VrecError, not ObsUnreachable) is never treated as "OBS isn't
    open": it propagates immediately, same as without this feature.
    """
    print("Connecting to OBS...")
    # Probe first: a closed port costs ~2 s per connection attempt on Windows, a probe 0.3 s at most.
    if probe(settings.obs_host, settings.obs_port):
        try:
            client, password = connect(settings, paths)
            ready(client, settings.obs_start_timeout_s)
            return client, password
        except obs_control.ObsUnreachable:
            if not features.enabled("auto_start_obs"):
                raise
    elif not features.enabled("auto_start_obs"):
        raise obs_control.ObsUnreachable()

    if running():
        raise VrecError(
            "OBS is open but its WebSocket server doesn't answer. In OBS: Tools > WebSocket Server "
            "Settings > Enable WebSocket server."
        )
    exe = find(settings)
    if exe is None:
        raise VrecError(
            "OBS isn't open and wasn't found. Open it yourself, or set [obs] path in config.toml."
        )

    print("Starting OBS...")
    start(exe)
    started = clock()
    deadline = started + settings.obs_start_timeout_s
    line = ProgressLine()
    while True:
        sleep(_START_POLL_S)
        line.show(f"Waiting for OBS to start... {clock() - started:.0f} s")
        try:
            if not probe(settings.obs_host, settings.obs_port):
                raise obs_control.ObsUnreachable()
            client, password = connect(settings, paths)
        except obs_control.ObsUnreachable:
            if clock() >= deadline:
                line.end()
                raise VrecError(
                    "OBS started, but its WebSocket server doesn't answer. In OBS: Tools > WebSocket "
                    "Server Settings > Enable WebSocket server."
                ) from None
            continue
        line.end()
        ready(client, max(deadline - clock(), 5.0))
        print("OBS started.")
        return client, password


# ---------- Chrome ----------


def _chrome_standard_paths() -> list[Path]:
    """The same 3 locations scripts/windows/launch_chrome.bat tries, in the same order."""
    program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    paths = [
        Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        paths.append(Path(local_appdata) / "Google" / "Chrome" / "Application" / "chrome.exe")
    return paths


def find_chrome(settings: Settings, read_registry: RegistryReader = _read_registry_default) -> Path | None:
    """The chrome.exe to launch: `[chrome] path`, else the standard install paths, else the registry."""
    if settings.chrome_path:
        configured = Path(settings.chrome_path)
        if configured.exists():
            return configured
    for candidate in _chrome_standard_paths():
        if candidate.exists():
            return candidate
    install_path = read_registry(_CHROME_APP_PATHS_KEY)
    if install_path:
        exe = Path(install_path)
        if exe.exists():
            return exe
    return None


def chrome_profile_dir(settings: Settings) -> Path:
    """The Chrome profile directory: `[chrome] profile`, else `VREC_CHROME_PROFILE`, else the default.

    Resolved the same way as scripts/windows/launch_chrome.bat, so both use the same profile.
    """
    if settings.chrome_profile:
        return Path(settings.chrome_profile)
    env = os.environ.get("VREC_CHROME_PROFILE")
    if env:
        return Path(env)
    local_appdata = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local_appdata) / "vrec" / "chrome-profile"


def chrome_port_open(
    port: int, urlopen: UrlOpener = urllib.request.urlopen, probe: Callable[[str, int], bool] = port_listening
) -> bool:
    """Whether Chrome's DevTools debugging port answers."""
    if not probe("127.0.0.1", port):
        return False
    url = f"http://127.0.0.1:{port}/json/version"
    try:
        with urlopen(url, timeout=2):  # noqa: S310 - local debug port only
            return True
    except Exception:
        return False


# A fresh profile would otherwise open welcome / default-browser / search-engine screens on top.
CHROME_QUIET_FLAGS = ("--no-first-run", "--no-default-browser-check", "--disable-search-engine-choice-screen")


def start_chrome(exe: Path, port: int, profile: Path, popen: PopenFn = subprocess.Popen) -> None:
    """Start the recording Chrome, detached, with the same flags as launch_chrome.bat."""
    popen(
        [
            str(exe),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--autoplay-policy=no-user-gesture-required",
            "--disable-features=CalculateNativeWinOcclusion",
            "--disable-backgrounding-occluded-windows",
            *CHROME_QUIET_FLAGS,
        ],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def ensure_chrome(
    settings: Settings,
    features: FeatureSet,
    port_open: PortOpenFn = chrome_port_open,
    find: FindChromeFn = find_chrome,
    start: StartChromeFn = start_chrome,
    sleep: SleepFn = time.sleep,
    clock: ClockFn = time.time,
) -> None:
    """Start the recording Chrome if its debug port doesn't answer (feature `auto_start_chrome`)."""
    port = settings.chrome_port
    if port_open(port):
        return
    if not features.enabled("auto_start_chrome"):
        return

    exe = find(settings)
    if exe is None:
        raise VrecError("Chrome wasn't found. Install it, or set [chrome] path in config.toml.")

    profile = chrome_profile_dir(settings)
    print("Starting the recording Chrome...")
    start(exe, port, profile)

    deadline = clock() + settings.chrome_start_timeout_s
    while not port_open(port):
        if clock() >= deadline:
            raise VrecError(f"Chrome started, but its debugging port doesn't answer (port {port}).")
        sleep(_START_POLL_S)
    print("Recording Chrome started. If the site needs it, log in in that window.")
