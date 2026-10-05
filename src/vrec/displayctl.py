"""`vrec --display on|off|status|auto`: drive the virtual display by hand or by watching programs."""

from __future__ import annotations

import csv
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from vrec import display
from vrec.display import Runner
from vrec.errors import VrecError
from vrec.lock import InstanceLock

WATCH_INTERVAL_S = 5.0
RECORDING_MESSAGE = "vrec is recording: the virtual display stays on until it ends."
NOT_INSTALLED = (
    "The virtual display helper isn't installed.",
    "Run once, as administrator: vrec --install-display-helper",
    'If your adapter has another name: vrec --install-display-helper "*part of its name*"',
)


def batch_running(lock_path: Path) -> bool:
    """Whether a vrec batch holds the instance lock (taken and released at once if not)."""
    try:
        with InstanceLock(lock_path):
            return False
    except VrecError:
        return True


def running_processes(run: Runner = display._run) -> list[str] | None:
    """Image names of the running processes (`tasklist`), None when the list can't be read."""
    result = run(["tasklist", "/FO", "CSV", "/NH"])
    if result.returncode != 0:
        return None
    return [row[0] for row in csv.reader(result.stdout.splitlines()) if row]


def listed_running(names: Sequence[str], processes: Sequence[str]) -> str | None:
    """The first running process that is on the list (case-insensitive), as Windows names it."""
    wanted = {n.lower() for n in names}
    return next((p for p in processes if p.lower() in wanted), None)


def _require_helper() -> None:
    if not display.helper_installed():
        raise VrecError("\n".join(NOT_INSTALLED))


def show_status(
    run: Runner = display._run, lister: Callable[[], list[display.Screen]] = display.list_screens
) -> int:
    if display.helper_installed():
        adapters = display.switched_devices(run)
        print(f"Helper: installed, it switches: {', '.join(adapters) if adapters else '(no adapter found)'}")
        state = display.virtual_display_enabled(run)
        print(f"Virtual display: {'unknown' if state is None else 'on' if state else 'off'}")
    else:
        print("Helper: not installed")
        print("Virtual display: unknown")
        print("To switch it from vrec, run once, as administrator: vrec --install-display-helper")
    screens = lister()
    print("Screens Windows has now:" if screens else "No screen found.")
    for s in screens:
        print(f"  {s.describe()} at {s.left},{s.top}")
    return 0


def switch(
    on: bool,
    lock_path: Path,
    run: Runner = display._run,
    lister: Callable[[], list[display.Screen]] = display.list_screens,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    _require_helper()
    if not on and batch_running(lock_path):
        print("A vrec batch is recording: not turning the virtual display off.")
        return 1
    if display.virtual_display_enabled(run) is on:
        print(f"The virtual display is already {'on' if on else 'off'}.")
        return 0
    before = lister()
    display.set_virtual_display(on, run)
    if not on:
        print("Virtual display turned off.")
        return 0
    screen = display.wait_for_new_screen(before, lister=lister, sleep=sleep)
    if screen:
        print(f"Virtual display turned on: {screen.describe()}.")
    else:
        print(
            "Virtual display turned on, but no new screen showed up within 15 s. Check: vrec --display status"
        )
    return 0


def watch(
    names: Sequence[str],
    lock_path: Path,
    run: Runner = display._run,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = datetime.now,
    interval: float = WATCH_INTERVAL_S,
) -> int:
    """Keep the virtual display off while a listed program runs. Stops on Ctrl+C."""
    if not names:
        print("Nothing to watch: [display] off_while_running is empty in config.toml.")
        print('Set it to the programs to wait for, e.g.: off_while_running = ["VALORANT-Win64-Shipping.exe"]')
        return 1
    _require_helper()
    print(f"Watching: {', '.join(names)}. Ctrl+C to stop.")

    def say(text: str) -> None:
        print(f"[{now():%H:%M}] {text}")

    is_on = display.virtual_display_enabled(run) is not False
    told_recording = False
    found: str | None = None
    try:
        while True:
            processes = running_processes(run)
            if processes is not None:
                found = listed_running(names, processes)
            if batch_running(lock_path):
                if not told_recording:
                    print(RECORDING_MESSAGE)
                    told_recording = True
            else:
                told_recording = False
                if found and is_on:
                    display.set_virtual_display(False, run)
                    is_on = False
                    say(f"{found} started: virtual display off.")
                elif not found and not is_on:
                    display.set_virtual_display(True, run)
                    is_on = True
                    say("No listed program running: virtual display on.")
            sleep(interval)
    except KeyboardInterrupt:
        # Leave the display as it should be: on, unless a listed program still runs.
        processes = running_processes(run)
        if processes is not None:
            found = listed_running(names, processes)
        if not is_on and not found and not batch_running(lock_path):
            display.set_virtual_display(True, run)
            say("No listed program running: virtual display on.")
        print("Stopped.")
        return 0
