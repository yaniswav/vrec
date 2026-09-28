"""Keep Windows awake while recording (feature keep_awake).

A long batch or a scheduled night run would otherwise be cut by the sleep timer or by the screens
turning off. This only asks Windows to stay awake while vrec is recording; the power settings
themselves are never changed, and everything is back to normal as soon as vrec stops.
The request belongs to the calling thread: call both functions from the same one.
"""

from __future__ import annotations

import sys

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


def stay_awake() -> bool:
    """Prevent system sleep and display power-off until `allow_sleep()`. Returns whether it worked."""
    if sys.platform != "win32":
        return False
    import ctypes

    flags = _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED
    return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))


def allow_sleep() -> None:
    """Give Windows its normal sleep and screen-off behavior back."""
    if sys.platform != "win32":
        return
    import ctypes

    ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
