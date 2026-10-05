"""`vrec --setup`: a step-by-step first-run wizard. Safe to re-run, never overwrites anything.

Steps: files, requirements (the doctor's checks), virtual display control, optional features,
summary. Input and output are injectable (see Seams) so tests drive it with scripted answers.
"""

from __future__ import annotations

import getpass
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from vrec import display, doctor, launcher
from vrec.config import Paths, Settings, load_settings
from vrec.console import first_line
from vrec.doctor import Check
from vrec.errors import VrecError
from vrec.features import FeatureSet, load_features, save_features

DISPLAY_FEATURE = "manage_virtual_display"
_DEFAULT_ADAPTER_PATTERN = "*Virtual*"
_STATUS_TEXT = doctor._LABELS


def _like(name: str, pattern: str) -> bool:
    from fnmatch import fnmatchcase

    return fnmatchcase(name.lower(), pattern.lower())


def _elevated_command() -> tuple[str, str]:
    """(program, arguments) that run `vrec --install-display-helper` as exe or as `python -m vrec`."""
    tail = f"--install-display-helper {_DEFAULT_ADAPTER_PATTERN} --pause-on-exit"
    if getattr(sys, "frozen", False):
        return sys.executable, tail
    return sys.executable, f"-m vrec {tail}"


def shell_execute_wait(program: str, params: str) -> bool:  # pragma: no cover - real UAC prompt
    """Run a program elevated (UAC prompt), wait for it to end. False if the user said no."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [  # noqa: RUF012 - ctypes layout
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(ShellExecuteInfo)
    info.fMask = 0x40  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = program
    info.lpParameters = params
    info.lpDirectory = str(Path.cwd())
    info.nShow = 1
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)) or not info.hProcess:
        return False
    ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
    ctypes.windll.kernel32.CloseHandle(info.hProcess)
    return True


def _install_in_process() -> bool:
    """Install the display helper from this (already elevated) process."""
    import argparse

    from vrec.__main__ import _run_display_helper

    args = argparse.Namespace(install_display_helper=_DEFAULT_ADAPTER_PATTERN, uninstall_display_helper=False)
    return _run_display_helper(args) == 0


def _example_dirs() -> list[Path]:
    root = (
        Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    )
    return [root, Path.cwd()]


def _find_example(name: str) -> Path | None:
    for folder in _example_dirs():
        if (folder / name).is_file():
            return folder / name
    return None


def run_checks(settings: Settings, paths: Paths, features: FeatureSet) -> list[Check]:
    """The doctor's checks that matter for a first setup, plus a Chrome-installed check."""
    ctx = doctor._Context(settings=settings, paths=paths, features=features)

    def chrome_installed(_ctx: doctor._Context) -> Check:
        exe = launcher.find_chrome(settings)
        if exe is None:
            return Check(
                "fail",
                "Chrome",
                detail="Not found.",
                hint="Install Chrome, or set [chrome] path in config.toml.",
            )
        return Check("ok", "Chrome", detail=f"Found: {exe}")

    functions = [
        doctor.check_obs_connection,
        doctor.check_vb_cable,
        chrome_installed,
        doctor.check_virtual_screen,
    ]
    results = []
    for function in functions:
        try:
            results.append(function(ctx))
        except Exception as e:
            title = (function.__doc__ or function.__name__).strip().rstrip(".")
            results.append(Check("fail", title, detail=first_line(e)))
    return results


@dataclass
class Seams:
    """Everything the wizard takes from the outside, so tests can fake it."""

    ask: Callable[[str], str] = input
    say: Callable[[str], None] = print
    secret: Callable[[str], str] = getpass.getpass
    interactive: bool = field(default_factory=lambda: bool(sys.stdin and sys.stdin.isatty()))
    is_admin: Callable[[], bool] = display.is_admin
    elevate: Callable[[str, str], bool] = shell_execute_wait
    install_here: Callable[[], bool] = _install_in_process
    list_devices: Callable[[], list[str]] = display.list_display_devices
    helper_state: Callable[[], bool | None] = display.virtual_display_enabled
    run_checks: Callable[[Settings, Paths, FeatureSet], list[Check]] = run_checks


def _yes(io: Seams, question: str, default: bool) -> bool:
    """Ask a yes/no question, the default shown in brackets and taken on Enter."""
    hint = "Y/n" if default else "y/N"
    while True:
        answer = io.ask(f"{question} [{hint}]: ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes", "on"):
            return True
        if answer in ("n", "no", "off"):
            return False
        io.say("   Please answer y or n (Enter keeps the value in brackets).")


