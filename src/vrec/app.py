"""Top-level orchestration: read the playlist, drive the menu, record the videos.

The batch run is split into small steps threaded through a `Batch`, so each step can be
tested with fakes instead of a live Chrome/OBS connection: `record` and the two health
probes (`chrome_alive`, `obs_alive`) are parameters of `_record_batch` for exactly that
reason. `_prepare_window` moves Chrome to the virtual screen and fullscreens it before the
pre-flight check and the recording; `_restore_window` puts the window back afterwards.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import obsws_python as obs
from playwright.sync_api import Browser, Page

from vrec import display, history, launcher, menu, notify, obs_control, obs_scene, power, preflight
from vrec.browser import (
    WindowBounds,
    connect_browser,
    get_window_bounds,
    move_window_to,
    pick_page,
    set_window_bounds,
    window_state,
)
from vrec.config import Paths, Settings, load_settings
from vrec.console import first_line, human_duration, warn
from vrec.errors import VrecError
from vrec.features import FeatureSet, load_features
from vrec.lock import InstanceLock
from vrec.naming import INTERRUPTED_PREFIX, clean_title, rename_recording
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
    current_url: str = ""  # the video being recorded right now ("" between videos)
    stopped_early: bool = False  # the batch ended before the end of its selection
    interrupted: bool = False  # Ctrl+C handled inside the batch
    initial_window_state: str | None = None
    initial_bounds: WindowBounds | None = None
    display_turned_on: bool = False
    virtual_screen: display.Screen | None = None  # the screen this run turned on
    capture: str = ""  # vrec's display capture source (obs_scene feature)
    previous_scene: str | None = None  # program scene to switch back to
    hidden_scene: str = ""  # scene whose other captures vrec hid
    hidden_sources: list[tuple[str, int]] = field(default_factory=list)  # (source name, item id) hidden
    capture_mode: str = "screen"  # what `capture` is: "screen" or "window"
    interactive: bool = False
    kept_awake: bool = False  # this run asked Windows not to sleep
    started_at: float = field(default_factory=time.time)


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
        # Screen and Chrome first: they are ready (and visible) by the time the menu shows up.
        _virtual_display_on(batch)
        _start_chrome_if_needed(batch)
        _place_chrome_early(batch)

        selection, batch.interactive = _choose_selection(
            videos, videos_history, paths, settings, features, test_mode, all_videos, only
        )
        if not selection:
            return 0

        if features.enabled("keep_awake"):
            batch.kept_awake = power.stay_awake()
        _prepare_scene(batch)
        _prepare_audio(batch)
        with connect_browser(
            settings.chrome_port,
            quality_filter=features.enabled("quality_filter"),
            audio_sink=features.enabled("audio_sink"),
        ) as (browser, page):
            batch.browser = browser
            batch.page = page
            if batch.interactive:
                where = (
                    "Log in to the site in the Chrome window if needed"
                    if features.enabled("auto_place_window")
                    else "Check that the Chrome window is on the virtual screen"
                )
                menu.ask(f"\n{len(selection)} video(s) to record. {where}, then press Enter to start...")
                print()

            if client.get_record_status().output_active:
                raise VrecError("An OBS recording started meanwhile. Stop it, then restart.")

            try:
                _ensure_page(batch)
                _prepare_window(batch)
                _preflight(batch)
                _record_batch(batch, selection)
            finally:
                _restore_window(batch)
    except KeyboardInterrupt:
        _handle_keyboard_interrupt(batch)
    finally:
        _cleanup_audio(batch)
        _restore_scene(batch)
        _virtual_display_off(batch)
        if batch.kept_awake:
            power.allow_sleep()

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
    _restore_leftover_sources(client, paths.obs_restore_sources)
    _restore_leftover_scene(client, paths.obs_scene_restore)

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

    if batch.capture:  # vrec's own scene: its audio source must be in it to be recorded
        obs_scene.ensure_in_scene(batch.client, batch.scene, source_audio)

    batch.meter = obs_control.AudioMeter(source_audio)
    batch.listener = obs_control.connect_audio_listener(batch.settings, batch.password, batch.meter)
    if batch.listener is None:
        batch.meter = None
        print("(Audio check unavailable, continuing without it.)")


def _prepare_scene(batch: Batch) -> None:
    """Record from vrec's own OBS scene (feature obs_scene): create what's missing, switch to it."""
    if not batch.features.enabled("obs_scene"):
        return
    assert batch.client is not None
    name = batch.settings.obs_scene_name
    mode = batch.settings.obs_capture
    label = "window capture" if mode == "window" else "display capture"
    setup = obs_scene.ensure_scene(batch.client, name, mode)
    if setup.created_scene:
        print(f"Created the OBS scene '{name}' with a {label} '{setup.capture}'.")
    elif setup.created_capture:
        print(f"Added a {label} '{setup.capture}' to the OBS scene '{name}'.")
    elif setup.shown_capture:
        print(f"Showed '{setup.capture}' in the OBS scene '{name}' (it was hidden).")
    if setup.also_items:
        # Marker first, so a killed run still shows them again on the next start.
        batch.hidden_scene, batch.hidden_sources = name, list(setup.also_items)
        batch.paths.obs_restore_sources.write_text(
            json.dumps({"scene": name, "items": [{"source": n, "id": i} for n, i in setup.also_items]}),
            encoding="utf-8",
        )
        for _, item_id in setup.also_items:
            batch.client.set_scene_item_enabled(name, item_id, False)
        others = ", ".join(f"'{source}'" for source in setup.also_visible)
        print(f"Hid {others} in the OBS scene '{name}' while recording (shown again afterwards).")
    # Written before switching, so a killed run still gets its scene back on the next start.
    batch.paths.obs_scene_restore.write_text(obs_control.current_scene(batch.client), encoding="utf-8")
    batch.previous_scene = obs_scene.switch_to(batch.client, name)
    batch.scene, batch.capture, batch.capture_mode = name, setup.capture, mode


def _restore_scene(batch: Batch) -> None:
    """Show the hidden captures again, then switch OBS back to the scene the user was on."""
    if batch.client is None:
        return
    if batch.hidden_sources:
        try:
            obs_scene.set_items_enabled(batch.client, batch.hidden_scene, batch.hidden_sources, True)
            batch.paths.obs_restore_sources.unlink(missing_ok=True)
        except Exception as e:
            warn(
                f"Couldn't show the sources hidden in the OBS scene '{batch.hidden_scene}' again: "
                f"{first_line(e)}"
            )
    if batch.previous_scene is None:
        return
    try:
        if batch.previous_scene != batch.scene:
            batch.client.set_current_program_scene(batch.previous_scene)
        batch.paths.obs_scene_restore.unlink(missing_ok=True)
    except Exception as e:
        warn(f"Couldn't switch OBS back to the scene '{batch.previous_scene}': {first_line(e)}")


def _preflight(batch: Batch) -> None:
    """Check the whole chain before recording (feature preflight_check); stop if something's wrong."""
    if not batch.features.enabled("preflight_check"):
        return
    assert batch.browser is not None and batch.page is not None and batch.client is not None
    screen = batch.virtual_screen or display.pick_screen(
        display.list_screens(), batch.settings.display_screen
    )
    ctx = preflight.Context(
        page=batch.page,
        browser=batch.browser,
        client=batch.client,
        capture=batch.capture or batch.scene,
        scene=batch.scene,
        can_retarget=bool(batch.capture) and batch.capture_mode == "screen",
        capture_mode=batch.capture_mode if batch.capture else "screen",
        screen=screen,
        meter=batch.meter,
        audio_output=batch.settings.audio_output if batch.features.enabled("audio_sink") else "",
        audio_level=batch.settings.audio_level,
    )
    print("Checking everything before recording...")
    results = preflight.run_checks(ctx)
    for result in results:
        print(result.line())
    print()
    if all(r.ok is not False for r in results):
        return
    if batch.interactive and menu.ask("Something isn't right. Record anyway? (y/N) ").lower().startswith("y"):
        return
    raise VrecError(
        "Pre-flight check failed: nothing was recorded. Fix the points above, "
        "or turn the check off: vrec --disable preflight_check"
    )


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
        batch.initial_bounds = bounds = get_window_bounds(batch.browser, batch.page)
        if screen.contains(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2):
            return  # already there (placed before the menu, or by hand)
        if move_window_to(batch.browser, batch.page, screen):
            print(f"Chrome moved to {screen.describe()}.")
        else:
            warn(f"Couldn't move Chrome to {screen.describe()}. Move it by hand (Win+Shift+Arrow).")
    except Exception as e:
        warn(f"Couldn't move Chrome: {first_line(e)}")


