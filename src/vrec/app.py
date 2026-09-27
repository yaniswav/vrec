"""Top-level orchestration: read the playlist, drive the menu, record the videos.

The batch run is split into small steps threaded through a `Batch`, so each step can be
tested with fakes instead of a live Chrome/OBS connection: `record` and the two health
probes (`chrome_alive`, `obs_alive`) are parameters of `_record_batch` for exactly that
reason. `_prepare_window`/`_restore_window` are the seam where window placement and
virtual-display handling will hook in later; for now they just keep the fullscreen logic.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import obsws_python as obs
from playwright.sync_api import Browser, Page

from vrec import display, history, launcher, menu, obs_control
from vrec.browser import (
    WindowBounds,
    connect_browser,
    get_window_bounds,
    move_window_to,
    set_window_bounds,
    window_state,
)
from vrec.config import Paths, Settings, load_settings
from vrec.console import first_line, warn
from vrec.errors import VrecError
from vrec.features import FeatureSet, load_features
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

RecordFn = Callable[..., RecordingResult]
Video = tuple[str, str | None]


@dataclass
class Batch:
    """Everything one batch run threads through its steps."""

    paths: Paths
    settings: Settings
    features: FeatureSet
    test_mode: bool
    videos_history: history.Videos = field(default_factory=dict)
    client: obs.ReqClient | None = None
    password: str = ""
    scene: str = ""
    meter: obs_control.AudioMeter | None = None
    mutes: dict[str, bool] = field(default_factory=dict)
    listener: obs.EventClient | None = None
    browser: Browser | None = None
    page: Page | None = None
    results: list[RecordingResult] = field(default_factory=list)
    current_title: str = ""
    initial_window_state: str | None = None
    initial_bounds: WindowBounds | None = None
    display_turned_on: bool = False
    virtual_screen: display.Screen | None = None  # the screen this run turned on


def run(
    data_dir: Path,
    config_path: Path,
    test_mode: bool,
    all_videos: bool = False,
    only: str | None = None,
) -> int:
    if test_mode and all_videos:
        raise VrecError("--test can't be combined with --all. Use --test --only <number> to test one video.")

    paths = Paths(data_dir=data_dir, config=config_path)
    settings = load_settings(config_path)
    features, feature_warnings = load_features(paths.features)
    for message in feature_warnings:
        warn(message)
    disabled = features.disabled()
    if disabled:
        print(f"Features off: {', '.join(disabled)} (change with the menu or vrec --enable).")

    with InstanceLock(paths.lock):
        return _run_locked(paths, settings, features, test_mode, all_videos, only)


def _run_locked(
    paths: Paths,
    settings: Settings,
    features: FeatureSet,
    test_mode: bool,
    all_videos: bool,
    only: str | None,
) -> int:
    videos, videos_history = _load_inputs(paths)
    client, password = _connect_obs(paths, settings, features, videos, videos_history)

    selection, interactive = _choose_selection(
        videos, videos_history, paths, settings, features, test_mode, all_videos, only
    )
    if not selection:
        return 0

    batch = Batch(
        paths=paths,
        settings=settings,
        features=features,
        test_mode=test_mode,
        videos_history=videos_history,
        client=client,
        password=password,
        scene=obs_control.current_scene(client),
    )

    try:
        _prepare_audio(batch)
        _virtual_display_on(batch)
        _start_chrome_if_needed(batch)
        with connect_browser(
            settings.chrome_port,
            quality_filter=features.enabled("quality_filter"),
            audio_sink=features.enabled("audio_sink"),
        ) as (browser, page):
            batch.browser = browser
            batch.page = page
            if interactive:
                where = (
                    "Chrome will be moved to the virtual screen automatically"
                    if features.enabled("auto_place_window")
                    else "Check that the Chrome window is on the virtual screen"
                )
                menu.ask(f"\n{len(selection)} video(s) to record. {where}. Press Enter to start...")
                print()

            if client.get_record_status().output_active:
                raise VrecError("An OBS recording started meanwhile. Stop it, then restart.")

            _prepare_window(batch)
            try:
                _record_batch(batch, selection)
            finally:
                _restore_window(batch)
    except KeyboardInterrupt:
        _handle_keyboard_interrupt(batch)
    finally:
        _cleanup_audio(batch)
        _virtual_display_off(batch)

    return _final_report(batch)


# ---------- Steps ----------


def _load_inputs(paths: Paths) -> tuple[list[Video], history.Videos]:
    """Read videos.txt and history.json."""
    if not paths.videos.exists():
        raise VrecError(
            f"File not found: {paths.videos}\nCopy videos.example.txt to {paths.videos} and add your links."
        )
    videos = read_playlist(paths.videos)
    if not videos:
        raise VrecError(f"{paths.videos.name} contains no links.")
    videos_history = history.load(paths.history)
    return videos, videos_history


def _connect_obs(
    paths: Paths,
    settings: Settings,
    features: FeatureSet,
    videos: list[Video],
    videos_history: history.Videos,
) -> tuple[obs.ReqClient, str]:
    """Connect to OBS (starting it first if needed, feature auto_start_obs), restore any leftover audio
    state, and check it's idle and up to date."""
    client, password = launcher.ensure_obs(settings, paths, features)
    _restore_leftover_audio(client, paths.obs_restore)

    if client.get_record_status().output_active:
        raise VrecError("An OBS recording is already in progress. Stop it, then restart.")
    try:
        record_dir = client.get_record_directory().record_directory
    except Exception:
        record_dir = None
    history.adopt_existing_files(videos, videos_history, record_dir, paths.history)
    return client, password


