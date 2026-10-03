"""Command-line entry point: `python -m vrec` / the `vrec` console script."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vrec.config import Paths
    from vrec.features import FeatureSet


def _build_parser() -> argparse.ArgumentParser:
    from vrec import __version__

    parser = argparse.ArgumentParser(
        prog="vrec", description="Automatic video recording driven by OBS and Chrome."
    )
    parser.add_argument(
        "--test", action="store_true", help="record 30 s of the first video and run a diagnostic"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="folder for videos.txt, config.toml, history.json (default: VREC_DATA_DIR env var, else ./data)",
    )
    parser.add_argument(
        "--config", type=Path, default=None, help="config file path (default: <data-dir>/config.toml)"
    )
    parser.add_argument(
        "--pause-on-exit",
        action="store_true",
        help="wait for Enter before closing (used by the .bat files)",
    )
    parser.add_argument(
        "--features", action="store_true", help="show which optional features are on or off, then exit"
    )
    parser.add_argument(
        "--enable", nargs="+", metavar="NAME", default=None, help="turn one or more features on"
    )
    parser.add_argument(
        "--disable", nargs="+", metavar="NAME", default=None, help="turn one or more features off"
    )
    parser.add_argument("--version", action="version", version=f"vrec {__version__}")
    parser.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    _add_schedule_arguments(parser)
    _add_selection_arguments(parser)
    _add_display_arguments(parser)
    _add_diagnostic_arguments(parser)
    parser.add_argument(
        "--launch-chrome",
        action="store_true",
        help="open the recording Chrome on the virtual screen (e.g. to log in), then exit",
    )
    return parser


def _add_selection_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--all", action="store_true", help="record every video still to do (NEW/FAILED), no menu"
    )
    group.add_argument(
        "--only", metavar="LIST", default=None, help="record exactly these numbers, e.g. 3,1,5-8, no menu"
    )


def _add_diagnostic_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--doctor", action="store_true", help="check your setup (OBS, Chrome, disk...) without recording"
    )


def _add_display_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--install-display-helper",
        nargs="?",
        const="*Virtual*",
        metavar="PATTERN",
        help="once, as administrator: let vrec turn the virtual display on/off "
        "(PATTERN matches the display adapter name, default *Virtual*)",
    )
    group.add_argument(
        "--uninstall-display-helper", action="store_true", help="remove what --install-display-helper added"
    )


def _run_display_helper(args: argparse.Namespace) -> int:
    """Handle --install-display-helper / --uninstall-display-helper: no OBS/Chrome, no instance lock."""
    from vrec import display
    from vrec.errors import VrecError

    try:
        if args.uninstall_display_helper:
            display.uninstall_helper()
            print("Virtual display helper removed.")
            return 0

        pattern = args.install_display_helper
        devices = display.list_display_devices()
        matching = [d for d in devices if _like(d, pattern)]
        if not matching:
            print(f"No display adapter matches '{pattern}'. Adapters found:")
            for device in devices:
                print(f"  - {device}")
            print('Run again with a pattern that matches yours, e.g. --install-display-helper "*Virtual*".')
            return 1
        script = display.install_helper(pattern)
    except VrecError as e:
        print(str(e))
        return 1
    print(f"Virtual display helper installed ({script}). It will switch: {', '.join(matching)}")
    print("Now turn the feature on: vrec --enable manage_virtual_display")
    return 0


def _like(name: str, pattern: str) -> bool:
    """PowerShell-style -like matching (case-insensitive wildcards), as the helper script uses."""
    from fnmatch import fnmatchcase

    return fnmatchcase(name.lower(), pattern.lower())


def _run_features(args: argparse.Namespace, data_dir: Path) -> int:
    """Handle --features/--enable/--disable: no OBS/Chrome, no instance lock."""
    from vrec.config import Paths
    from vrec.errors import VrecError
    from vrec.features import LEGEND, feature_names, load_features, render_lines, save_features

    paths = Paths(data_dir=data_dir, config=data_dir / "config.toml")

    try:
        features, warnings = load_features(paths.features)
    except VrecError as e:
        print(str(e))
        return 1
    for message in warnings:
        print(message)

    names = [*(args.enable or []), *(args.disable or [])]
    unknown = [name for name in names if name not in feature_names()]
    if unknown:
        print(f"Unknown feature: {', '.join(unknown)}")
        print(f"Valid names: {', '.join(feature_names())}")
        return 1

    if args.enable or args.disable:
        for name in args.enable or []:
            features.set(name, True)
        for name in args.disable or []:
            features.set(name, False)
        save_features(paths.features, features)

    for line in render_lines(features):
        print(line)
    print(LEGEND)
    return 0


def _add_schedule_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--schedule",
        nargs="+",
        metavar="ACTION",
        help="manage a scheduled unattended run: 'on HH:MM', 'off', or 'status'",
    )
    parser.add_argument(
        "--days",
        default=None,
        metavar="MON,TUE,...",
        help="days for '--schedule on' (default: every day)",
    )


def _selftest() -> int:
    """Import every dependency and load every bundled JS file (used to smoke-test vrec.exe)."""
    import importlib
    import importlib.resources

    for module in ("vrec.app", "vrec.doctor", "playwright.sync_api", "obsws_python", "PIL.Image", "pyvda"):
        try:
            importlib.import_module(module)
        except Exception as e:
            print(f"selftest failed: cannot import {module}: {type(e).__name__}: {e}")
            return 1

    from vrec.browser import load_js

    try:
        names = sorted(
            entry.name
            for entry in importlib.resources.files("vrec").joinpath("js").iterdir()
            if entry.name.endswith(".js")
        )
        if not names:
            print("selftest failed: no JS files found")
            return 1
        for name in names:
            if not load_js(name).strip():
                print(f"selftest failed: JS file {name} is empty")
                return 1
    except Exception as e:
        print(f"selftest failed: cannot load JS files: {type(e).__name__}: {e}")
        return 1
    print("selftest ok")
    return 0


def _run(args: argparse.Namespace, argv: list[str]) -> int:
    if args.selftest:
        return _selftest()
    if args.schedule is not None:
        return _run_schedule(args)
    if args.install_display_helper or args.uninstall_display_helper:
        return _run_display_helper(args)

    data_dir = args.data_dir or Path(os.environ.get("VREC_DATA_DIR", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    if args.features or args.enable or args.disable:
        return _run_features(args, data_dir)

    config_path = args.config or (data_dir / "config.toml")

    from vrec.config import Paths
    from vrec.console import first_line, no_quick_edit
    from vrec.errors import VrecError
    from vrec.features import FeatureSet, load_features
    from vrec.logs import capture

    paths = Paths(data_dir=data_dir, config=config_path)
    try:
        features, _warnings = load_features(paths.features)  # the app and the doctor report the warnings
    except VrecError:
        features = FeatureSet()

    guard = no_quick_edit() if features.enabled("protect_console") else contextlib.nullcontext()
    with capture(data_dir, argv, features) as log, guard:
        try:
            return _dispatch(args, paths, features)
        except VrecError as e:
            print(str(e))
            return 1
        except KeyboardInterrupt:
            print("Stopped.")
            return 130
        except Exception as e:
            log.log_exception(e)
            if log.path:
                print(f"Unexpected error: {first_line(e)}. Details in {log.path}")
            else:
                traceback.print_exc()
            return 1


def _dispatch(args: argparse.Namespace, paths: Paths, features: FeatureSet) -> int:
    """Run the doctor or a recording session (inside the run log)."""
    try:
        from vrec import app, doctor
    except ImportError as e:
        from vrec.console import first_line

        print(f"Missing dependency: {first_line(e)}")
        if getattr(sys, "frozen", False):
            print("This download looks incomplete or corrupted. Download the zip again and re-extract it.")
        else:
            print("Run scripts\\windows\\install.bat (or: pip install -e .).")
        return 1

    if args.launch_chrome:
        return app.launch_chrome(paths.data_dir, paths.config)

    if args.doctor:
        from vrec.config import Settings, load_settings
        from vrec.errors import VrecError

        try:
            settings = load_settings(paths.config)
        except VrecError:
            settings = Settings()  # the doctor reports the config problem itself
        return doctor.run_doctor(settings, paths, features)

    return app.run(paths.data_dir, paths.config, args.test, all_videos=args.all, only=args.only)


def _run_schedule(args: argparse.Namespace) -> int:
    from vrec import schedule
    from vrec.errors import VrecError

    action, *rest = args.schedule
    try:
        if action == "on" and len(rest) == 1:
            data_dir = args.data_dir or Path(os.environ.get("VREC_DATA_DIR", "data"))
            print(schedule.schedule_on(rest[0], args.days, data_dir, args.config))
        elif action == "off" and not rest:
            print(schedule.schedule_off())
        elif action == "status" and not rest:
            print(schedule.schedule_status())
        else:
            print("Usage: vrec --schedule on HH:MM [--days MON,TUE,...] | off | status")
            return 1
    except VrecError as e:
        print(str(e))
        return 1
    return 0


def _opened_by_double_click() -> bool:
    from vrec.console import opened_by_double_click

    return opened_by_double_click()


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    resolved_argv = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(argv)
    code = _run(args, resolved_argv)
    if args.pause_on_exit or _opened_by_double_click():
        with contextlib.suppress(EOFError):
            input("\nPress Enter to close.")
    return code


if __name__ == "__main__":
    sys.exit(main())
