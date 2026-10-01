"""Scheduled unattended runs, driven by Windows Task Scheduler (`schtasks`).

Creates a small launcher batch file under `%LOCALAPPDATA%\\vrec\\` and registers a
Task Scheduler task that runs it. All `schtasks` calls go through an injectable
`Runner` so tests never touch the real scheduler.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from subprocess import CompletedProcess

from vrec.errors import VrecError

TASK_NAME = r"\vrec\scheduled-run"

Runner = Callable[[Sequence[str]], "CompletedProcess[str]"]

_DAY_NAMES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

# schtasks reports a missing task with a localized "file not found"-style message.
# We only recognize these to decide "absent" vs. a real failure; anything else is
# treated as an error and surfaced to the user.
_ABSENT_MARKERS = (
    "cannot find the file specified",  # English
    "introuvable",  # French: "Le fichier spécifié est introuvable."
    "does not exist",  # English, some builds: "...does not exist in the system."
)

_NEXT_RUN_LABELS = {"next run time", "prochaine exécution"}
_LAST_RESULT_LABELS = {"last result", "dernier résultat"}


def _oem_encoding() -> str:
    """Name of the console (OEM) code page, which is what schtasks writes its output in."""
    try:
        import ctypes

        code_page = int(ctypes.windll.kernel32.GetOEMCP())
        encoding = f"cp{code_page}"
        "".encode(encoding)  # make sure Python knows this code page
        return encoding
    except Exception:
        return "mbcs" if sys.platform.startswith("win") else "utf-8"


def _decode_console(data: bytes | str | None) -> str:
    """Decode console output with the OEM code page so accented labels survive."""
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode(_oem_encoding(), errors="replace")


def _default_runner(args: Sequence[str]) -> CompletedProcess[str]:
    raw = subprocess.run(args, capture_output=True, check=False)
    return CompletedProcess(
        raw.args, raw.returncode, _decode_console(raw.stdout), _decode_console(raw.stderr)
    )


def _require_windows() -> None:
    if not sys.platform.startswith("win"):
        raise VrecError("Scheduling unattended runs is only available on Windows.")


def _validate_time(value: str) -> str:
    match = _TIME_RE.match(value.strip())
    if not match:
        raise VrecError(f"Invalid time '{value}': use 24h HH:MM, e.g. 07:30.")
    return f"{match.group(1)}:{match.group(2)}"


def _validate_days(value: str) -> str:
    days = [d.strip().upper() for d in value.split(",") if d.strip()]
    if not days:
        raise VrecError("Invalid --days: use day codes like MON,TUE,WED,THU,FRI,SAT,SUN.")
    bad = [d for d in days if d not in _DAY_NAMES]
    if bad:
        raise VrecError(f"Invalid day(s): {', '.join(bad)}. Use MON,TUE,WED,THU,FRI,SAT,SUN.")
    seen: list[str] = []
    for day in days:
        if day not in seen:
            seen.append(day)
    return ",".join(seen)


def _launcher_path() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if not local_appdata:
        raise VrecError("LOCALAPPDATA is not set; can't create the scheduled-run launcher.")
    return Path(local_appdata) / "vrec" / "scheduled-run.cmd"


def _launcher_command(data_dir: Path, config: Path | None) -> str:
    # In the packaged vrec.exe, sys.executable is vrec.exe itself: there is no "-m vrec".
    module = [] if getattr(sys, "frozen", False) else ["-m", "vrec"]
    parts = [f'"{sys.executable}"', *module, "--all", "--data-dir", f'"{data_dir}"']
    if config is not None:
        parts += ["--config", f'"{config}"']
    return " ".join(parts)


def _batch_escape(text: str) -> str:
    """Escape text for a .cmd file: a literal % must be doubled or cmd expands it."""
    return text.replace("%", "%%")


def _launcher_script(cwd: Path, command: str) -> str:
    # chcp 65001 makes cmd read the UTF-8 file correctly (accented paths); CRLF is what cmd expects.
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        f'cd /d "{_batch_escape(str(cwd))}"',
        _batch_escape(command),
        "exit /b %ERRORLEVEL%",
    ]
    return "\r\n".join(lines) + "\r\n"


def _schtasks_create_args(time: str, days: str | None, launcher: Path) -> list[str]:
    # The value must carry its own quotes: schtasks stores /TR verbatim as the task's
    # command line, so a path with spaces needs literal quotes to survive as one token.
    tr_value = f'"{launcher}"'
    args = ["schtasks", "/Create", "/F"]
    if days:
        args += ["/SC", "WEEKLY", "/D", days]
    else:
        args += ["/SC", "DAILY"]
    args += ["/ST", time, "/TN", TASK_NAME, "/TR", tr_value]
    return args


def schedule_on(
    time: str,
    days: str | None,
    data_dir: Path,
    config: Path | None,
    runner: Runner = _default_runner,
) -> str:
    """Create (or replace) the daily/weekly scheduled task. Returns a summary to print."""
    _require_windows()
    time = _validate_time(time)
    days_arg = _validate_days(days) if days else None

    launcher = _launcher_path()
    launcher.parent.mkdir(parents=True, exist_ok=True)
    command = _launcher_command(data_dir.resolve(), config.resolve() if config is not None else None)
    launcher.write_text(_launcher_script(Path.cwd(), command), encoding="utf-8", newline="")

    result = runner(_schtasks_create_args(time, days_arg, launcher))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise VrecError(f"Couldn't create the scheduled task: {detail or 'schtasks failed.'}")

    when = f"every day at {time}" if not days_arg else f"{days_arg} at {time}"
    return (
        f"Scheduled: vrec will run unattended {when}.\n"
        f"It will run: {command}\n"
        "Reminder: OBS and the recording Chrome (launch_chrome.bat) must be open at that "
        "time; the PC must be awake."
    )


def schedule_off(runner: Runner = _default_runner) -> str:
    """Delete the scheduled task and its launcher. Not an error if there is none."""
    _require_windows()
    result = runner(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    launcher = _launcher_path()
    if launcher.exists():
        with contextlib.suppress(OSError):
            launcher.unlink()

    if result.returncode != 0:
        combined = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
        if any(marker in combined for marker in _ABSENT_MARKERS):
            return "No scheduled run."
        detail = (result.stderr or result.stdout or "").strip()
        raise VrecError(f"Couldn't remove the scheduled task: {detail or 'schtasks failed.'}")
    return "Scheduled run removed."


def schedule_status(runner: Runner = _default_runner) -> str:
    """Report whether the scheduled task exists, its next run time and last result."""
    _require_windows()
    result = runner(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"])
    if result.returncode != 0:
        combined = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
        if any(marker in combined for marker in _ABSENT_MARKERS):
            return "No scheduled run."
        detail = (result.stderr or result.stdout or "").strip()
        raise VrecError(f"Couldn't read the scheduled task: {detail or 'schtasks failed.'}")
    return _format_status(result.stdout or "")


def _format_status(output: str) -> str:
    next_run: str | None = None
    last_result: str | None = None
    for line in output.splitlines():
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        key = label.strip().lower()
        value = value.strip()
        if key in _NEXT_RUN_LABELS:
            next_run = value
        elif key in _LAST_RESULT_LABELS:
            last_result = value

    if next_run is None and last_result is None:
        # Unrecognized (or unexpected) output: don't guess, just show what schtasks said.
        relevant = [ln for ln in output.splitlines() if ln.strip()]
        return "Scheduled run: task exists (couldn't parse the details below).\n" + "\n".join(relevant)

    last_result_text = last_result or "unknown"
    if last_result == "0":
        last_result_text += " (success)"
    elif last_result not in (None, ""):
        last_result_text += " (error)"

    return f"Scheduled run: task exists.\nNext run: {next_run or 'unknown'}\nLast result: {last_result_text}"
