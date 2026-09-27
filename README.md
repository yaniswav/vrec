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

## How it works

- **Chrome** is driven over the DevTools protocol (CDP) on a dedicated profile, positioned on an
  invisible/virtual display so it never gets in your way.
- **OBS** is controlled through its WebSocket API: starting/stopping recordings, checking the current
  scene, and reading the audio meter.
- **Audio** is routed through **VB-CABLE**, a virtual audio cable, so OBS can capture Chrome's sound
  without playing it on your speakers.
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
3. Run `scripts\windows\launch_chrome.bat` to open the dedicated recording Chrome window, and move it
   to the virtual display.
4. Put your links in `data\videos.txt` (see [videos.example.txt](videos.example.txt) for the format).
5. Run `scripts\windows\test.bat` for a quick 30-second test and diagnostic.
6. Run `scripts\windows\start.bat` to record your list.

## Usage

Running `start.bat` (or `vrec`) shows a menu of your videos with their current status, then:

```
1 - Record everything (the new ones and the failures)
2 - Choose which ones, in the order you want (even already-done ones)
3 - Mark videos as already done (without recording them)
4 - Reset videos back to NEW
Q - Quit
```

For option 2, type numbers in the order you want them recorded, for example `3,1,5-8` (`5-8` means
5 through 8). vrec then offers to continue with everything else not yet done, which is handy for
bumping a few videos to the front of the queue.

Press **Ctrl+C** at any point to stop. The current recording is stopped cleanly, OBS audio settings
are restored, and the Chrome window is put back the way it was. Videos already finished stay marked
as done; the one that was in progress is renamed `INTERRUPTED - <title>` and marked as failed.

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

Environment variables:

| Variable | Purpose |
|---|---|
| `VREC_DATA_DIR` | Overrides the data directory (default `./data`) |
| `VREC_OBS_PASSWORD` | OBS WebSocket password, skips the password file/prompt |
| `VREC_CHROME_PORT` | Chrome DevTools port that `launch_chrome.bat` starts Chrome with (default `9222`). vrec itself connects on `[chrome] debug_port` from `config.toml`, so change both together if you use a non-default port. |
| `VREC_CHROME_PROFILE` | Chrome profile directory used by `launch_chrome.bat` (default `%LOCALAPPDATA%\vrec\chrome-profile`) |

CLI flags:

```
vrec [--test] [--data-dir DIR] [--config FILE] [--pause-on-exit] [--version]
```

- `--test`: record only a short clip of one video, then print a diagnostic.
- `--data-dir DIR`: use `DIR` instead of `VREC_DATA_DIR` / `./data`.
- `--config FILE`: use `FILE` instead of `<data-dir>/config.toml`.
- `--pause-on-exit`: wait for Enter before closing (used by the `.bat` launchers).
- `--version`: print the version and exit.

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
src/vrec/          application source
scripts/windows/    install.bat, start.bat, test.bat, launch_chrome.bat
docs/                setup, virtual display, troubleshooting
config.example.toml  documented settings template
videos.example.txt   videos.txt format reference
```

## Development

```
pip install -e ".[dev]"
ruff check .
pytest
```

## Responsible use

vrec is meant for recording video content that you are entitled to access, for your own personal use.
Always respect the website's terms of service and applicable copyright law. vrec does not bypass DRM
or any other content protection: a protected video simply renders black on screen and is skipped with
a `FAILED black image` result. If the source offers an official download or app, prefer that — a
screen recording is always a little less sharp than the original file.

## License

MIT, see [LICENSE](LICENSE).
