"""keep_awake: the Windows execution-state request, with a fake kernel32."""

from __future__ import annotations

import ctypes
import sys

import pytest

from vrec import power


def test_does_nothing_off_windows(monkeypatch):
    monkeypatch.setattr(power.sys, "platform", "linux")
    assert power.stay_awake() is False
    power.allow_sleep()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows API")
def test_asks_windows_to_stay_awake_then_releases(monkeypatch):
    states: list[int] = []

    class Kernel32:
        def SetThreadExecutionState(self, flags: int) -> int:  # noqa: N802 - Windows API name
            states.append(flags)
            return 0x80000000  # previous state: non-zero means accepted

    monkeypatch.setattr(ctypes.windll, "kernel32", Kernel32())
    assert power.stay_awake() is True
    power.allow_sleep()
    assert states == [0x80000003, 0x80000000]  # continuous + system + display, then continuous only


@pytest.mark.skipif(sys.platform != "win32", reason="Windows API")
def test_refused_request(monkeypatch):
    class Kernel32:
        def SetThreadExecutionState(self, flags: int) -> int:  # noqa: N802
            return 0

    monkeypatch.setattr(ctypes.windll, "kernel32", Kernel32())
    assert power.stay_awake() is False
