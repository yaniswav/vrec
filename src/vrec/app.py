"""Top-level orchestration: read the playlist, drive the menu, record the videos."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import obsws_python as obs

from vrec import history, menu, obs_control
from vrec.browser import connect_browser, window_state
from vrec.config import Paths, Settings, load_settings
from vrec.console import first_line, warn
from vrec.errors import VrecError
from vrec.lock import InstanceLock
from vrec.naming import INTERRUPTED_PREFIX, rename_recording
from vrec.playlist import read_playlist
from vrec.recorder import (
    RecordingResult,
    history_status,
    lower_quality_retry_cap,
    record_one,
    status_text,
)


def run(data_dir: Path, config_path: Path, test_mode: bool) -> int:
    paths = Paths(data_dir=data_dir, config=config_path)
    settings = load_settings(config_path)

    with InstanceLock(paths.lock):
        return _run_locked(paths, settings, test_mode)


def _run_locked(paths: Paths, settings: Settings, test_mode: bool) -> int:
    if not paths.videos.exists():
        raise VrecError(
            f"File not found: {paths.videos}\nCopy videos.example.txt to {paths.videos} and add your links."
        )
    videos = read_playlist(paths.videos)
    if not videos:
        raise VrecError(f"{paths.videos.name} contains no links.")
    videos_history = history.load(paths.history)

    client, password = obs_control.connect(settings, paths)
    _restore_leftover_audio(client, paths.obs_restore)

    if client.get_record_status().output_active:
        raise VrecError("An OBS recording is already in progress. Stop it, then restart.")
    try:
        record_dir = client.get_record_directory().record_directory
    except Exception:
        record_dir = None
    history.adopt_existing_files(videos, videos_history, record_dir, paths.history)

    if test_mode:
        print(f"\nTEST MODE: {settings.test_duration_s:.0f} s of one video, to check your settings.")
        selection = menu.choose_test_video(videos, videos_history)
    else:
        selection = menu.main_menu(videos, videos_history, paths.history)
    if not selection:
        return 0

    scene = obs_control.current_scene(client)
    mutes: dict[str, bool] = {}
    listener = None
    meter = None
    results: list[RecordingResult] = []
    current_title = ""
    try:
        try:
            source_audio = obs_control.prepare_audio(client, scene, settings, mutes, paths.obs_restore)
        except Exception as e:
            raise VrecError(f"Audio problem: {first_line(e)}") from e
        print(f"\nOBS ready: scene '{scene}', audio captured from '{source_audio}', other sounds muted.")

        meter = obs_control.AudioMeter(source_audio)
        listener = obs_control.connect_audio_listener(settings, password, meter)
        if listener is None:
            meter = None
            print("(Audio check unavailable, continuing without it.)")

        with connect_browser(settings.chrome_port) as (browser, page):
            menu.ask(
                f"\n{len(selection)} video(s) to record. Check that the Chrome window is on the "
                "virtual screen, then press Enter to start..."
            )
            print()

            if client.get_record_status().output_active:
                raise VrecError("An OBS recording started meanwhile. Stop it, then restart.")

            initial_state = None
            try:
                initial_state = window_state(browser, page, "fullscreen")
            except Exception as e:
                warn(f"Couldn't fullscreen Chrome: {first_line(e)}")

            try:
                error_streak = 0
                for i, (url, title) in enumerate(selection, 1):
                    current_title = history.display_title(videos_history, url, title)
                    had_error = False
                    try:
                        result = record_one(
                            page,
                            client,
                            meter,
                            scene,
                            settings,
                            i,
                            len(selection),
                            url,
                            title,
                            test_mode,
                            settings.max_height,
                        )
                        retry_cap = lower_quality_retry_cap(result, test_mode)
                        if retry_cap:
                            print(f"   -> {status_text(result)}")
                            print(f"   Retrying once below {result.target_height}p...\n")
                            result = record_one(
                                page,
                                client,
                                meter,
                                scene,
                                settings,
                                i,
                                len(selection),
                                url,
                                title,
                                test_mode,
                                retry_cap,
                            )
                    except Exception as e:
                        had_error = True
                        path = obs_control.stop_if_recording(client)
                        warn(f"ERROR: {first_line(e)}")
                        result = RecordingResult(
                            number=i, title=current_title, reason=f"ERROR: {first_line(e)}"
                        )
                        if path:
                            result.file = rename_recording(path, f"{INTERRUPTED_PREFIX} - {current_title}")
                            print(f"   Interrupted recording saved as {result.file.name}")

                    results.append(result)
                    if not test_mode:
                        status, detail = history_status(result)
                        history.record(
                            paths.history,
                            videos_history,
                            url,
                            result.title or current_title,
                            status,
                            detail,
                            result.file,
                            result.quality,
                        )
                    print(f"   -> {status_text(result)}\n")

                    if had_error:
                        error_streak += 1
                        chrome_alive = not page.is_closed() and browser.is_connected()
                        obs_alive = _obs_alive(client)
                        if not chrome_alive or not obs_alive:
                            what = "Chrome" if not chrome_alive else "OBS"
                            print(
                                f"Lost connection to {what}. Batch stopped; "
                                "the remaining videos are untouched."
                            )
                            break
                        if error_streak >= 3:
                            print("3 errors in a row: batch stopped.")
                            break
                    else:
                        error_streak = 0

                    page.wait_for_timeout(3000)
            finally:
                if initial_state and initial_state != "fullscreen":
                    with contextlib.suppress(Exception):
                        window_state(browser, page, initial_state)

    except KeyboardInterrupt:
        print("\nStop requested. The current video is not counted as done.")
        path = obs_control.stop_if_recording(client)
        if path and current_title:
            renamed = rename_recording(path, f"{INTERRUPTED_PREFIX} - {current_title}")
            print(f"   Interrupted recording saved as {renamed.name}")
    finally:
        obs_control.restore_mutes(client, mutes, paths.obs_restore)
        if listener:
            with contextlib.suppress(Exception):
                listener.disconnect()

    if not results:
        return 0
    if test_mode:
        _print_diagnostic(results[0])
    else:
        _print_summary(results, paths.history)
    return 0


def _restore_leftover_audio(client: obs.ReqClient, restore_file: Path) -> None:
    """If a previous run was killed mid-recording, put OBS's audio sources back as they were."""
    if not restore_file.exists():
        return
    try:
        mutes = json.loads(restore_file.read_text(encoding="utf-8"))
    except Exception:
        with contextlib.suppress(OSError):
            restore_file.unlink()
        warn("Couldn't read the leftover OBS audio settings file: ignoring it.")
        return
    obs_control.restore_mutes(client, mutes, restore_file)
    print("Restored OBS audio settings left over from an interrupted run.")


def _obs_alive(client: obs.ReqClient) -> bool:
    """Whether the OBS WebSocket connection is still responding."""
    try:
        client.get_version()
        return True
    except Exception:
        return False


def _print_diagnostic(result: RecordingResult) -> None:
    print("===== DIAGNOSTIC =====")
    if result.reason.startswith("ERROR"):
        print(result.reason)
        return
    print(f"Fullscreen : {'OK' if result.fullscreen else 'NO'}")
    w, h = result.max_resolution
    print(f"Quality    : {result.quality or 'auto (setting not found)'}, image received {w}x{h}")
    print(f"Image      : {'OK' if result.image_ok else 'BLACK (check the screen capture source)'}")
    audio_labels = {True: "OK", False: "NONE (Chrome must be routed to CABLE Input)", None: "not checked"}
    print(f"Audio      : {audio_labels[result.audio_ok]}")
    print(f"File       : {result.file}")
    print("\nOpen the file to check it, then run start.bat.")


def _print_summary(results: list[RecordingResult], history_path: Path) -> None:
    print("===== SUMMARY =====")
    for result in results:
        print(f"{result.number:2d}. {status_text(result):<40} {result.title}")
    print(f"\nHistory updated ({history_path.name}).")
