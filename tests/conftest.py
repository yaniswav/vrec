"""Shared safety net: no test may touch real windows or virtual desktops."""

from __future__ import annotations

import pytest

from vrec import vdesktop


@pytest.fixture(autouse=True)
def _no_real_desktops(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.module.__name__.endswith("test_vdesktop"):
        return  # tests the real functions with fake seams
    monkeypatch.setattr(vdesktop, "find_chrome_hwnd", lambda page, allow_marker=False: None)
    monkeypatch.setattr(vdesktop, "pin", lambda hwnd: False)
    monkeypatch.setattr(vdesktop, "unpin", lambda hwnd: False)
    monkeypatch.setattr(vdesktop, "is_pinned", lambda hwnd: None)
    monkeypatch.setattr(vdesktop, "on_current_desktop", lambda hwnd: None)
