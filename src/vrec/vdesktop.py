"""Windows virtual desktops: show the recording Chrome on all of them, and know where it is.

Virtual desktops span every monitor, so switching desktops while recording makes the virtual
screen show the other desktop and the (cloaked) Chrome window disappears from OBS's capture.
Pinning the window ("Show this window on all desktops") keeps it visible whatever desktop the
user works on. Pinning goes through `pyvda` (the undocumented shell interfaces); asking whether
a window is on the current desktop uses the documented IVirtualDesktopManager COM interface.

Every public function swallows its errors: a missing library or a Windows update that changes
the shell interfaces must never stop a batch.
"""

from __future__ import annotations

import ctypes
import uuid
from dataclasses import dataclass
from typing import Any

from vrec.browser import WindowBounds, get_window_bounds

CHROME_CLASS = "Chrome_WidgetWin_1"
_TITLE_SUFFIX = " - Google Chrome"

# CLSID_VirtualDesktopManager and IID_IVirtualDesktopManager (documented in shobjidl_core.h).
_CLSID_VDM = "{aa509086-5ca9-4c25-8f95-589d3c07b48a}"
_IID_VDM = "{a5cd92ff-29be-454c-8d04-d82879fb3f1b}"


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    rect: tuple[int, int, int, int]  # left, top, right, bottom
    exe: str  # lower-case file name of the owning process


# ---------- Finding the window ----------


def _enum_windows() -> list[WindowInfo]:
    """Every visible top-level Chrome_WidgetWin_1 window with a title (Win32 EnumWindows)."""
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    def exe_of(hwnd: int) -> str:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            buffer = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buffer))
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return ""
            return buffer.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
        finally:
            kernel32.CloseHandle(handle)

    found: list[WindowInfo] = []

    def visit(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, name, len(name))
        if name.value != CHROME_CLASS:
            return True
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, len(title))
        rect = wintypes.RECT()
        if title.value and user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            found.append(
                WindowInfo(
                    int(hwnd), title.value, (rect.left, rect.top, rect.right, rect.bottom), exe_of(hwnd)
                )
            )
        return True

    user32.EnumWindows(callback_type(visit), 0)
    return found


def pick_window(windows: list[WindowInfo], title: str, bounds: WindowBounds | None) -> int | None:
    """The HWND of the Chrome window titled "<title> - Google Chrome", or None if unknown or ambiguous.

    Several windows may carry the same title (your everyday Chrome on the same site): the one whose
    rectangle is closest to the bounds CDP reports for the recording window wins.
    """
    if not title:
        return None
    wanted = title + _TITLE_SUFFIX
    matches = [
        w
        for w in windows
        if w.exe == "chrome.exe" and (w.title == wanted or w.title.startswith(wanted + " - "))
    ]
    if len(matches) == 1:
        return matches[0].hwnd
    if not matches or bounds is None:
        return None

    def distance(w: WindowInfo) -> int:
        left, top, right, bottom = w.rect
        return (
            abs(left - bounds.left)
            + abs(top - bounds.top)
            + abs((right - left) - bounds.width)
            + abs((bottom - top) - bounds.height)
        )

    ranked = sorted(matches, key=distance)
    return ranked[0].hwnd if distance(ranked[0]) < distance(ranked[1]) else None


def _find_with_marker(page: Any, windows: Any) -> int | None:
    """Give the tab a unique title for a moment, so its window can't be confused with another one."""
    original = page.title()
    marker = f"vrec-{uuid.uuid4().hex[:12]}"
    try:
        page.evaluate("t => { document.title = t; }", marker)
        for _ in range(10):
            page.wait_for_timeout(300)
            hwnd = pick_window(windows(), marker, None)
            if hwnd is not None:
                return hwnd
        return None
    finally:
        page.evaluate("t => { document.title = t; }", original)


def find_chrome_hwnd(page: Any, allow_marker: bool = False) -> int | None:
    """The HWND of the Chrome window showing `page`, or None if it can't be told for sure.

    Chrome exposes no window handle through CDP, so the window is recognised by its title
    ("<page title> - Google Chrome", from a chrome.exe window) and, when several windows share it,
    by the rectangle CDP reports. With `allow_marker`, a still ambiguous (or untitled) page gets a
    temporary unique title that identifies its window without any doubt, then gets its title back;
    that's only used at start-up, not while a video is being recorded (OBS's window capture follows
    the title).
    """
    try:
        browser = page.context.browser
        bounds = get_window_bounds(browser, page) if browser is not None else None
        windows = _enum_windows()
        hwnd = pick_window(windows, page.title(), bounds)
        if hwnd is None and allow_marker:
            hwnd = _find_with_marker(page, _enum_windows)
        return hwnd
    except Exception:
        return None


# ---------- Pinning (pyvda) ----------


def pin(hwnd: int) -> bool:
    """Show the window on all virtual desktops. False if it couldn't be done."""
    try:
        from pyvda import AppView

        AppView(hwnd).pin()
        return True
    except Exception:
        return False


def unpin(hwnd: int) -> bool:
    """Stop showing the window on all desktops. False if it couldn't be done."""
    try:
        from pyvda import AppView

        AppView(hwnd).unpin()
        return True
    except Exception:
        return False


def is_pinned(hwnd: int) -> bool | None:
    """Whether the window is shown on all desktops; None if that can't be told."""
    try:
        from pyvda import AppView

        return bool(AppView(hwnd).is_pinned())
    except Exception:
        return None


# ---------- Is the window on the current desktop? (documented COM API) ----------


class _Guid(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> _Guid:
    raw = uuid.UUID(text.strip("{}"))
    guid = _Guid()
    guid.Data1, guid.Data2, guid.Data3 = raw.time_low, raw.time_mid, raw.time_hi_version
    for i, byte in enumerate(raw.bytes[8:]):
        guid.Data4[i] = byte
    return guid


def on_current_desktop(hwnd: int) -> bool | None:
    """IVirtualDesktopManager::IsWindowOnCurrentVirtualDesktop. None if it can't be asked."""
    ole32 = ctypes.OleDLL("ole32")
    initialized = False
    manager = ctypes.c_void_p()
    try:
        hr = ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
        # S_OK / S_FALSE need a matching CoUninitialize; RPC_E_CHANGED_MODE means COM is already
        # set up differently on this thread, which is fine to use as is.
        initialized = hr in (0, 1)
        clsid, iid = _guid(_CLSID_VDM), _guid(_IID_VDM)
        ole32.CoCreateInstance(
            ctypes.byref(clsid),
            None,
            1,
            ctypes.byref(iid),
            ctypes.byref(manager),  # CLSCTX_INPROC_SERVER
        )
        if not manager.value:
            return None
        vtable = ctypes.cast(manager, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
        is_on_current = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)
        )(vtable[3])
        try:
            answer = ctypes.c_int()
            if is_on_current(manager, hwnd, ctypes.byref(answer)) != 0:
                return None
            return bool(answer.value)
        finally:
            release(manager)
    except Exception:
        return None
    finally:
        if initialized:
            ole32.CoUninitialize()