def _place_chrome_early(batch: Batch) -> None:
    """Put Chrome on the virtual screen right away (maximized), before the menu.

    A short connection of its own: the recording connection (with its page scripts) comes later.
    """
    if not batch.features.enabled("auto_place_window"):
        return
    try:
        with connect_browser(batch.settings.chrome_port, quality_filter=False, audio_sink=False) as (
            browser,
            page,
        ):
            batch.browser, batch.page = browser, page
            _place_window(batch)
    except VrecError:
        pass  # Chrome isn't reachable: the recording step reports it with the right advice
    finally:
        batch.browser = batch.page = None
        batch.initial_bounds = None


def launch_chrome(data_dir: Path, config_path: Path) -> int:
    """`vrec --launch-chrome`: start the recording Chrome and put it on the virtual screen, maximized."""
    paths = Paths(data_dir=data_dir, config=config_path)
    settings = load_settings(config_path)
    features, _warnings = load_features(paths.features)
    forced = FeatureSet({**features.overrides(), "auto_start_chrome": True})
    launcher.ensure_chrome(settings, forced)
    batch = Batch(paths=paths, settings=settings, features=forced, test_mode=False)
    _place_chrome_early(batch)
    print("Chrome is ready. Log in to the site in that window if needed, then run start.bat or test.bat.")
    return 0


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