def _show_check(io: Seams, check: Check) -> None:
    line = f"  {_STATUS_TEXT.get(check.status, '[ ?? ]')} {check.title}"
    if check.detail:
        line += f": {check.detail}"
    io.say(line)
    if check.hint:
        io.say(f"         -> {check.hint}")


@dataclass
class _Report:
    done: list[str] = field(default_factory=list)
    todo: list[str] = field(default_factory=list)


# ---------- step 1: files ----------


def _copy_if_missing(io: Seams, target: Path, example_name: str, report: _Report) -> None:
    if target.exists():
        io.say(f"  Already there: {target}")
        return
    example = _find_example(example_name)
    if example is None:
        io.say(f"  Can't create {target.name}: {example_name} not found next to vrec.")
        report.todo.append(f"Create {target} (copy {example_name} from the vrec download).")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(example, target)
    io.say(f"  Created: {target} (copy of {example_name})")


def _step_files(io: Seams, paths: Paths, report: _Report) -> None:
    io.say("\nStep 1 of 5: your files")
    if paths.data_dir.exists():
        io.say(f"  Already there: {paths.data_dir}")
    else:
        paths.data_dir.mkdir(parents=True, exist_ok=True)
        io.say(f"  Created: {paths.data_dir}")
    _copy_if_missing(io, paths.config, "config.example.toml", report)
    _copy_if_missing(io, paths.videos, "videos.example.txt", report)
    example = _find_example("videos.example.txt")
    if paths.videos.exists() and example is not None:
        same = paths.videos.read_text(encoding="utf-8").strip() == example.read_text(encoding="utf-8").strip()
        if same:
            io.say(f"  {paths.videos.name} still holds the example links: replace them with your own.")
            report.todo.append(f"Put your own links in {paths.videos}.")
        else:
            report.done.append("Playlist file")


# ---------- step 2: requirements ----------


def _offer_password(io: Seams, paths: Paths) -> None:
    import os

    if os.environ.get("VREC_OBS_PASSWORD") or paths.password.exists():
        return
    io.say("  No OBS WebSocket password saved yet.")
    io.say("  In OBS: Tools > WebSocket Server Settings > Show Connect Info")
    if not _yes(io, "  Save the password now?", True):
        return
    password = io.secret("  Paste the WebSocket server password here (hidden): ").strip()
    if not password:
        io.say("  Nothing typed: skipped.")
        return
    paths.password.write_text(password, encoding="utf-8")
    io.say(f"  Saved in {paths.password}.")


def _step_requirements(
    io: Seams, paths: Paths, settings: Settings, features: FeatureSet, report: _Report
) -> None:
    io.say("\nStep 2 of 5: what vrec needs")
    _offer_password(io, paths)
    io.say("  Playwright: vrec drives your installed Chrome, so no extra browser download is needed.")
    for check in io.run_checks(settings, paths, features):
        _show_check(io, check)
        if check.status in ("fail", "warn"):
            report.todo.append(f"{check.title}: {check.hint or check.detail}")
        elif check.status == "ok":
            report.done.append(check.title)


# ---------- step 3: virtual display control ----------


def _install_helper(io: Seams) -> bool:
    if io.is_admin():
        io.say("  Installing the helper...")
        return io.install_here()
    program, params = _elevated_command()
    io.say("  Installing it needs administrator rights. Windows will ask for your permission.")
    if not io.elevate(program, params):
        io.say("  The administrator step didn't run (cancelled or refused).")
        return False
    return True


def _step_display(io: Seams, features: FeatureSet, report: _Report) -> None:
    io.say("\nStep 3 of 5: virtual display control (optional)")
    io.say(f"  Feature {DISPLAY_FEATURE}: vrec turns the virtual display on before a batch and off after it.")
    io.say("  It needs a small helper installed once, with administrator rights.")
    state = io.helper_state()
    if state is None:
        matching = [d for d in io.list_devices() if _like(d, _DEFAULT_ADAPTER_PATTERN)]
        if not matching:
            io.say(
                f"  No display adapter matches '{_DEFAULT_ADAPTER_PATTERN}': skipped. "
                "See docs/virtual-display.md."
            )
            return
        io.say(f"  The helper isn't installed. It would switch: {', '.join(matching)}")
        if not _yes(io, "  Install it now?", False):
            return
        if _install_helper(io):
            state = io.helper_state()
        if state is None:
            io.say("  The helper still isn't installed. Try again later: vrec --install-display-helper")
            report.todo.append(
                "Virtual display helper: run setup again, or vrec --install-display-helper (as admin)."
            )
            return
        io.say("  Helper installed.")
    else:
        io.say("  The helper is already installed.")
    report.done.append("Virtual display helper")
    features.set(DISPLAY_FEATURE, _yes(io, f"  Turn {DISPLAY_FEATURE} on?", True))


