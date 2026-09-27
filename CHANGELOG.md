# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