def _ensure_page(batch: Batch, redo_window: bool = False) -> None:
    """Switch to another Chrome tab if the one vrec was using got closed (browser still connected)."""
    # Set by `run()` before any step that touches the page is called.
    assert batch.browser is not None and batch.page is not None
    if not batch.browser.is_connected() or not batch.page.is_closed():
        return
    batch.page = pick_page(batch.browser, latest=True)
    print("The Chrome tab vrec was using was closed: using another one.")
    if redo_window:
        # _prepare_window overwrites the saved window state with the already prepared one: keep the originals.
        bounds, state = batch.initial_bounds, batch.initial_window_state
        _prepare_window(batch)
        batch.initial_bounds = bounds or batch.initial_bounds
        batch.initial_window_state = state or batch.initial_window_state


def _chrome_alive(batch: Batch) -> bool:
    # Set by `run()` before any step that checks Chrome's liveness is called.
    assert batch.browser is not None
    return batch.browser.is_connected()


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
    """Whether Chrome or OBS is gone. If so, print why the batch is stopping."""
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
    free_bytes: Callable[[str], int] = lambda folder: shutil.disk_usage(folder).free,
) -> None:
    """Record every video in `selection`, in order, stopping early on a circuit-breaker trip."""
    # Set by `run()` before the batch loop starts.
    assert batch.page is not None
    error_streak = 0
    for i, (url, title) in enumerate(selection, 1):
        _ensure_page(batch, redo_window=True)
        batch.current_title = history.display_title(batch.videos_history, url, title)
        if not _enough_disk_space(batch, free_bytes):
            batch.stopped_early = True
            break
        batch.current_url = url
        result, had_error = _record_one_with_retry(batch, i, len(selection), url, title, record)
        batch.results.append(result)
        if not batch.test_mode:
            _record_in_history(batch, url, result)
        batch.current_url = ""
        print(f"   -> {status_text(result)}\n")

        if had_error:
            error_streak += 1
            if batch.features.enabled("circuit_breaker"):
                if _connection_lost(batch, chrome_alive, obs_alive):
                    batch.stopped_early = True
                    break
                if error_streak >= 3:
                    print("3 errors in a row: batch stopped.")
                    batch.stopped_early = True
                    break
        else:
            error_streak = 0

        if not batch.page.is_closed():
            batch.page.wait_for_timeout(3000)


