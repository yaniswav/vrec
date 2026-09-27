# vrec

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

## How it works

- **Chrome** is driven over the DevTools protocol (CDP) on a dedicated profile. By default vrec moves
  its window onto the virtual display itself before each batch and puts it back afterwards (feature
  `auto_place_window`); moving it there by hand (**Win + Shift + Right Arrow**) is a fallback for when
  that's off or doesn't work for you.
- **OBS** is controlled through its WebSocket API: starting/stopping recordings, checking the current
  scene, and reading the audio meter.
- **Audio** is sent through **VB-CABLE**, a virtual audio cable, so OBS can capture the video's sound
  without playing it on your speakers. By default (feature `audio_sink`) vrec routes only the recorded
  page's own sound there itself, using a brief, automatically-revoked microphone permission to look up
  the output device by name (it never actually reads or uses the microphone); routing Chrome through
  the Windows volume mixer, as described in
  [setup-windows.md](docs/setup-windows.md#2-vb-cable-virtual-audio-cable), is then just a fallback.
- **End detection**: the page's video element reports when playback has ended; if that signal never
  arrives, recording is cut once the video's duration plus a couple of minutes has elapsed (reviewed
  afterwards, not retried automatically). Independently of that, a hard wall-clock cap (duration x 3
  plus 10 minutes by default) stops the recording no matter what, even mid-pause; hitting it is treated
  as a failure and retried by "Record everything".
- **Buffering pauses**: if the loaded reserve drops below a threshold (2 s by default), both the video
  and the OBS recording are paused until it climbs back above another threshold (10 s by default) — or
  has simply stopped growing for a few seconds — then resumed together, so the final file never
  contains a frozen frame. A load that stays stalled for too long (5 minutes by default) is cut, and
  vrec immediately retries that same video once, one quality step lower.
- **Quality forcing**: the player's manifest is intercepted so it only offers the best available
  quality, instead of letting adaptive streaming start low and ramp up.
