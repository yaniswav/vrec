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
    parser.add_argument("--version", action="version", version=f"vrec {__version__}")
    _add_schedule_arguments(parser)
    return parser


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
    config_path = args.config or (data_dir / "config.toml")

    try:
        from vrec import app
    except ImportError:
        print("Missing dependencies. Run scripts\\windows\\install.bat (or: pip install -e .).")
        return 1

    from vrec.errors import VrecError

    try:
        return app.run(data_dir, config_path, args.test)
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
