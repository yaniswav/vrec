# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `vrec --display auto`: a watcher that turns the virtual display off while a program listed in
  `[display] off_while_running` runs (for games whose anti-cheat dislike a virtual display) and back on
  when none runs anymore, printing one line per change. It never turns the display off during a batch,
  and Ctrl+C leaves the display on unless a listed program still runs. A batch now also warns when such
  a program is running. New launcher `display_auto.bat`.
- `vrec --display on|off|status` turns the virtual display on or off, or shows its state (helper, adapter,
  on/off, screens), without starting OBS or Chrome. `off` is refused while a batch is recording, and the
  commands say what to run if the display helper isn't installed. New launchers `display_on.bat`,
  `display_off.bat` and `display_status.bat` (dev checkout and release zip).
- A fixed 2-line status bar at the bottom of the terminal during a batch (feature `status_bar`): the
  current video's state (playing, paused, buffering, away from the virtual desktop, stopping after this
  video), progress, image size, batch totals, the estimated end time of the whole batch, and the
  keyboard shortcuts. Output scrolls above it. It is off in a scheduled run and when the output isn't a
  terminal, and the log file never contains it.

- Keyboard controls during a batch (feature `hotkeys`): `P` pauses and resumes the video and the OBS
  recording together, `S` skips the video (file named `SKIPPED - <title>`, history unchanged, not a
  failure), `R` restarts it from the beginning (the partial file is deleted), `Q` stops the batch after
  the current video (press again to cancel), `H` lists the keys. Off in scheduled runs.

- Working on another virtual desktop while recording (feature `pin_all_desktops`): vrec shows the
  recording Chrome window on all virtual desktops (through `pyvda`, a new dependency) and puts it back
  afterwards, so switching desktops no longer makes OBS film the wallpaper. A new pre-flight line
  reports it, and a warning with the manual steps appears if it can't be done.
- Safety net for virtual desktops (feature `desktop_pause`): when the recording Chrome isn't visible on the
  current virtual desktop, the video and the OBS recording pause until it is back, and that time is
  excluded from the recording.
- `[obs] capture_cursor` (default `false`): whether vrec's own OBS capture films the mouse cursor. Capture
  sources you made yourself are never changed.

### Fixed

- A Chrome tab closed or replaced while vrec waited for Enter (or between two videos) no longer ends the
  run with "Target page, context or browser has been closed": vrec switches to another tab.

## [0.1.0] - 2026-10-01

Initial public release.

### Added

- Frozen image detection (feature `frozen_check`, `[checks] abort_if_frozen_after`, default 180 s): a
  recording where OBS films a still picture while the video plays is stopped and marked
  `FAILED: frozen image`.
- A standalone Windows build: each release now includes `vrec-<version>-windows.zip`, which runs
  without Python. It holds `vrec.exe`, the `docs` folder, example files and `start.bat`, `test.bat`,
  `launch_chrome.bat` and `doctor.bat`.
- `vrec.exe` keeps its window open at the end when started by double-click, as if `--pause-on-exit`
  were given.
- Unattended, sequential recording of a list of web videos through OBS, driven from a menu that shows
  each video's current status, or non-interactively with `--all`/`--only`, or on a schedule
  (`vrec --schedule on HH:MM`).
- Chrome control over the DevTools protocol (CDP): fullscreen playback, rewind to start, and filling
  the window with the video element.
- Automatic quality forcing: intercepts the player's manifest to select the best available quality
  instead of "auto", with a configurable maximum height cap and a menu-based fallback for unrecognized
  players. A stalled load gets one automatic retry at the next lower quality.
- OBS WebSocket integration: scene/recording control, audio meter reading, and automatic setup and
  restoration of mute states around each recording.
- Buffering-aware recording: pauses the video and the OBS recording together when the buffer runs low,
  and resumes automatically once it recovers, avoiding frozen frames in the output.
- End-of-video detection from the page's player, with a hard time-limit fallback and a hard wall-clock
  cap per video.
- History tracking by URL (`data/history.json`), so recordings survive moving or renaming files, and
  videos already recorded are recognized even after replacing the video list.
- File naming conventions for test runs, black-image failures, incomplete recordings, and interrupted
  recordings, with automatic `(2)`, `(3)`... suffixes to avoid overwriting existing files.
