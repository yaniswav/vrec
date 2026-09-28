"""A Windows notification when a batch is over (feature notify_when_done).

Uses the notification system built into Windows through Windows PowerShell 5.1, so nothing needs
to be installed. The texts go through environment variables, never through the command line, so
titles with quotes or symbols can't break the script. A failure is silent: it's only a courtesy.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence

# Windows PowerShell's own app id: notifications need a registered sender.
_SENDER = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

_SCRIPT = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
    "ContentType = WindowsRuntime] > $null;"
    "$m = [Windows.UI.Notifications.ToastNotificationManager];"
    "$t = $m::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
    "$x = $t.GetElementsByTagName('text');"
    "$x.Item(0).AppendChild($t.CreateTextNode($env:VREC_NOTIFY_TITLE)) > $null;"
    "$x.Item(1).AppendChild($t.CreateTextNode($env:VREC_NOTIFY_TEXT)) > $null;"
    f"$m::CreateToastNotifier('{_SENDER}').Show([Windows.UI.Notifications.ToastNotification]::new($t))"
)

Runner = Callable[[Sequence[str], Mapping[str, str]], int]


def _run(args: Sequence[str], env: Mapping[str, str]) -> int:
    try:
        return subprocess.run(
            list(args), env=dict(env), capture_output=True, timeout=20, check=False
        ).returncode
    except (OSError, subprocess.TimeoutExpired):
        return 1


def notify(title: str, text: str, run: Runner = _run) -> bool:
    """Show a Windows notification. Returns whether PowerShell reported success."""
    if sys.platform != "win32":
        return False
    env = {**os.environ, "VREC_NOTIFY_TITLE": title, "VREC_NOTIFY_TEXT": text}
    return run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT], env) == 0
