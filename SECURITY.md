# Security policy

## Supported versions

Only the latest released version of vrec is supported with security fixes. Please upgrade before
reporting an issue if you're not already on the latest release.

## Reporting a vulnerability

Please report security issues privately, using GitHub's "Report a vulnerability" button on the
[Security tab](../../security/advisories) of this repository (private security advisories). Do not
open a public issue for a security report.

## Scope

vrec runs entirely on your own machine and doesn't talk to any server of ours. In scope for a security
report is anything that could let another local process, a malicious page, or a network peer:

- read or exfiltrate your OBS WebSocket password or `data/` contents,
- take over or hijack your Chrome recording session or profile,
- escalate access beyond what vrec itself needs to do its job.

### Known design trade-off: the Chrome debugging port

vrec drives a dedicated Chrome profile over the DevTools remote-debugging protocol
(`[chrome] debug_port`, default `9222`). That port is bound to `localhost` only, but **any** local
process that can reach `localhost:9222` gets full control of that Chrome profile (navigation, script
execution, reading cookies/session data for whatever is logged into it). This is a known, accepted
trade-off of how Chrome's remote-debugging protocol works, not a vulnerability specific to vrec: it's
the same exposure any CDP-based tool has. Keep the recording profile dedicated to vrec (don't sign into
unrelated accounts in it), and don't expose that port beyond `localhost`.

### Known design trade-offs: the virtual-display helper and per-page audio routing

`vrec --install-display-helper` registers two Windows Task Scheduler tasks that run with the highest
privileges (needed to enable/disable a display adapter), so that vrec itself never needs to be
elevated at run time. Those tasks only run `Enable-PnpDevice`/`Disable-PnpDevice` against the display
adapter(s) matching the pattern given at install time, nothing else, and only when triggered by
`schtasks /Run` (no network trigger, no schedule of their own).

The script the tasks run lives in `%ProgramData%\vrec`. The install step (which already runs as
administrator) removes inherited permissions on that folder and gives Administrators and SYSTEM full
control and Users read-and-run only, so a standard process can't edit a script that later runs with
elevated rights. The install pattern is limited to letters, digits, spaces and `* ? . ( ) - [ ]`, so it
can't carry quotes or other characters that could break out of the script's string. Copies left in
`%LOCALAPPDATA%\vrec` by earlier betas, which a standard process could write to, are deleted when you
install or uninstall the helper. An administrator can still change the script, as with any elevated
task.

The `audio_sink` feature grants the recorded page a temporary microphone permission (reset to "ask"
again immediately after, not left granted) so it can read the list of audio output device names and find "CABLE Input" by
name; it is used only to enumerate device labels through `setSinkId`, never to capture or transmit
microphone audio.

### The OBS WebSocket password

vrec stores the OBS WebSocket password in plain text in `data\obs_password.txt`, protected only by the
file permissions of your data folder. If you'd rather not keep it on disk, set the `VREC_OBS_PASSWORD`
environment variable instead (it is read first, and no file is written). The password prompt hides what
you type and is not written to the run logs.

### Release downloads

Each release has a `SHA256SUMS.txt` next to the zip. Compare it with `Get-FileHash <zip>` in PowerShell
before you run `vrec.exe`; see "Without Python" in the [README](README.md#without-python).

### Run logs

When the `run_logs` feature is on (the default), `data/logs/` holds a copy of everything vrec printed
during recent runs, including video titles and URLs from your `videos.txt`; anything typed at a
"password" prompt is masked as `***` before being written. Treat `data/logs/` with the same care as
the rest of `data/` if you share a log file (for example when reporting a bug).
