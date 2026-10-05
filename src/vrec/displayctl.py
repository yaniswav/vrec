"""`vrec --display on|off|status`: drive the virtual display by hand."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from vrec import display
from vrec.display import Runner
from vrec.errors import VrecError
from vrec.lock import InstanceLock

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