def _choose_selection(
    videos: list[Video],
    videos_history: history.Videos,
    paths: Paths,
    settings: Settings,
    features: FeatureSet,
    test_mode: bool,
    all_videos: bool,
    only: str | None,
) -> tuple[list[Video], bool]:
    """Pick which videos to record.

    Returns (selection, interactive): interactive selections (the menu) still show the
    "press Enter to start" confirmation; --all/--only skip both the menu and that prompt.
    """
    if test_mode:
        print(f"\nTEST MODE: {settings.test_duration_s:.0f} s of one video, to check your settings.")

    if only is not None:
        try:
            numbers = menu.parse_numbers(only, len(videos))
        except ValueError as e:
            raise VrecError(f"'{e}' is not valid in --only. Example: 3,1,5-8") from e
        if not numbers:
            raise VrecError("--only needs at least one number. Example: 3,1,5-8")
        return [videos[n - 1] for n in numbers], False

    if all_videos:
        todo = [v for v in videos if history.status_of(videos_history, v[0]) in history.TODO]
        if not todo:
            print("Nothing to record.")
            return [], False
        return todo, False

    if test_mode:
        return menu.choose_test_video(videos, videos_history), True
    return menu.main_menu(videos, videos_history, paths.history, features, paths.features), True


def _prepare_audio(batch: Batch) -> None:
    """Route Chrome's audio through OBS and mute everything else, unless obs_audio_routing is off."""
    if not batch.features.enabled("obs_audio_routing"):
        print("\n(obs_audio_routing is off: OBS audio left as is, audio check disabled.)")
        return

    try:
        source_audio = obs_control.prepare_audio(
            batch.client, batch.scene, batch.settings, batch.mutes, batch.paths.obs_restore
        )
    except Exception as e:
        raise VrecError(f"Audio problem: {first_line(e)}") from e
    print(f"\nOBS ready: scene '{batch.scene}', audio captured from '{source_audio}', other sounds muted.")

    batch.meter = obs_control.AudioMeter(source_audio)
    batch.listener = obs_control.connect_audio_listener(batch.settings, batch.password, batch.meter)
    if batch.listener is None:
        batch.meter = None
        print("(Audio check unavailable, continuing without it.)")