- Feature toggles (`vrec --features`/`--enable`/`--disable`, menu option 5, `data/features.toml`) to
  turn optional behavior on or off individually. Data-safety mechanisms (history saving, restoring
  leftover OBS audio settings, the instance lock) are always on and have no toggle.
- `vrec --doctor`: read-only checks of the playlist, config, feature toggles, the OBS
  connection/version/output settings, recording folder space, the display capture source, VB-CABLE,
  video sound routing, Chrome's debug port, and the virtual screen, each with a hint and an exit code.
- `preflight_check` feature: right before the first video of a batch, and in `--test` mode, checks the
  whole chain end to end on a small local test page: virtual screen, Chrome window placement and
  fullscreen, OBS actually seeing Chrome (retargeting vrec's own display capture if needed), and sound
  reaching OBS.
- `obs_scene` feature: records from vrec's own OBS scene instead of whatever scene is current, creating
  it with a display capture if it's missing (reusing an existing scene as is otherwise, so a crop
  filter for 360 videos stays), and switching back to the previous scene afterwards.
- Screen or window capture (`[obs] capture`): record the whole virtual screen, or only the recording
  Chrome window through a window capture that vrec points at the right window before each video.
  A hidden capture is never recorded from: vrec shows its own, and hides the other visible captures
  of its scene while it records (shown again afterwards, also after Ctrl+C or a crash).
- `auto_start_obs` and `auto_start_chrome` features: start OBS and the recording Chrome themselves if
  they aren't already open when a recording, `--test`, or scheduled run begins, and leave them open
  afterwards.
- `auto_place_window` feature: moves the recording Chrome window to the virtual display automatically,
  maximized, before the menu shows up, and fullscreens it only right before recording starts.
- `--launch-chrome` (what `scripts\windows\launch_chrome.bat` runs): opens the recording Chrome on the
  virtual display, for logging in to a site before `start.bat`/`test.bat`.
- `audio_sink` feature: routes only the recorded video's own sound to the configured audio output
  (`[audio] output`, default "CABLE Input"), so the Windows volume mixer doesn't need to be set up by
  hand.
- `manage_virtual_display` feature, with `vrec --install-display-helper`/`--uninstall-display-helper`:
  turns the virtual display on before a batch and off again afterwards, undoing only what it turned on.
- `disk_space_guard` feature: stops the batch before a video when the OBS recording folder is almost
  full, instead of OBS failing mid-recording.
- `keep_awake` feature: keeps Windows from sleeping or turning the screens off while a batch records.
- `protect_console` feature: turns off the console's QuickEdit mode while vrec runs, so a stray click
  in its window can no longer pause it mid-recording.
- `notify_when_done` feature: shows a Windows notification when a batch or a test is over, and ends the
  batch summary with a totals line (OK count, video length, size, time taken).
- Per-run log files in `data/logs` (feature `run_logs`), with the OBS password masked and the last 20
  files kept.
- Exit codes: `0` when every video is OK or there was nothing to record, `1` when a video is not OK or
  the batch stopped early, `130` after Ctrl+C. Ctrl+C marks the video in progress as failed
  ("interrupted") and names its file `INTERRUPTED - <title>`.
- The OBS WebSocket password prompt is hidden, and a run with no keyboard and no saved password fails
  with a clear message instead of hanging (`VREC_OBS_PASSWORD` is the alternative). "OBS refused the
  connection" only appears for a real authentication failure.
- "OBS stopped recording" stop reason: if OBS stops recording during a video, the video ends as
  incomplete and failed instead of waiting for a recording that is gone.
- A `history.json` with an unknown version or that isn't a JSON object is set aside as
  `history.unreadable.json`, and a failed save only warns instead of stopping the batch.
- Safer file names (Windows device names, control characters) and duplicate detection that ignores
  tracking parameters but keeps other query parameters, so `?v=A` and `?v=B` stay separate videos.
- The virtual display helper lives in `%ProgramData%\vrec`, writable only by Administrators and
  SYSTEM, and accepts only a restricted adapter pattern.
- `SHA256SUMS.txt` with each release, and a hidden `--selftest` that checks the packaged exe.
- Single-instance lock to prevent two runs from colliding on the same data directory.
- Command-line interface (`vrec`) with a test mode, configurable data directory and config file, and
  Windows launcher scripts (`install.bat`, `start.bat`, `test.bat`, `launch_chrome.bat`).
- Documentation: setup guide, virtual display options, and troubleshooting reference.