def _enough_disk_space(batch: Batch, free_bytes: Callable[[str], int]) -> bool:
    """Whether the OBS recording folder still has room for a video (feature disk_space_guard).

    Anything unexpected (folder unknown, OBS busy) lets the video go ahead: the guard must never be
    the reason a batch doesn't record.
    """
    if not batch.features.enabled("disk_space_guard") or batch.client is None:
        return True
    try:
        folder = batch.client.get_record_directory().record_directory
        free = free_bytes(folder)
    except Exception:
        return True
    minimum = batch.settings.min_free_gb * 1e9
    if free >= minimum:
        return True
    print(
        f"Only {free / 1e9:.1f} GB free in {folder}: batch stopped before '{batch.current_title}'. "
        "Free some space, or lower [recording] min_free_gb in config.toml."
    )
    return False


def _record_one_with_retry(
    batch: Batch, number: int, total: int, url: str, title: str | None, record: RecordFn
) -> tuple[RecordingResult, bool]:
    """Record one video, retrying once at a lower quality if it stalled (quality_retry)."""
    window: dict[str, str] = {}
    if batch.capture and batch.capture_mode == "window":
        window["window_capture"] = batch.capture
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
            **window,
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
                    **window,
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
        result.file = rename_recording(path, f"{INTERRUPTED_PREFIX} - {clean_title(batch.current_title)}")
        print(f"   Interrupted recording saved as {result.file.name}")
    return result


def _save_history(batch: Batch, url: str, title: str, status: str, detail: str, **extra: Any) -> None:
    """history.record, but a history.json that can't be written never aborts the batch."""
    try:
        history.record(batch.paths.history, batch.videos_history, url, title, status, detail, **extra)
    except OSError as e:
        warn(
            f"Couldn't save {batch.paths.history.name} ({first_line(e)}): "
            "this video's status is kept in memory only."
        )


def _record_in_history(batch: Batch, url: str, result: RecordingResult) -> None:
    status, detail = history_status(result)
    _save_history(
        batch,
        url,
        result.title or batch.current_title,
        status,
        detail,
        file=result.file,
        quality=result.quality,
    )


def _handle_keyboard_interrupt(batch: Batch) -> None:
    batch.interrupted = True
    print("\nStop requested. The video in progress is marked as failed.")
    path = obs_control.stop_if_recording(batch.client)
    file: Path | None = None
    if path and batch.current_title:
        file = rename_recording(path, f"{INTERRUPTED_PREFIX} - {clean_title(batch.current_title)}")
        print(f"   Interrupted recording saved as {file.name}")
    if batch.current_url and not batch.test_mode:
        _save_history(
            batch,
            batch.current_url,
            batch.current_title,
            history.STATUS_FAILED,
            "interrupted",
            file=file,
        )
    batch.current_url = ""


def _cleanup_audio(batch: Batch) -> None:
    if batch.features.enabled("obs_audio_routing"):
        obs_control.restore_mutes(batch.client, batch.mutes, batch.paths.obs_restore)
    if batch.listener:
        with contextlib.suppress(Exception):
            batch.listener.disconnect()


