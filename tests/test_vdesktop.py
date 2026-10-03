"""vrec.vdesktop: window lookup, pinning through pyvda, and the current-desktop question."""

from __future__ import annotations

import ctypes
import sys
import types
from typing import Any

import pytest

from vrec import vdesktop
from vrec.browser import WindowBounds
from vrec.vdesktop import WindowInfo, pick_window

BOUNDS = WindowBounds(0, 0, 3840, 2160, "fullscreen")


def win(
    hwnd: int, title: str, rect: tuple[int, int, int, int] = (0, 0, 3840, 2160), exe: str = "chrome.exe"
) -> WindowInfo:
    return WindowInfo(hwnd, title, rect, exe)


# ---------- pick_window ----------


def test_pick_window_by_title() -> None:
    windows = [win(1, "Other - Google Chrome"), win(2, "Video - Google Chrome")]
    assert pick_window(windows, "Video", BOUNDS) == 2


def test_pick_window_accepts_a_profile_suffix() -> None:
    assert pick_window([win(1, "Video - Google Chrome - Person 1")], "Video", None) == 1


def test_pick_window_ignores_other_programs() -> None:
    assert pick_window([win(1, "Video - Google Chrome", exe="msedge.exe")], "Video", BOUNDS) is None


def test_pick_window_none_when_missing_or_untitled() -> None:
    assert pick_window([win(1, "Other - Google Chrome")], "Video", BOUNDS) is None
    assert pick_window([win(1, " - Google Chrome")], "", BOUNDS) is None


def test_pick_window_same_title_uses_the_bounds() -> None:
    windows = [
        win(1, "Video - Google Chrome", (100, 100, 1000, 800)),
        win(2, "Video - Google Chrome", (0, 0, 3840, 2160)),
    ]
    assert pick_window(windows, "Video", BOUNDS) == 2


def test_pick_window_ambiguous_without_bounds_or_on_a_tie() -> None:
    windows = [win(1, "Video - Google Chrome"), win(2, "Video - Google Chrome")]
    assert pick_window(windows, "Video", None) is None
    assert pick_window(windows, "Video", BOUNDS) is None


# ---------- find_chrome_hwnd ----------


class FakePage:
    def __init__(self, title: str = "Video") -> None:
        self._title = title
        self.context = types.SimpleNamespace(browser=object())
        self.titles_set: list[str] = []

    def title(self) -> str:
        return self._title

    def evaluate(self, script: str, value: str) -> None:
        self.titles_set.append(value)
        self._title = value

    def wait_for_timeout(self, ms: int) -> None:
        pass


@pytest.fixture
def bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vdesktop, "get_window_bounds", lambda browser, page: BOUNDS)


def test_find_by_title(monkeypatch: pytest.MonkeyPatch, bounds: None) -> None:
    monkeypatch.setattr(vdesktop, "_enum_windows", lambda: [win(7, "Video - Google Chrome")])
    assert vdesktop.find_chrome_hwnd(FakePage()) == 7


def test_find_without_marker_gives_up_when_ambiguous(monkeypatch: pytest.MonkeyPatch, bounds: None) -> None:
    windows = [win(1, "Video - Google Chrome"), win(2, "Video - Google Chrome")]
    monkeypatch.setattr(vdesktop, "_enum_windows", lambda: windows)
    page = FakePage()
    assert vdesktop.find_chrome_hwnd(page) is None
    assert page.titles_set == []  # the title is never touched without allow_marker


def test_find_with_marker_when_ambiguous_then_restores_the_title(
    monkeypatch: pytest.MonkeyPatch, bounds: None
) -> None:
    page = FakePage()

    def enum() -> list[WindowInfo]:
        windows = [win(1, "Video - Google Chrome"), win(2, "Video - Google Chrome")]
        if page.titles_set:  # the marker shows up in exactly one window's title
            windows[1] = win(2, f"{page.title()} - Google Chrome")
        return windows

    monkeypatch.setattr(vdesktop, "_enum_windows", enum)
    assert vdesktop.find_chrome_hwnd(page, allow_marker=True) == 2
    assert page.titles_set[0].startswith("vrec-") and page.titles_set[-1] == "Video"
    assert page.title() == "Video"


def test_find_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> list[WindowInfo]:
        raise OSError("no windows")

    monkeypatch.setattr(vdesktop, "_enum_windows", boom)
    assert vdesktop.find_chrome_hwnd(FakePage()) is None


# ---------- pin / unpin / is_pinned ----------


class FakeAppView:
    pinned: dict[int, bool] = {}
    fail = False

    def __init__(self, hwnd: int) -> None:
        if FakeAppView.fail:
            raise RuntimeError("shell interface changed")
        self.hwnd = hwnd

    def pin(self) -> None:
        FakeAppView.pinned[self.hwnd] = True

    def unpin(self) -> None:
        FakeAppView.pinned[self.hwnd] = False

    def is_pinned(self) -> bool:
        return FakeAppView.pinned.get(self.hwnd, False)


@pytest.fixture
def fake_pyvda(monkeypatch: pytest.MonkeyPatch) -> type[FakeAppView]:
    FakeAppView.pinned, FakeAppView.fail = {}, False
    module = types.ModuleType("pyvda")
    module.AppView = FakeAppView  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyvda", module)
    return FakeAppView


def test_pin_unpin_round_trip(fake_pyvda: type[FakeAppView]) -> None:
    assert vdesktop.is_pinned(5) is False
    assert vdesktop.pin(5) is True
    assert vdesktop.is_pinned(5) is True
    assert vdesktop.unpin(5) is True
    assert vdesktop.is_pinned(5) is False


def test_pyvda_errors_are_swallowed(fake_pyvda: type[FakeAppView]) -> None:
    fake_pyvda.fail = True
    assert vdesktop.pin(5) is False
    assert vdesktop.unpin(5) is False
    assert vdesktop.is_pinned(5) is None


def test_pyvda_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pyvda", None)  # makes `import pyvda` raise ImportError
    assert vdesktop.pin(5) is False
    assert vdesktop.unpin(5) is False
    assert vdesktop.is_pinned(5) is None


# ---------- on_current_desktop ----------


def test_on_current_desktop_none_on_com_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenOle:
        def __init__(self, *args: Any) -> None:
            pass

        def __getattr__(self, name: str) -> Any:
            def fail(*args: Any) -> int:
                raise OSError("COM unavailable")

            return fail

    monkeypatch.setattr(ctypes, "OleDLL", BrokenOle)
    assert vdesktop.on_current_desktop(123) is None


def test_on_current_desktop_none_without_an_interface(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class QuietOle:
        def __init__(self, *args: Any) -> None:
            pass

        def CoInitializeEx(self, *args: Any) -> int:  # noqa: N802
            calls.append("init")
            return 0

        def CoCreateInstance(self, *args: Any) -> int:  # noqa: N802
            return 0  # "succeeds" but leaves the pointer empty

        def CoUninitialize(self) -> None:  # noqa: N802
            calls.append("uninit")

    monkeypatch.setattr(ctypes, "OleDLL", QuietOle)
    assert vdesktop.on_current_desktop(123) is None
    assert calls == ["init", "uninit"]


def test_guid_layout() -> None:
    guid = vdesktop._guid(vdesktop._CLSID_VDM)
    assert guid.Data1 == 0xAA509086 and guid.Data2 == 0x5CA9 and guid.Data3 == 0x4C25
    assert bytes(guid.Data4) == bytes.fromhex("8f95589d3c07b48a")
