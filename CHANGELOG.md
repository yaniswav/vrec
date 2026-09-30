# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- A standalone Windows build: each release now includes `vrec-<version>-windows.zip`, which runs
  without Python. It holds `vrec.exe`, the docs, example files and `start.bat`, `test.bat`,
  `launch_chrome.bat` and `doctor.bat`.
- `vrec.exe` keeps its window open at the end when started by double-click, as if `--pause-on-exit`
  were given.

## [0.1.0] - 2026-09-28

Initial public release.

### Added

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
- Single-instance lock to prevent two runs from colliding on the same data directory.
- Command-line interface (`vrec`) with a test mode, configurable data directory and config file, and
  Windows launcher scripts (`install.bat`, `start.bat`, `test.bat`, `launch_chrome.bat`).
- Documentation: setup guide, virtual display options, and troubleshooting reference.
