# vrec

[![CI](https://github.com/yaniswav/vrec/actions/workflows/ci.yml/badge.svg)](https://github.com/yaniswav/vrec/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Windows](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-lightgrey.svg)

Unattended OBS recording of web videos, one after another.

## What it does

- Reads a list of video links and, for each one:
  - opens the page in Chrome, placed on an invisible virtual display,
  - puts the video fullscreen and rewinds it to the start,
  - forces the best available quality for that video (1440p, 2160p...) instead of "auto",
  - pauses the recording while the video buffers, so the file never contains a frozen frame,
  - starts the OBS recording, then plays the video,
  - detects the end of the video and stops the recording,
  - renames the file after the video's title,
  - logs the result in a history file.
- Shows a menu at startup with what is already done, so you can pick what to record next (all of it,
  or just some entries, in whatever order you want).
- Lets you keep using your PC normally on your main screen while it works: the recording happens on a
  second, invisible display, and you don't hear the videos.
- Can also run with no menu at all (`--all`/`--only`, or on a schedule) and checks your setup for
  common problems before you start (`vrec --doctor`).

## What a check looks like

`vrec --doctor` checks your setup without recording anything. With OBS and the recording Chrome
closed, it tells you what vrec will do about them:

```text
===== VREC DOCTOR =====

[ OK ] Playlist file: 30 link(s) in videos.txt.
[ OK ] Config file: Not found: using built-in defaults.
[INFO] Feature toggles: Disabled: manage_virtual_display.
[INFO] OBS connection: Not open: vrec will start it (C:\Program Files\obs-studio\bin\64bit\obs64.exe).
[INFO] Video sound: Sent to "CABLE Input" by vrec for the recorded page only (checked at each video).
[INFO] Chrome debugging port: Not open: vrec will start it (C:\Program Files\Google\Chrome\Application\chrome.exe, ...).
[ OK ] Virtual screen: DISPLAY12 (3840x2160, screen).

3 OK, 0 warning(s), 0 failure(s), 12 info.
```

Before the first video of a batch, the pre-flight check proves the whole chain works:

```text
Checking everything before recording...
  [ OK ] Virtual screen: DISPLAY12 (3840x2160, screen)
  [ OK ] Chrome window: on DISPLAY12, fullscreen
  [ OK ] OBS sees Chrome: 'vrec screen' shows the Chrome window
  [ OK ] Sound reaches OBS: tone played on CABLE Input (VB-Audio Virtual Cable)
```

## How it works

- **Chrome** is driven over the DevTools protocol (CDP) on a dedicated profile. By default vrec starts
  the recording Chrome itself if its debug port doesn't answer (feature `auto_start_chrome`, using
  `[chrome] path`/`profile` if you set them, or the standard install location and profile otherwise),
  places it on the virtual display, maximized, before the menu shows up (feature `auto_place_window`),
  then fullscreens it right before recording starts and puts it back afterwards; moving it by hand
  (**Win + Shift + Right Arrow**) is a fallback for when that feature is off or doesn't work for you.
  `scripts\windows\launch_chrome.bat` (`vrec --launch-chrome`) opens that same recording Chrome and
  places it the same way, whatever `auto_start_chrome` says, then exits, so you can log in to a site
  before running `start.bat`/`test.bat`.
- **OBS** is controlled through its WebSocket API: starting/stopping recordings, checking the current
  scene, and reading the audio meter. By default vrec also starts OBS itself if it isn't already open
  (feature `auto_start_obs`, using `[obs] path` if you set it, or the registry/default install location
  otherwise) and leaves it open afterwards.
- **Audio** is sent through **VB-CABLE**, a virtual audio cable, so OBS can capture the video's sound
  without playing it on your speakers. By default (feature `audio_sink`) vrec routes only the recorded
  page's own sound there itself, using a brief microphone permission, reset to "ask" afterwards, to look up
  the output device by name (it never actually reads or uses the microphone); routing Chrome through
  the Windows volume mixer, as described in
  [setup-windows.md](docs/setup-windows.md#2-vb-cable-virtual-audio-cable), is then just a fallback.
- **End detection**: the page's video element reports when playback has ended; if that signal never
  arrives, recording is cut once the video's duration plus a couple of minutes has elapsed (reviewed
  afterwards, not retried automatically). Independently of that, a hard wall-clock cap (duration x 3
  plus 10 minutes by default) stops the recording no matter what, even mid-pause; hitting it is treated
  as a failure and retried by "Record everything".
- **Buffering pauses**: if the loaded reserve drops below a threshold (2 s by default), both the video
  and the OBS recording are paused until it climbs back above another threshold (10 s by default), or
  has simply stopped growing for a few seconds, then resumed together, so the final file never
  contains a frozen frame. A load that stays stalled for too long (5 minutes by default) is cut, and
  vrec immediately retries that same video once, one quality step lower.
- **Quality forcing**: the player's manifest is intercepted so it only offers the best available
  quality, instead of letting adaptive streaming start low and ramp up.
- **vrec's own OBS scene** (feature `obs_scene`, `[obs] scene`, default `"vrec"`): by default vrec
  records from a dedicated scene instead of whatever scene is current in OBS. The first time, it
  creates that scene with a display capture ("`<scene>` screen", cursor hidden, fitted to the canvas)
  pointed at the virtual display; if the scene already exists, it's reused as is: only a missing
  display capture is added, so anything you already set up on it (a crop filter for 360 videos, say)
  stays. Its own VB-CABLE audio source is added to it too. OBS switches to it for the batch and back to
  the scene you were on afterwards, also after Ctrl+C, and on the next start if vrec was killed before
  it could switch back. Your other scenes, and OBS's global settings (resolution, encoder), are never
  touched. Hidden sources don't count: if vrec's own capture was hidden in the scene, vrec shows it
  again. Other visible captures in that scene would be recorded over Chrome, so vrec hides them while
  it records and shows them again afterwards (also after Ctrl+C, and on the next start if vrec was
  killed). If one is still visible, the pre-flight check fails.
- **Screen or window capture** (`[obs] capture`, default `"screen"`): `"screen"` records the whole
  virtual screen through a display capture. `"window"` records only the recording Chrome window through
  a window capture ("`<scene>` window", "Windows 10" capture method, cursor hidden). The window's title
  changes with every page, so vrec points the capture at the right Chrome window before each video and
  keeps it on it during playback; it only ever matches that exact window, never your usual Chrome.
  A visible window capture you already added to the scene is used instead of creating one.
- **Pre-flight check** (feature `preflight_check`): right before the first video of every batch, and in
  `--test` mode, vrec opens a small local test page (served from `http://127.0.0.1`) in the recording
  Chrome and checks the whole chain end to end: the virtual screen is present; Chrome is on it and
  fullscreen; Chrome is shown on all virtual desktops (informative only, never blocks); OBS actually sees Chrome (the page shows two solid colors in turn, and OBS's capture must
  show them; if vrec's own display capture is filming another screen, vrec tries the other screens OBS
  offers it and keeps the one that works); and sound reaches OBS (a 1 s 440 Hz tone sent to CABLE Input
  must move OBS's meter, normally inaudible since it's routed to the cable rather than played out
  loud). It briefly uses the same microphone permission as `audio_sink` does (reset to "ask" afterwards), for
  the same reason: finding the right audio output by name. Results print as `[ OK ]`/`[FAIL]`/`[SKIP]`
  lines; on failure, the menu asks "Record anyway? (y/N)", while `--all`/`--only` stop before recording
  anything.
- **Working on another virtual desktop** (feature `pin_all_desktops`): Windows virtual desktops span
  every screen, so switching desktops would show the wallpaper on the virtual screen instead of Chrome.
  Before recording, vrec makes the recording Chrome window "Show this window on all desktops" and puts
  it back afterwards (a window you pinned yourself stays pinned), so you can record on desktop 3 and
  work on desktop 1. If that fails you get a warning with the manual steps (Win+Tab, right-click the
  window). As a safety net (feature `desktop_pause`), if Chrome still isn't visible on the current
  desktop while a video plays, the video and the OBS recording are paused, like when buffering, and
  resume when it is back; that time is excluded from the recording. vrec's own OBS capture doesn't film the mouse cursor unless `[obs] capture_cursor` is `true`,
  so your mouse crossing onto the virtual screen isn't recorded.
- **Feature toggles** let you turn any of the behavior above off individually if it misbehaves for
  you; see [Features on/off](#features-onoff) below.
- **`vrec --doctor`** checks your setup (OBS, Chrome, disk space, audio routing, the virtual screen...)
  without recording anything. Run it first whenever something looks wrong.

## Requirements

- Windows 10 or 11
- Python 3.11+
- OBS Studio 28+ with the WebSocket server enabled
- Google Chrome
- [VB-CABLE](https://vb-audio.com/Cable/) (virtual audio cable)
- A virtual or second display (see [docs/virtual-display.md](docs/virtual-display.md))

## Quick start

### Without Python

1. Download `vrec-<version>-windows.zip` from the [Releases page](https://github.com/yaniswav/vrec/releases)
   and unzip it anywhere.
2. Do the one-time setup in [docs/setup-windows.md](docs/setup-windows.md) (OBS, Chrome, VB-CABLE).
3. Copy `videos.example.txt` to `data\videos.txt` next to `vrec.exe` and add your links.
4. Double-click `launch_chrome.bat`, log in to the site if needed, then double-click `test.bat`.
   `doctor.bat` checks your setup and `start.bat` records your list.

The zip also holds the `docs` folder, so the links to the setup guides work offline. To check that a
download is intact, download `SHA256SUMS.txt` from the same release, then in PowerShell:

```powershell
Get-FileHash .\vrec-<version>-windows.zip -Algorithm SHA256
```

The hash printed must match the line for that zip in `SHA256SUMS.txt`. If vrec.exe says "This
download looks incomplete or corrupted. Download the zip again and re-extract it.", download the zip
again and unzip it into a fresh folder.

### With Python

1. Follow the one-time setup: [docs/setup-windows.md](docs/setup-windows.md).
2. Run `scripts\windows\install.bat` to install vrec and its dependencies.
3. Put your links in `data\videos.txt` (see [videos.example.txt](videos.example.txt) for the format).
   A link listed twice is recorded once. Duplicates ignore tracking parameters (`utm_*`, `fbclid`,
   `gclid`, `ref`, `ref_src`, `si`, `feature`); links that differ in any other query parameter (for
   example `?v=`) are different videos.
4. Run `vrec --doctor` to check that OBS, Chrome, VB-CABLE and disk space are all ready. Fix anything
   it reports as `[FAIL]` before continuing.
5. Run `scripts\windows\test.bat` for a quick 30-second test and diagnostic.
6. Run `scripts\windows\start.bat` to record your list.

You don't need to open OBS or the recording Chrome by hand first: by default vrec starts OBS itself if
it isn't already open (feature `auto_start_obs`), and also starts the recording Chrome itself if its
debug port doesn't answer (feature `auto_start_chrome`); both are left open afterwards. Before the menu
shows up, vrec also places Chrome on the virtual display, maximized (feature `auto_place_window`), so
it's ready and visible while you pick videos. Run `scripts\windows\launch_chrome.bat` the first time (or
whenever you need to): it opens that same recording Chrome and places it the same way, whatever
`auto_start_chrome` says, then exits, so you can log in to a site before running `start.bat`/`test.bat`.

## Usage

Running `start.bat` (or `vrec`) shows a menu of your videos with their current status, then:

```
1 - Record everything (N video(s): the new ones and the failures)
2 - Choose which ones, in the order you want (even already-done ones)
3 - Mark videos as already done (without recording them)
4 - Reset videos back to NEW
5 - Features on/off
Q - Quit
```

For option 2, type numbers in the order you want them recorded, for example `3,1,5-8` (`5-8` means
5 through 8). vrec then offers to continue with everything else not yet done, which is handy for
bumping a few videos to the front of the queue. Option 5 opens the same list as
[Features on/off](#features-onoff) below: type numbers to flip toggles, saved immediately.

Press **Ctrl+C** at any point to stop. The current recording is stopped cleanly, OBS audio settings
are restored, and the Chrome window is put back the way it was. Videos already finished stay marked
as done; the one that was in progress is marked as failed in the history (detail "interrupted"), and
its file is renamed `INTERRUPTED - <title>`. vrec exits with code 130.

### Keyboard controls

While a batch records (started from a terminal, including `--all` run by hand), these keys work in the
vrec window, upper or lower case. vrec prints `Keys: P pause, S skip, R restart, Q stop after this video, H help`
before the first video. They are off in a scheduled run, and with `vrec --disable hotkeys`.

| Key | What it does |
|---|---|
| `P` | Pause or resume. The video and the OBS recording pause together, and that time is left out of the recording and of the stall, frozen, black and time-cap checks. |
| `S` | Skip this video. The partial file is named `SKIPPED - <title>`, its history entry is left as it was, and it isn't counted as a failure. |
| `R` | Restart this video from the beginning. The partial file is deleted and the video is recorded again. |
| `Q` | Stop the batch after this video (press again to cancel). The summary still prints. |
| `H` | Show this list. |

Ctrl+C still stops everything at once.

In a terminal, the last two lines of the window stay fixed during a batch (feature `status_bar`). The
first shows the current video (`3/59`, playing, paused, buffering or away from the virtual desktop, the
progress, the image size), the batch totals (OK, failed, skipped) and the estimated end time of the whole
batch. The second lists these keys. Everything else scrolls above them. It is off in a scheduled run, when
the output isn't a terminal, and with `vrec --disable status_bar`; vrec then prints the usual progress
line. The log file never contains the bar.

### Non-interactive runs

Skip the menu entirely, for scripting or scheduling:

- `vrec --all` records every video still to do (`NEW`/`FAILED`), in playlist order, with no menu and
  no "press Enter to start" prompt.
- `vrec --only 3,1,5-8` records exactly those videos, in that order (same syntax as menu option 2).
- `vrec --test --only 4` runs `--test` mode against video 4 specifically, instead of the first one.

`--all` and `--only` are mutually exclusive, and `--test` can't be combined with `--all`.

### Scheduled runs

```
vrec --schedule on HH:MM [--days MON,TUE,...]
vrec --schedule off
vrec --schedule status
```

`--schedule on` registers a Windows Task Scheduler task that runs `vrec --all` unattended, every day
at `HH:MM` by default, or only on the given days with `--days`. `--schedule off` removes it, and
`--schedule status` reports whether it's registered, its next run time, and the result of its last
run. vrec starts OBS and the recording Chrome itself (features `auto_start_obs` and
`auto_start_chrome`, on by default). What has to be true at that time:

- The PC is awake and you are logged in to Windows. The task only runs while you are logged on.
- The OBS WebSocket password is saved (run vrec once by hand), or `VREC_OBS_PASSWORD` is set. A run
  with no saved password can't ask for it, so it fails with "No OBS WebSocket password saved. Run vrec
  once by hand to save it, or set VREC_OBS_PASSWORD."
- The recording Chrome profile is logged in to the site (use `launch_chrome.bat` once).

The task's last result is the exit code of `vrec --all` (see [Exit codes](#exit-codes)): `0` means
every video was recorded fine, anything else means a video needs a look or the batch stopped early.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Every video is OK, or there was nothing to record |
| `1` | At least one video is not OK (`CHECK`, `FAILED` or `ERROR`), or the batch stopped early |
| `130` | Stopped with Ctrl+C |

### The OBS password

The first time, vrec asks for the OBS WebSocket password (**Tools > WebSocket Server Settings > Show
Connect Info** in OBS). What you type is hidden, and the prompt is not written to the run log. The
password is saved in plain text in `data\obs_password.txt`; set `VREC_OBS_PASSWORD` instead if you
don't want a file. If OBS rejects it, vrec says so, deletes the saved file and asks again on the next
run. A run with no keyboard (`--all` from a scheduled task, for instance) can't ask: it fails with
"No OBS WebSocket password saved. Run vrec once by hand to save it, or set VREC_OBS_PASSWORD."

### Diagnosing your setup: `vrec --doctor`

Runs every check without recording anything: the playlist and config files, feature toggles, the OBS
connection/version/output settings, free disk space, the capture source, VB-CABLE, how the
video's sound is routed, Chrome's debug port, and which screen recording will use. Each line is
`[ OK ]`, `[WARN]`, `[FAIL]` or `[INFO]`, with a `->` hint for anything that isn't `[ OK ]`. Exits with
a non-zero code if any check failed. Run it first whenever something looks wrong, and include its
output in a bug report.

### CLI flags

```
vrec [--test] [--data-dir DIR] [--config FILE] [--pause-on-exit] [--launch-chrome]
     [--all | --only LIST] [--doctor] [--features] [--enable NAME...] [--disable NAME...]
     [--schedule ACTION... [--days MON,TUE,...]]
     [--install-display-helper [PATTERN] | --uninstall-display-helper] [--version]
```

| Flag | Meaning |
|---|---|
| `--test` | record 30 s of the first video and run a diagnostic |
| `--data-dir DIR` | folder for videos.txt, config.toml, history.json (default: `VREC_DATA_DIR` env var, else `./data`) |
| `--config FILE` | config file path (default: `<data-dir>/config.toml`) |
| `--pause-on-exit` | wait for Enter before closing (used by the `.bat` files) |
| `--launch-chrome` | open the recording Chrome on the virtual screen (e.g. to log in), then exit |
| `--all` | record every video still to do (NEW/FAILED), no menu |
| `--only LIST` | record exactly these numbers, e.g. `3,1,5-8`, no menu |
| `--doctor` | check your setup (OBS, Chrome, disk...) without recording |
| `--features` | show which optional features are on or off, then exit |
| `--enable NAME [NAME ...]` | turn one or more features on |
| `--disable NAME [NAME ...]` | turn one or more features off |
| `--schedule ACTION [ACTION ...]` | manage a scheduled unattended run: `on HH:MM`, `off`, or `status` |
| `--days MON,TUE,...` | days for `--schedule on` (default: every day) |
| `--install-display-helper [PATTERN]` | once, as administrator: let vrec turn the virtual display on/off (PATTERN matches the display adapter name, default `*Virtual*`) |
| `--uninstall-display-helper` | remove what `--install-display-helper` added |
| `--version` | print the version and exit |

## Configuration

Settings live in a TOML file (default `data\config.toml`), documented in
[config.example.toml](config.example.toml). Copy it to `data\config.toml` and edit as needed. Every key
is optional; a missing file or key falls back to the default shown below.

| Section | Key | Default | Meaning |
|---|---|---|---|
| `[obs]` | `host` | `127.0.0.1` | Address of the machine running OBS (`127.0.0.1` rather than `localhost`: on Windows it saves a few seconds when OBS is closed). |
| `[obs]` | `port` | `4455` | obs-websocket server port. |
| `[obs]` | `audio_source_name` | `Chrome Audio (VB-CABLE)` | Name of the OBS audio input source that carries Chrome's sound; created automatically if missing. |
| `[obs]` | `path` | (empty) | Path to `obs64.exe`, if vrec can't find it itself (feature `auto_start_obs`). Empty = look in the registry, then the default install location. |
| `[obs]` | `start_timeout` | `60` | Seconds to wait for OBS's WebSocket server to answer after starting it (feature `auto_start_obs`). |
| `[obs]` | `scene` | `vrec` | Name of the OBS scene vrec records from (feature `obs_scene`); created automatically, with a display or window capture, if it doesn't already exist. |
| `[obs]` | `capture` | `screen` | What OBS records: `screen` (display capture of the virtual screen) or `window` (window capture of the recording Chrome window). |
| `[obs]` | `capture_cursor` | `false` | Film the mouse cursor in vrec's own OBS capture (`<scene> screen` / `<scene> window`). Off by default, so your mouse never shows up when it crosses onto the virtual screen. Your own capture sources are never changed. |
| `[chrome]` | `debug_port` | `9222` | Remote debugging port Chrome was started with (see `launch_chrome.bat`). |
| `[chrome]` | `path` | (empty) | Path to `chrome.exe`, if vrec can't find it itself (feature `auto_start_chrome`). Empty = look in the standard install locations, then the registry. |
| `[chrome]` | `profile` | (empty) | Chrome profile directory used when vrec starts the recording Chrome itself (feature `auto_start_chrome`). Empty = `VREC_CHROME_PROFILE`, then `%LocalAppData%\vrec\chrome-profile` (same as `launch_chrome.bat`). |
| `[chrome]` | `start_timeout` | `30` | Seconds to wait for Chrome's debugging port to answer after starting it (feature `auto_start_chrome`). |
| `[recording]` | `lead_in` | `2` | Seconds recorded before playback starts. |
| `[recording]` | `tail` | `2` | Seconds recorded after the video ends. |
| `[recording]` | `fullscreen_settle` | `2` | Seconds to wait after going fullscreen before rewinding the video. |
| `[recording]` | `test_duration` | `30` | Duration recorded in `--test` mode, in seconds. |
| `[recording]` | `max_wall_factor` | `3` | Wall-clock safety cap: give up on a video after roughly (duration x this factor) seconds. |
| `[recording]` | `max_wall_extra` | `600` | Extra seconds added on top of that cap (also used when the duration is unknown). |
| `[recording]` | `min_free_gb` | `5` | Free space (GB) the OBS recording folder must have before each video (feature `disk_space_guard`). |
| `[checks]` | `black_level` | `20` | A frame is considered black when its brightest pixel (0-255) is below this level. |
| `[checks]` | `abort_if_black_after` | `60` | Give up on a video that stays black for this many seconds. |
| `[checks]` | `abort_if_frozen_after` | `180` | Give up on a video when OBS keeps recording the same still picture for this many seconds of playback (feature `frozen_check`). |
| `[checks]` | `audio_level` | `0.003` | Minimum audio level to consider that there is sound (roughly -50 dB). |
| `[buffering]` | `pause_below` | `2` | Seconds of buffered video below which playback and recording pause. |
| `[buffering]` | `resume_at` | `10` | Seconds of buffered video required before playback and recording resume. |
| `[buffering]` | `max_stall` | `300` | Give up on a video whose loading stays stalled for this many seconds. |
| `[quality]` | `max_height` | `0` | Highest video height (px) vrec asks the player for; `0` = no cap, always the best. |
| `[display]` | `screen` | `auto` | Screen the recording Chrome window is moved to (feature `auto_place_window`). `auto` = the largest screen that isn't your main one, or your main screen if it is the only one; or a number (`2`) or a name (`DISPLAY3`). See [docs/virtual-display.md](docs/virtual-display.md). |
| `[audio]` | `output` | `CABLE Input` | Audio output that receives the recorded video's sound (feature `audio_sink`). Any part of the device name works. |

Environment variables:

| Variable | Purpose |
|---|---|
| `VREC_DATA_DIR` | Overrides the data directory (default `./data`) |
| `VREC_OBS_PASSWORD` | OBS WebSocket password, skips the password file/prompt. Needed for scheduled runs if you never saved the password by hand |
| `VREC_CHROME_PROFILE` | Chrome profile directory vrec starts the recording Chrome with, including via `launch_chrome.bat` (`vrec --launch-chrome`), when `[chrome] profile` isn't set in `config.toml` (default `%LOCALAPPDATA%\vrec\chrome-profile`) |

## Features on/off

Optional behavior can be turned on or off without touching any code: menu option **5**, the CLI
(`vrec --features`, `vrec --enable NAME...`, `vrec --disable NAME...`), or by hand in
`data\features.toml` (a missing file, or a missing key in it, means "use the default" below).

The following are always on and have no toggle, because turning them off would risk losing data:
saving to `data\history.json`, restoring OBS audio mute states left over from an interrupted run, and
the single-instance lock (`data\vrec.lock`).

| Feature | Default | What it does |
|---|---|---|
| `quality_filter` | ON | Force the best stream quality from the first second |
| `quality_retry` | ON | Retry a stalled video once at the next lower quality |
| `buffer_pause` | ON | Pause OBS while the player buffers, so no frozen frames are recorded |
| `black_check` | ON | Detect a black image and give up on protected videos |
| `frozen_check` | ON | Detect a frozen image (OBS filming a still picture) and give up |
| `audio_check` | ON | Warn when no audio reaches OBS |
| `obs_audio_routing` | ON | Set up OBS audio automatically (capture VB-CABLE, mute desktop and mic) |
| `wall_clock_cap` | ON | Hard wall-clock time limit per video |
| `circuit_breaker` | ON | Stop the batch when Chrome or OBS is gone, or after 3 errors in a row |
| `run_logs` | ON | Write a log file for each run in `data\logs` |
| `auto_place_window` | ON | Move the recording Chrome window to the virtual display automatically |
| `manage_virtual_display` | OFF | Turn the virtual display on before a batch and off after it |
| `pin_all_desktops` | ON | Show the recording Chrome on all virtual desktops, so you can switch desktops |
| `desktop_pause` | ON | Pause the recording while Chrome isn't on the current virtual desktop |
| `hotkeys` | ON | Keyboard controls during a batch: P pause, S skip, R restart, Q stop after this video |
| `status_bar` | ON | Keep a 2-line status bar (state, progress, ETA, keys) at the bottom of the terminal |
| `audio_sink` | ON | Send only the recorded video's sound to CABLE Input (no Windows mixer setup) |
| `auto_start_obs` | ON | Start OBS if it isn't open (it stays open afterwards) |
| `auto_start_chrome` | ON | Start the recording Chrome if it isn't open (it stays open afterwards) |
| `obs_scene` | ON | Record from vrec's own OBS scene (created if missing), then switch back |
| `preflight_check` | ON | Before a batch, check screen, window, OBS capture and sound end to end |
| `protect_console` | ON | Stop a click in the vrec window from pausing it (Windows QuickEdit) |
| `keep_awake` | ON | Keep Windows from sleeping or turning the screens off while recording |
| `disk_space_guard` | ON | Stop the batch before a video when the recording disk is almost full |
| `notify_when_done` | ON | Show a Windows notification when a batch or a test is over |

## Output files & statuses

Recordings are written to your OBS recording folder. File name prefixes:

| Prefix / suffix | Meaning |
|---|---|
| `<title>.mp4` | Recorded normally |
| `<title> (2).mp4` | Re-recorded; the previous file is never overwritten |
| `TEST - <title>` | Produced by `--test` mode |
| `FAILED black image - <title>` | Image stayed black for too long, recording abandoned (likely DRM-protected) |
| `FAILED frozen image - <title>` | The picture in OBS never changed while the video played, recording abandoned |
| `INCOMPLETE - <title>` | Loading stalled (even after the automatic retry), the video was removed from the page, the wall-clock cap was hit, or OBS stopped recording |
| `INTERRUPTED - <title>` | Recording was cut short by Ctrl+C or an error |

File names are cleaned up: characters Windows doesn't allow and control characters are removed, and a
name Windows reserves for devices (`CON`, `NUL`, `COM1`...) gets a `_` after the base name.

End-of-run status shown in the menu and summary:

| Status | Meaning |
|---|---|
| `OK` (-> DONE) | Everything went fine |
| `CHECK: ...` (-> REVIEW) | Recorded, but something needs a look: black image?, no audio, and/or the time limit was reached before the end was detected. Not retried automatically. |
| `FAILED: black image (protected video?)` (-> FAILED) | Image stayed black the whole time (probably a protected video) |
| `FAILED: frozen image (OBS isn't filming the video?)` (-> FAILED) | OBS kept recording the same still picture while the video played |
| `FAILED: incomplete (...)` (-> FAILED) | Loading stalled, the video was removed from the page, it took too long overall, or OBS stopped recording |
| `ERROR: ...` (-> FAILED) | A problem occurred on the page; the message explains what |

`FAILED` results (of any kind above) are retried by "Record everything". A stalled load also gets one
immediate retry, one quality step lower, before it is ever counted as failed.

See [docs/troubleshooting.md](docs/troubleshooting.md) for every message in detail.

## Project layout

```
src/vrec/            application source
  app.py              batch orchestration (the steps a run goes through)
  monitor.py           the playback-watching loop (buffering, end detection, wall-clock cap)
  recorder.py          recording a single video: fullscreen, quality, OBS start/stop, live checks
  browser.py            Chrome connection (CDP), window placement, per-page audio routing
  obs_control.py        OBS WebSocket control: connect, audio setup, screenshots
  obs_scene.py           vrec's own OBS scene: create/reuse it, then switch to it and back
  preflight.py           pre-flight check: local test page, screen/window/capture/sound checks
  vdesktop.py            virtual desktops: find Chrome's window, show it on all desktops
  launcher.py           starting OBS/Chrome themselves if they aren't already open
  display.py            screens and the virtual-display on/off helper
  doctor.py             `vrec --doctor` checks
  schedule.py           `vrec --schedule` (Windows Task Scheduler)
  features.py           feature toggle registry and data/features.toml
  logs.py               per-run log files (data/logs)
  lock.py               single-instance lock
  history.py, naming.py, playlist.py, config.py, console.py, menu.py  supporting modules
  js/                    JS snippets injected into the recorded page
scripts/windows/    install.bat, start.bat, test.bat, launch_chrome.bat
docs/                setup, virtual display, troubleshooting (also in the Windows zip)
config.example.toml  documented settings template
videos.example.txt   videos.txt format reference
```

## Development

```
pip install -e ".[dev]"
pre-commit install
ruff check .
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full developer workflow (pre-commit, coverage, mypy).

## Responsible use

vrec is meant for recording video content that you are entitled to access, for your own personal use.
Always respect the website's terms of service and applicable copyright law. vrec does not bypass DRM
or any other content protection: a protected video simply renders black on screen and is skipped with
a `FAILED black image` result. If the source offers an official download or app, prefer that: a
screen recording is always a little less sharp than the original file.

## License

MIT, see [LICENSE](LICENSE).
