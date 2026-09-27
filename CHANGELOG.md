# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `auto_start_obs` feature (on by default): starts OBS itself if it isn't already open when a
  recording, `--test`, or scheduled run begins, using `[obs] path` if set, else the registry, else the
  default install location, and leaves it open afterwards. Never starts a second copy: if OBS is
  already running but its WebSocket server doesn't answer, vrec reports that instead. New `[obs] path`
  and `[obs] start_timeout` config keys.
- `auto_start_chrome` feature (on by default): starts the recording Chrome itself if its debug port
  doesn't answer, using `[chrome] path` if set, else the standard install locations, else the registry,
  on the same profile `launch_chrome.bat` uses (`[chrome] profile`, else `VREC_CHROME_PROFILE`, else
  `%LocalAppData%\vrec\chrome-profile`), and leaves it open afterwards. New `[chrome] path`,
  `[chrome] profile`, and `[chrome] start_timeout` config keys.
- `vrec --doctor` reports in advance what these two features would do (`[INFO] ... Not open: vrec will
  start it (...)`) instead of starting anything itself; it stays fully read-only.

## [0.2.0] - 2026-09-27

### Added

- Feature toggles (`vrec --features`/`--enable`/`--disable`, menu option 5, `data/features.toml`):
  turn optional behavior on or off individually. Data-safety mechanisms (history saving, restoring
  leftover OBS audio settings, the instance lock) are always on and have no toggle.
- `vrec --doctor`: read-only checks of the playlist, config, feature toggles, the OBS
  connection/version/output settings, recording folder space, the display capture source, VB-CABLE,
  video sound routing, Chrome's debug port, and the virtual screen, each with a hint and an exit code.
- Per-run log files in `data/logs` (feature `run_logs`): a copy of the console output for each
  recording, `--test`, or `--doctor` run, with the OBS password masked and the last 20 files kept.
- Non-interactive selection: `vrec --all` (every NEW/FAILED video) and `vrec --only 3,1,5-8`, plus
  `--test --only N` to test one specific video.
- Scheduled unattended runs: `vrec --schedule on HH:MM [--days MON,...] | off | status`, backed by
  Windows Task Scheduler.
- `audio_sink` feature (on by default): routes only the recorded video's own sound to the configured
  audio output (`[audio] output`, default "CABLE Input") using a brief, automatically-revoked
  microphone permission to find the device by name; routing Chrome through the Windows volume mixer
  becomes a fallback.
- `auto_place_window` feature (on by default): moves the recording Chrome window to the virtual screen
  automatically and puts it back afterwards; moving it by hand becomes a fallback.
- `manage_virtual_display` feature (off by default) with `vrec --install-display-helper [PATTERN]` /
  `--uninstall-display-helper`: registers elevated on-demand scheduled tasks (administrator rights
  needed once) so vrec can turn the virtual display on before a batch and off after it, undoing only
  what it turned on.
- `[display] screen` and `[audio] output` config keys.

### Fixed

- With a real second monitor connected, `[display] screen = "auto"` could pick it instead of the
  virtual display while the virtual display was off. vrec now reads the virtual adapter's own state
  from Windows instead of guessing from the list of screens.
- A video paused by the page itself right after a playback-progress tick could be replayed one extra
  time; playback progress is now checked before counting a replay.
- An image or audio check that was turned off (or otherwise not run) was reported as failed instead of
  "not checked".

## [0.1.0] - 2026-09-27

Initial public version.

### Added

- Unattended, sequential recording of a list of web videos through OBS, driven from a menu that shows
  each video's current status.
- Chrome control over the DevTools protocol (CDP): fullscreen playback, rewind to start, and filling
  the window with the video element.
- Automatic quality forcing: intercepts the player's manifest to select the best available quality
  instead of "auto", with a configurable maximum height cap and a menu-based fallback for
  unrecognized players.
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
- Single-instance lock to prevent two runs from colliding on the same data directory.
- Automatic one-step-down quality retry after a stalled load.
- Command-line interface (`vrec`) with a test mode, configurable data directory and config file, and
  Windows launcher scripts (`install.bat`, `start.bat`, `test.bat`, `launch_chrome.bat`).
- Documentation: setup guide, virtual display options, and troubleshooting reference.