def _final_report(batch: Batch, send: Callable[[str, str], bool] = notify.notify) -> int:
    code = exit_code(batch)
    if not batch.results:
        return code
    elapsed = time.time() - batch.started_at
    if batch.test_mode:
        _print_diagnostic(batch.results[0])
        title, text = "vrec: test finished", status_text(batch.results[0])
    else:
        _print_summary(batch.results, batch.paths.history, elapsed)
        title, text = "vrec: batch finished", summary_line(batch.results, elapsed)
    if batch.features.enabled("notify_when_done"):
        send(title, text)
    return code


def exit_code(batch: Batch) -> int:
    """0: all OK (or nothing to do on purpose); 1: a video not OK or the batch stopped early; 130: Ctrl+C."""
    if batch.interrupted:
        return 130
    if batch.test_mode and batch.results:
        return 0 if status_text(batch.results[0]) == "OK" else 1
    if batch.stopped_early or any(status_text(r) != "OK" for r in batch.results):
        return 1
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


def _restore_leftover_scene(client: obs.ReqClient, restore_file: Path) -> None:
    """If a previous run was killed while on vrec's scene, switch OBS back to the user's scene."""
    if not restore_file.exists():
        return
    scene = restore_file.read_text(encoding="utf-8").strip()
    try:
        if scene and scene in obs_scene.scene_names(client):
            client.set_current_program_scene(scene)
            print(f"Switched OBS back to the scene '{scene}' left over from an interrupted run.")
        elif scene:
            warn(f"The scene '{scene}' to switch back to after an interrupted run no longer exists.")
        restore_file.unlink(missing_ok=True)
    except Exception as e:
        warn(f"Couldn't switch OBS back to the scene '{scene}': {first_line(e)}")


def _restore_leftover_sources(client: obs.ReqClient, restore_file: Path) -> None:
    """If a previous run was killed while the captures of vrec's scene were hidden, show them again."""
    if not restore_file.exists():
        return
    try:
        data = json.loads(restore_file.read_text(encoding="utf-8"))
        scene = str(data["scene"])
        items = [(str(i["source"]), int(i["id"])) for i in data["items"]]
    except Exception:
        with contextlib.suppress(OSError):
            restore_file.unlink()
        warn("Couldn't read the leftover OBS hidden sources file: ignoring it.")
        return
    try:
        if scene in obs_scene.scene_names(client):
            done = obs_scene.set_items_enabled(client, scene, items, True)
            if done:
                shown = ", ".join(f"'{n}'" for n in done)
                print(f"Showed again {shown} in the OBS scene '{scene}', hidden by an interrupted run.")
        restore_file.unlink(missing_ok=True)
    except Exception as e:
        warn(f"Couldn't show the sources hidden in the OBS scene '{scene}' again: {first_line(e)}")


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


def _print_summary(results: list[RecordingResult], history_path: Path, elapsed_s: float = 0.0) -> None:
    print("===== SUMMARY =====")
    for result in results:
        print(f"{result.number:2d}. {status_text(result):<40} {result.title}")
    print()
    print(summary_line(results, elapsed_s))
    print(f"History updated ({history_path.name}).")


def summary_line(results: list[RecordingResult], elapsed_s: float) -> str:
    """One line of totals: how many OK, how much video, how big, how long it took."""
    ok = sum(1 for r in results if status_text(r) == "OK")
    recorded = sum(r.duration_s for r in results if status_text(r) == "OK")
    size = 0
    for r in results:
        if r.file:
            with contextlib.suppress(OSError):
                size += r.file.stat().st_size
    parts = [f"{ok}/{len(results)} OK"]
    if recorded:
        parts.append(f"{human_duration(recorded)} of video")
    if size:
        parts.append(f"{size / 1e9:.1f} GB")
    if elapsed_s:
        parts.append(f"done in {human_duration(elapsed_s)}")
    return " - ".join(parts)