- **Feature toggles** let you turn any of the behavior above off individually if it misbehaves for
  you — see [Features on/off](#features-onoff) below.
- **`vrec --doctor`** checks your setup (OBS, Chrome, disk space, audio routing, the virtual screen...)
  without recording anything — run it first whenever something looks wrong.

## Requirements

- Windows 10 or 11
- Python 3.11+
- OBS Studio 28+ with the WebSocket server enabled
- Google Chrome
- [VB-CABLE](https://vb-audio.com/Cable/) (virtual audio cable)
- A virtual or second display (see [docs/virtual-display.md](docs/virtual-display.md))

## Quick start

1. Follow the one-time setup: [docs/setup-windows.md](docs/setup-windows.md).
2. Run `scripts\windows\install.bat` to install vrec and its dependencies.
3. Run `scripts\windows\launch_chrome.bat` to open the dedicated recording Chrome window. vrec moves
   it to the virtual display for you before each run (feature `auto_place_window`); move it there
   yourself with **Win + Shift + Right Arrow** only as a fallback, if you turn that off.
4. Put your links in `data\videos.txt` (see [videos.example.txt](videos.example.txt) for the format).
5. Run `vrec --doctor` to check that OBS, Chrome, VB-CABLE and disk space are all ready. Fix anything
   it reports as `[FAIL]` before continuing.
6. Run `scripts\windows\test.bat` for a quick 30-second test and diagnostic.
7. Run `scripts\windows\start.bat` to record your list.

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
as done; the one that was in progress is renamed `INTERRUPTED - <title>` and marked as failed.

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
run. OBS and the recording Chrome window (`launch_chrome.bat`) must already be open at that time, and
the PC must be awake — the scheduled task doesn't start them for you.

### Diagnosing your setup: `vrec --doctor`

Runs every check without recording anything: the playlist and config files, feature toggles, the OBS
connection/version/output settings, free disk space, the display capture source, VB-CABLE, how the
video's sound is routed, Chrome's debug port, and which screen recording will use. Each line is
`[ OK ]`, `[WARN]`, `[FAIL]` or `[INFO]`, with a `->` hint for anything that isn't `[ OK ]`. Exits with
a non-zero code if any check failed. Run it first whenever something looks wrong, and include its
output in a bug report.

### CLI flags

```
vrec [--test] [--data-dir DIR] [--config FILE] [--pause-on-exit]
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
| `[obs]` | `host` | `localhost` | Hostname of the machine running OBS. |
| `[obs]` | `port` | `4455` | obs-websocket server port. |
| `[obs]` | `audio_source_name` | `Chrome Audio (VB-CABLE)` | Name of the OBS audio input source that carries Chrome's sound; created automatically if missing. |
| `[chrome]` | `debug_port` | `9222` | Remote debugging port Chrome was started with (see `launch_chrome.bat`). |
| `[recording]` | `lead_in` | `2` | Seconds recorded before playback starts. |
| `[recording]` | `tail` | `2` | Seconds recorded after the video ends. |
| `[recording]` | `fullscreen_settle` | `2` | Seconds to wait after going fullscreen before rewinding the video. |
| `[recording]` | `test_duration` | `30` | Duration recorded in `--test` mode, in seconds. |
| `[recording]` | `max_wall_factor` | `3` | Wall-clock safety cap: give up on a video after roughly (duration x this factor) seconds. |
| `[recording]` | `max_wall_extra` | `600` | Extra seconds added on top of that cap (also used when the duration is unknown). |
| `[checks]` | `black_level` | `20` | Maximum average brightness (0-255) below which a frame is considered black. |
| `[checks]` | `abort_if_black_after` | `60` | Give up on a video that stays black for this many seconds. |
| `[checks]` | `audio_level` | `0.003` | Minimum audio level to consider that there is sound (roughly -50 dB). |
| `[buffering]` | `pause_below` | `2` | Seconds of buffered video below which playback and recording pause. |
| `[buffering]` | `resume_at` | `10` | Seconds of buffered video required before playback and recording resume. |
| `[buffering]` | `max_stall` | `300` | Give up on a video whose loading stays stalled for this many seconds. |
| `[quality]` | `max_height` | `0` | Highest video height (px) vrec asks the player for; `0` = no cap, always the best. |
| `[display]` | `screen` | `auto` | Screen the recording Chrome window is moved to (feature `auto_place_window`). `auto` = the largest screen that isn't your main one; or a number (`2`) or a name (`DISPLAY3`). See [docs/virtual-display.md](docs/virtual-display.md). |
| `[audio]` | `output` | `CABLE Input` | Audio output that receives the recorded video's sound (feature `audio_sink`). Any part of the device name works. |

Environment variables:

| Variable | Purpose |
|---|---|
| `VREC_DATA_DIR` | Overrides the data directory (default `./data`) |
| `VREC_OBS_PASSWORD` | OBS WebSocket password, skips the password file/prompt |
| `VREC_CHROME_PORT` | Chrome DevTools port that `launch_chrome.bat` starts Chrome with (default `9222`). vrec itself connects on `[chrome] debug_port` from `config.toml`, so change both together if you use a non-default port. |
| `VREC_CHROME_PROFILE` | Chrome profile directory used by `launch_chrome.bat` (default `%LOCALAPPDATA%\vrec\chrome-profile`) |

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
| `audio_check` | ON | Warn when no audio reaches OBS |
| `obs_audio_routing` | ON | Set up OBS audio automatically (capture VB-CABLE, mute desktop and mic) |
| `wall_clock_cap` | ON | Hard wall-clock time limit per video |
| `circuit_breaker` | ON | Stop the batch when Chrome or OBS is gone, or after 3 errors in a row |
| `run_logs` | ON | Write a log file for each run in `data\logs` |
| `auto_place_window` | ON | Move the recording Chrome window to the virtual display automatically |
| `manage_virtual_display` | OFF | Turn the virtual display on before a batch and off after it |
| `audio_sink` | ON | Send only the recorded video's sound to CABLE Input (no Windows mixer setup) |

## Output files & statuses

Recordings are written to your OBS recording folder. File name prefixes:

| Prefix / suffix | Meaning |
|---|---|
| `<title>.mp4` | Recorded normally |
| `<title> (2).mp4` | Re-recorded; the previous file is never overwritten |
| `TEST - <title>` | Produced by `--test` mode |
| `FAILED black image - <title>` | Image stayed black for too long, recording abandoned (likely DRM-protected) |
| `INCOMPLETE - <title>` | Loading stalled (even after the automatic retry), the video was removed from the page, or the wall-clock cap was hit |
| `INTERRUPTED - <title>` | Recording was cut short by Ctrl+C or an error |

End-of-run status shown in the menu and summary:

| Status | Meaning |
|---|---|
| `OK` (-> DONE) | Everything went fine |
| `CHECK: ...` (-> REVIEW) | Recorded, but something needs a look: black image?, no audio, and/or the time limit was reached before the end was detected. Not retried automatically. |
| `FAILED: black image (protected video?)` (-> FAILED) | Image stayed black the whole time (probably a protected video) |
| `FAILED: incomplete (...)` (-> FAILED) | Loading stalled, the video was removed from the page, or it took too long overall |
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
  display.py            screens and the virtual-display on/off helper
  doctor.py             `vrec --doctor` checks
  schedule.py           `vrec --schedule` (Windows Task Scheduler)
  features.py           feature toggle registry and data/features.toml
  logs.py               per-run log files (data/logs)
  lock.py               single-instance lock
  history.py, naming.py, playlist.py, config.py, console.py, menu.py  supporting modules
  js/                    JS snippets injected into the recorded page
scripts/windows/    install.bat, start.bat, test.bat, launch_chrome.bat
docs/                setup, virtual display, troubleshooting
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
a `FAILED black image` result. If the source offers an official download or app, prefer that — a
screen recording is always a little less sharp than the original file.

## License

MIT, see [LICENSE](LICENSE).
