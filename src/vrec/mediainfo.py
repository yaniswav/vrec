"""Length of a video file, read from Windows itself (no ffmpeg needed).

Explorer shows the "Length" column of a video from its property store; this asks the same question.
Every failure (no handler for the format, a file still being written, a missing file) gives None.
"""

from __future__ import annotations

import ctypes
import math
import uuid
from ctypes import wintypes
from pathlib import Path

# A file shorter than this share of the video, and by more than this many seconds, is cut short.
# The recording also holds a lead-in and a tail, so a longer file is normal.
SHORT_RATIO = 0.95
SHORT_BY_S = 30.0

_TICKS_PER_SECOND = 10_000_000  # the property is in units of 100 ns
_VT_UI8 = 21


class _Guid(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_ulong),
        ("data2", ctypes.c_ushort),
        ("data3", ctypes.c_ushort),
        ("data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, text: str) -> _Guid:

        value = uuid.UUID(text)
        return cls(
            value.time_low, value.time_mid, value.time_hi_version, (ctypes.c_ubyte * 8)(*value.bytes[8:])
        )


class _PropertyKey(ctypes.Structure):
    _fields_ = [("fmtid", _Guid), ("pid", wintypes.DWORD)]


class _PropVariant(ctypes.Structure):
    """A PROPVARIANT (24 bytes on 64-bit Windows), only read as an unsigned 64-bit integer."""

    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("reserved", ctypes.c_ushort * 3),
        ("value", ctypes.c_ulonglong),
        ("padding", ctypes.c_ulonglong),
    ]


def _read_duration_ticks(path: str) -> int | None:  # pragma: no cover - real Windows shell API
    """PKEY_Media_Duration of a file in 100 ns units, or None."""
    ole32 = ctypes.OleDLL("ole32")
    shell32 = ctypes.WinDLL("shell32")
    started = ole32.CoInitialize(None) in (0, 1)  # S_OK or S_FALSE: balanced by CoUninitialize below
    try:
        store = ctypes.c_void_p()
        iid = _Guid.parse("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")  # IPropertyStore
        shell32.SHGetPropertyStoreFromParsingName.argtypes = [
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(_Guid),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        shell32.SHGetPropertyStoreFromParsingName.restype = ctypes.c_long
        if (
            shell32.SHGetPropertyStoreFromParsingName(path, None, 0, ctypes.byref(iid), ctypes.byref(store))
            < 0
        ):
            return None
        if not store.value:
            return None
        # IPropertyStore vtable: QueryInterface, AddRef, Release, GetCount, GetAt, GetValue, ...
        vtable = ctypes.cast(
            ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p)
        )
        get_value = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(_PropertyKey), ctypes.POINTER(_PropVariant)
        )(vtable[5])
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
        try:
            key = _PropertyKey(_Guid.parse("64440490-4C8B-11D1-8B70-080036B11A03"), 3)  # PKEY_Media_Duration
            value = _PropVariant()
            if get_value(store, ctypes.byref(key), ctypes.byref(value)) < 0:
                return None
            ticks = int(value.value) if value.vt == _VT_UI8 else None
            ole32.PropVariantClear(ctypes.byref(value))
            return ticks
        finally:
            release(store)
    finally:
        if started:
            ole32.CoUninitialize()


def file_duration_s(path: str | Path) -> float | None:
    """Length in seconds of a video file, or None when Windows can't tell."""
    try:
        ticks = _read_duration_ticks(str(path))
    except Exception:
        return None
    if not ticks:
        return None
    seconds = ticks / _TICKS_PER_SECOND
    return seconds if math.isfinite(seconds) and seconds > 0 else None


def is_cut_short(file_s: float | None, video_s: float) -> bool:
    """Whether a recording is clearly shorter than its video. Unknown lengths are never a problem."""
    if not file_s or video_s <= 0:
        return False
    return file_s < video_s * SHORT_RATIO and video_s - file_s > SHORT_BY_S