# ---------- step 4: features ----------


def _walk_features(io: Seams, features: FeatureSet) -> None:
    for feature, value in features.items():
        if feature.name == DISPLAY_FEATURE:
            continue  # asked in step 3
        state = "ON" if value else "OFF"
        io.say(f"\n  {feature.name}: {feature.description}")
        while True:
            answer = io.ask(f"  Now {state}. Keep / on / off? [Keep]: ").strip().lower()
            if answer in ("", "k", "keep"):
                break
            if answer in ("y", "on"):
                features.set(feature.name, True)
                break
            if answer in ("n", "off"):
                features.set(feature.name, False)
                break
            io.say("   Type Enter (keep), on (or y) or off (or n).")


def _step_features(io: Seams, paths: Paths, features: FeatureSet, before: dict[str, bool]) -> None:
    io.say("\nStep 4 of 5: optional features")
    if _yes(io, "  Go through them one by one?", False):
        _walk_features(io, features)
    after = {feature.name: value for feature, value in features.items()}
    changed = [name for name in after if after[name] != before[name]]
    if not changed:
        io.say("  No feature changed.")
        return
    save_features(paths.features, features)
    io.say(
        f"  Saved {paths.features.name}: " + ", ".join(f"{n} {'on' if after[n] else 'off'}" for n in changed)
    )


# ---------- step 5: summary ----------


def _step_summary(io: Seams, report: _Report) -> None:
    io.say("\nStep 5 of 5: summary")
    io.say("  Ready: " + (", ".join(report.done) if report.done else "nothing yet"))
    if report.todo:
        io.say("  Still to do:")
        for item in report.todo:
            io.say(f"    - {item}")
    else:
        io.say("  Nothing left to do.")
    io.say("\n  Next:")
    io.say("    launch_chrome.bat   open the recording Chrome and log in to your site")
    io.say("    test.bat            record 30 seconds as a check")
    io.say("    start.bat           record your list")


def run_setup(data_dir: Path, config_path: Path | None, io: Seams | None = None) -> int:
    """Run the wizard. Returns 0 when done, 1 if a non-interactive check failed, 130 on Ctrl+C."""
    io = io or Seams()
    paths = Paths(data_dir=data_dir, config=config_path or data_dir / "config.toml")
    try:
        features, warnings = load_features(paths.features)
    except VrecError as e:
        io.say(str(e))
        features, warnings = None, []
    for message in warnings:
        io.say(message)
    try:
        settings = load_settings(paths.config)
    except VrecError:
        settings = Settings()

    io.say("===== VREC SETUP =====")
    if not io.interactive:
        io.say("No keyboard available: showing the checks only. Run setup.bat in a normal window to set up.")
        checks = io.run_checks(settings, paths, features or FeatureSet())
        for check in checks:
            _show_check(io, check)
        return 1 if any(c.status == "fail" for c in checks) else 0

    io.say("Press Enter to accept the answer in [brackets]. Ctrl+C stops at any time.")
    report = _Report()
    try:
        _step_files(io, paths, report)
        # Re-read after step 1: the config may have just been created.
        try:
            settings = load_settings(paths.config)
        except VrecError:
            settings = Settings()
        _step_requirements(io, paths, settings, features or FeatureSet(), report)
        if features is None:
            io.say(f"\nSteps 3 and 4 skipped: fix or delete {paths.features} first.")
            report.todo.append(f"Fix {paths.features} (invalid), then run setup again.")
        else:
            before = {feature.name: value for feature, value in features.items()}
            _step_display(io, features, report)
            _step_features(io, paths, features, before)
        _step_summary(io, report)
    except (KeyboardInterrupt, EOFError):
        io.say("\nSetup stopped. No feature changes were saved.")
        return 130
    return 0