def _virtual_display_on(batch: Batch) -> None:
    """Turn the virtual display on for the batch (feature manage_virtual_display, off by default)."""
    if not batch.features.enabled("manage_virtual_display"):
        return
    # Ask the device itself: another physical screen must not be mistaken for the virtual one.
    if display.virtual_display_enabled():
        return  # already on: leave it as the user had it
    before = display.list_screens()
    try:
        display.set_virtual_display(True)
    except VrecError as e:
        warn(str(e))
        return
    batch.display_turned_on = True
    screen = display.wait_for_new_screen(before)
    if screen:
        batch.virtual_screen = screen
        print(f"Virtual display turned on: {screen.describe()}.")
    else:
        warn("The virtual display didn't show up within 15 s. Continuing anyway.")


def _virtual_display_off(batch: Batch) -> None:
    """Turn the virtual display back off, only if this run turned it on."""
    if not batch.display_turned_on:
        return
    try:
        display.set_virtual_display(False)
        print("Virtual display turned off.")
    except VrecError as e:
        warn(str(e))


def _start_chrome_if_needed(batch: Batch) -> None:
    """Start the recording Chrome if its debug port doesn't answer (feature auto_start_chrome)."""
    launcher.ensure_chrome(batch.settings, batch.features)


def _place_window(batch: Batch) -> None:
    """Move Chrome onto the virtual screen (feature auto_place_window)."""
    if not batch.features.enabled("auto_place_window"):
        return
    screen = batch.virtual_screen or display.pick_screen(
        display.list_screens(), batch.settings.display_screen
    )
    if screen is None:
        warn(
            "No virtual screen found to move Chrome to: recording on the screen it is on. "
            "Check [display] screen in config.toml, or move it by hand (Win+Shift+Arrow)."
        )
        return
    # Set by `run()` before any step that touches the window is called.
    assert batch.browser is not None and batch.page is not None
    try:
        batch.initial_bounds = get_window_bounds(batch.browser, batch.page)
        if move_window_to(batch.browser, batch.page, screen):
            print(f"Chrome moved to {screen.describe()}.")
        else:
            warn(f"Couldn't move Chrome to {screen.describe()}. Move it by hand (Win+Shift+Arrow).")
    except Exception as e:
        warn(f"Couldn't move Chrome: {first_line(e)}")


def _prepare_window(batch: Batch) -> None:
    """Put the Chrome window on the virtual screen, then fullscreen it, before recording starts."""
    _place_window(batch)
    # Set by `run()` before any step that touches the window is called.
    assert batch.browser is not None and batch.page is not None
    try:
        batch.initial_window_state = window_state(batch.browser, batch.page, "fullscreen")
    except Exception as e:
        warn(f"Couldn't fullscreen Chrome: {first_line(e)}")


def _restore_window(batch: Batch) -> None:
    """Put the Chrome window back where and how it was before `_prepare_window`."""
    # Set by `run()` before any step that touches the window is called.
    assert batch.browser is not None and batch.page is not None
    if batch.initial_bounds:
        with contextlib.suppress(Exception):
            set_window_bounds(batch.browser, batch.page, batch.initial_bounds)
        return
    state = batch.initial_window_state
    if state and state != "fullscreen":
        with contextlib.suppress(Exception):
            window_state(batch.browser, batch.page, state)


def _chrome_alive(batch: Batch) -> bool:
    # Set by `run()` before any step that checks Chrome's liveness is called.
    assert batch.browser is not None and batch.page is not None
    return not batch.page.is_closed() and batch.browser.is_connected()


def _obs_alive(client: obs.ReqClient) -> bool:
    """Whether the OBS WebSocket connection is still responding."""
    try:
        client.get_version()
        return True
    except Exception:
        return False


def _connection_lost(
    batch: Batch, chrome_alive: Callable[[Batch], bool], obs_alive: Callable[[obs.ReqClient], bool]
) -> bool:
    """Whether Chrome or OBS is gone -- and if so, print why the batch is stopping."""
    alive_chrome = chrome_alive(batch)
    alive_obs = obs_alive(batch.client)
    if not alive_chrome or not alive_obs:
        what = "Chrome" if not alive_chrome else "OBS"
        print(f"Lost connection to {what}. Batch stopped; the remaining videos are untouched.")
        return True
    return False


