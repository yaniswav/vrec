"""Command-line entry point: `python -m vrec` / the `vrec` console script."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path


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
    _add_schedule_arguments(parser)
    _add_selection_arguments(parser)
    return parser


def _add_selection_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--all", action="store_true", help="record every video still to do (NEW/FAILED), no menu"
    )
    group.add_argument(
        "--only", metavar="LIST", default=None, help="record exactly these numbers, e.g. 3,1,5-8, no menu"
    )


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


def _run(args: argparse.Namespace) -> int:
    if args.schedule is not None:
        return _run_schedule(args)

    data_dir = args.data_dir or Path(os.environ.get("VREC_DATA_DIR", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    if args.features or args.enable or args.disable:
        return _run_features(args, data_dir)

    config_path = args.config or (data_dir / "config.toml")

    try:
        from vrec import app
    except ImportError:
        print("Missing dependencies. Run scripts\\windows\\install.bat (or: pip install -e .).")
        return 1

    from vrec.errors import VrecError

    try:
        return app.run(data_dir, config_path, args.test, all_videos=args.all, only=args.only)
    except VrecError as e:
        print(str(e))
        return 1
    except KeyboardInterrupt:
        print("Stopped.")
        return 130


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


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    code = _run(args)
    if args.pause_on_exit:
        with contextlib.suppress(EOFError):
            input("\nPress Enter to close.")
    return code


if __name__ == "__main__":
    sys.exit(main())