def _record_batch(
    batch: Batch,
    selection: list[Video],
    record: RecordFn = record_one,
    chrome_alive: Callable[[Batch], bool] = _chrome_alive,
    obs_alive: Callable[[obs.ReqClient], bool] = _obs_alive,
) -> None:
    """Record every video in `selection`, in order, stopping early on a circuit-breaker trip."""
    # Set by `run()` before the batch loop starts.
    assert batch.page is not None
    error_streak = 0
    for i, (url, title) in enumerate(selection, 1):
        batch.current_title = history.display_title(batch.videos_history, url, title)
        result, had_error = _record_one_with_retry(batch, i, len(selection), url, title, record)
        batch.results.append(result)
        if not batch.test_mode:
            _record_in_history(batch, url, result)
        print(f"   -> {status_text(result)}\n")

        if had_error:
            error_streak += 1
            if batch.features.enabled("circuit_breaker"):
                if _connection_lost(batch, chrome_alive, obs_alive):
                    break
                if error_streak >= 3:
                    print("3 errors in a row: batch stopped.")
                    break
        else:
            error_streak = 0

        batch.page.wait_for_timeout(3000)


def _record_one_with_retry(
    batch: Batch, number: int, total: int, url: str, title: str | None, record: RecordFn
) -> tuple[RecordingResult, bool]:
    """Record one video, retrying once at a lower quality if it stalled (quality_retry)."""
    try:
        result = record(
            batch.page,
            batch.client,
            batch.meter,
            batch.scene,
            batch.settings,
            batch.features,
            number,
            total,
            url,
            title,
            batch.test_mode,
            batch.settings.max_height,
        )
        if batch.features.enabled("quality_retry"):
            retry_cap = lower_quality_retry_cap(result, batch.test_mode)
            if retry_cap:
                print(f"   -> {status_text(result)}")
                print(f"   Retrying once below {result.target_height}p...\n")
                result = record(
                    batch.page,
                    batch.client,
                    batch.meter,
                    batch.scene,
                    batch.settings,
                    batch.features,
                    number,
                    total,
                    url,
                    title,
                    batch.test_mode,
                    retry_cap,
                )
        return result, False
    except Exception as e:
        return _handle_failed_video(batch, number, e), True


def _handle_failed_video(batch: Batch, number: int, error: Exception) -> RecordingResult:
    """Stop OBS if it was recording, rename the leftover file, and build the failure result."""
    path = obs_control.stop_if_recording(batch.client)
    warn(f"ERROR: {first_line(error)}")
    result = RecordingResult(number=number, title=batch.current_title, reason=f"ERROR: {first_line(error)}")
    if path:
        result.file = rename_recording(path, f"{INTERRUPTED_PREFIX} - {batch.current_title}")
        print(f"   Interrupted recording saved as {result.file.name}")
    return result


def _record_in_history(batch: Batch, url: str, result: RecordingResult) -> None:
    status, detail = history_status(result)
    history.record(
        batch.paths.history,
        batch.videos_history,
        url,
        result.title or batch.current_title,
        status,
        detail,
        result.file,
        result.quality,
    )


def _handle_keyboard_interrupt(batch: Batch) -> None:
    print("\nStop requested. The current video is not counted as done.")
    path = obs_control.stop_if_recording(batch.client)
    if path and batch.current_title:
        renamed = rename_recording(path, f"{INTERRUPTED_PREFIX} - {batch.current_title}")
        print(f"   Interrupted recording saved as {renamed.name}")


def _cleanup_audio(batch: Batch) -> None:
    if batch.features.enabled("obs_audio_routing"):
        obs_control.restore_mutes(batch.client, batch.mutes, batch.paths.obs_restore)
    if batch.listener:
        with contextlib.suppress(Exception):
            batch.listener.disconnect()


def _final_report(batch: Batch) -> int:
    if not batch.results:
        return 0
    if batch.test_mode:
        _print_diagnostic(batch.results[0])
    else:
        _print_summary(batch.results, batch.paths.history)
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
