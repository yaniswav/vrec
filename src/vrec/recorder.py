"""Recording a single video: fullscreen, quality selection, OBS start/stop, live checks."""

from __future__ import annotations

import contextlib
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import obsws_python as obs
from playwright.sync_api import Page

from vrec import history, obs_scene
from vrec.browser import document_script, load_js, route_audio_to
from vrec.config import Settings
from vrec.console import ProgressLine, human_duration, warn
from vrec.errors import VrecError
from vrec.features import FeatureSet
from vrec.monitor import Output, PlayerState, StopReason, WatchConfig, watch
from vrec.naming import (
    FAILED_BLACK_PREFIX,
    FAILED_FROZEN_PREFIX,
    INCOMPLETE_PREFIX,
    TEST_PREFIX,
    clean_title,
    rename_recording,
)
from vrec.obs_control import AudioMeter, frame_signature, is_black_frame

__all__ = [
    "INCOMPLETE_REASONS",
    "RecordingResult",
    "StopReason",
    "history_status",
    "lower_quality_retry_cap",
    "record_one",
    "status_text",
]

JS_PLAY = "() => { window.__vrecVideo.play().catch(() => {}); }"
JS_PAUSE = "() => { window.__vrecVideo.pause(); }"
_JS_GET_FORCED_QUALITY = "() => window.__vrecForcedQuality || null"

# Reasons that mean the recording is missing part of the video, not just unverified:
# never fully recorded, so it should be retried rather than merely reviewed.
INCOMPLETE_REASONS = frozenset(
    {StopReason.STALLED, StopReason.VIDEO_GONE, StopReason.TOO_LONG, StopReason.OBS_LOST}
)


@dataclass
class RecordingResult:
    number: int
    title: str = ""
    reason: str = ""  # a StopReason value, or "ERROR: ..."
    fullscreen: bool = False
    image_ok: bool | None = False  # None: not checked (black_check off)
    audio_ok: bool | None = None  # None: not checked
    file: Path | None = None
    quality: str = ""
    target_height: int = 0  # height vrec asked the player for (0 = unknown)
    max_resolution: tuple[int, int] = field(default=(0, 0))
    buffering_pauses: int = 0
    duration_s: float = 0.0  # length of the video (0 = unknown)


class _PagePlayer:
    """The video element picked by pick_video.js, driven through Playwright."""

    def __init__(self, page: Page) -> None:
        self._page = page

    def state(self) -> PlayerState | None:
        # page.evaluate() returns Any (it's arbitrary JS); state.js's shape matches PlayerState.
        return cast("PlayerState | None", self._page.evaluate(load_js("state.js")))

    def play(self) -> None:
        self._page.evaluate(JS_PLAY)

    def pause(self) -> None:
        self._page.evaluate(JS_PAUSE)

    def wait(self, seconds: float) -> None:
        self._page.wait_for_timeout(int(seconds * 1000))


class _ObsCapture:
    """OBS as seen by the playback loop."""

    def __init__(
        self,
        client: obs.ReqClient,
        scene: str,
        settings: Settings,
        meter: AudioMeter | None,
        follow: Callable[[], object] | None = None,
    ) -> None:
        self._client, self._scene, self._settings, self._meter = client, scene, settings, meter
        # Called with each liveness check: keeps a window capture on the Chrome window when the
        # page changes its title during playback.
        self._follow = follow

    def pause(self) -> None:
        self._client.pause_record()

    def resume(self) -> None:
        self._client.resume_record()

    def is_black(self) -> bool | None:
        return is_black_frame(self._client, self._scene, self._settings)

    def frame(self) -> bytes | None:
        return frame_signature(self._client, self._scene)

    def audio_peak(self) -> float | None:
        return self._meter.peak if self._meter else None

    def recording(self) -> bool | None:
        if self._follow is not None:
            with contextlib.suppress(Exception):
                self._follow()
        try:
            return bool(self._client.get_record_status().output_active)
        except Exception:
            return None


def record_one(
    page: Page,
    client: obs.ReqClient,
    meter: AudioMeter | None,
    scene: str,
    settings: Settings,
    features: FeatureSet,
    number: int,
    total: int,
    url: str,
    title: str | None,
    test_mode: bool,
    max_height: int = 0,
    window_capture: str | None = None,
) -> RecordingResult:
    result = RecordingResult(number=number)
    print(f"[{number}/{total}] {title or url}")
    force_quality = features.enabled("quality_filter")

    # The quality filter reads this cap when the player fetches its list of qualities.
    with document_script(page, f"window.__vrecMaxHeight = {int(max_height)};"):
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    duration = page.evaluate(load_js("pick_video.js"))
    if isinstance(duration, int | float) and math.isfinite(duration) and duration > 0:
        result.duration_s = float(duration)
    result.title = title or clean_title(page.title())
    print(f"   {result.title if not title else url}  -  duration {human_duration(duration)}")

    result.fullscreen = page.evaluate(load_js("fill_window.js"))
    if not result.fullscreen:
        warn("The video doesn't fill the whole screen, recording continues anyway")

    if features.enabled("audio_sink"):
        routed, detail = route_audio_to(page, settings.audio_output)
        if routed:
            print(f"   Audio: this video only -> {detail}")
        else:
            warn(
                f"Couldn't send the video's sound to {settings.audio_output} ({detail}): "
                "using the Windows audio setup."
            )

    forced = page.evaluate(_JS_GET_FORCED_QUALITY) if force_quality else None
    if not force_quality:
        print("   Quality: left to the site (quality_filter is off)")
    elif forced:
        result.target_height = int(re.sub(r"\D", "", forced.split("x")[-1]) or 0)
        result.quality = forced
        print(f"   Quality: {forced} forced from the start")
    else:
        quality_info = page.evaluate(load_js("max_quality.js"), max_height)
        result.target_height = int(quality_info["target"] or 0)
        if quality_info["method"]:
            result.quality = f"{result.target_height}p" if result.target_height else "max"
            print(f"   Quality: {result.quality} (via {quality_info['method']})")
        else:
            warn("Couldn't find a quality selector: the site decides on its own (auto)")

    # Rewinding makes the player reload the video, now at the chosen quality.
    page.wait_for_timeout(int(settings.fullscreen_settle_s * 1000))
    page.evaluate(load_js("rewind.js"))
    page.evaluate(load_js("wait_can_play.js"))
    page.wait_for_timeout(1000)
    width, height, streaming = page.evaluate(load_js("resolution.js"))
    print(f"   Received image: {width}x{height}")
    if force_quality and streaming and not forced:
        warn("Unrecognized streaming format: couldn't force the max quality in advance")
        if test_mode:
            print("   Player requests (include these in a bug report):")
            for address in page.evaluate(load_js("player_requests.js")):
                print(f"     {address}")

    follow = None
    if window_capture:
        # Window capture: point it at this page's Chrome window (its title changes with every page).
        page_title = page.title()
        if not obs_scene.target_window(client, window_capture, page_title):
            warn(f"OBS can't find the Chrome window '{page_title}': the recording may be black.")

        def follow() -> None:
            obs_scene.target_window(client, window_capture, page.title())

    client.start_record()
    page.wait_for_timeout(int(settings.lead_in_s * 1000))
    if meter:
        meter.reset()
    page.evaluate(JS_PLAY)

    config = WatchConfig(
        duration=duration if isinstance(duration, int | float) else math.nan,
        test_mode=test_mode,
        test_duration_s=settings.test_duration_s,
        pause_below_s=settings.pause_below_s,
        resume_at_s=settings.resume_at_s,
        max_stall_s=settings.max_stall_s,
        abort_if_black_after_s=settings.abort_if_black_after_s,
        abort_if_frozen_after_s=settings.abort_if_frozen_after_s,
        audio_level=settings.audio_level,
        max_wall_factor=settings.max_wall_factor,
        max_wall_extra_s=settings.max_wall_extra_s,
        target_height=result.target_height,
        buffer_pause=features.enabled("buffer_pause"),
        black_check=features.enabled("black_check"),
        frozen_check=features.enabled("frozen_check"),
        audio_check=features.enabled("audio_check"),
        wall_clock_cap=features.enabled("wall_clock_cap"),
    )
    line = ProgressLine()
    outcome = watch(
        _PagePlayer(page),
        _ObsCapture(client, scene, settings, meter, follow),
        config,
        Output(warn=warn, progress=line.show, end_progress=line.end),
    )
    result.reason = outcome.reason.value
    result.image_ok = outcome.image_ok
    result.max_resolution = outcome.max_resolution
    result.buffering_pauses = outcome.buffering_pauses

    if result.buffering_pauses:
        print(f"   {result.buffering_pauses} buffering pause(s), excluded from the recording")
    if outcome.reason not in (StopReason.BLACK, StopReason.FROZEN, StopReason.STALLED, StopReason.OBS_LOST):
        page.wait_for_timeout(int(settings.tail_s * 1000))
    try:
        path = client.stop_record().output_path
    except Exception as e:
        if outcome.reason != StopReason.OBS_LOST:
            raise
        raise VrecError("OBS stopped recording during the video. Check the OBS recordings folder.") from e
    if meter and config.audio_check:
        result.audio_ok = meter.peak >= settings.audio_level

    if test_mode:
        stem = f"{TEST_PREFIX} - {result.title}"
    elif outcome.reason == StopReason.BLACK:
        stem = f"{FAILED_BLACK_PREFIX} - {result.title}"
    elif outcome.reason == StopReason.FROZEN:
        stem = f"{FAILED_FROZEN_PREFIX} - {result.title}"
    elif outcome.reason in INCOMPLETE_REASONS:
        stem = f"{INCOMPLETE_PREFIX} - {result.title}"
    else:
        stem = result.title
    result.file = rename_recording(path, stem)
    if result.file == Path(path):
        warn(f"Couldn't rename the recording, it keeps its OBS name: {result.file.name}")

    return result


def lower_quality_retry_cap(result: RecordingResult, test_mode: bool) -> int:
    """Height cap for one retry after a stalled load (0 = don't retry).

    The cap sits just below the height that stalled, so the next lower quality is picked.
    """
    if test_mode or result.reason != StopReason.STALLED or result.target_height <= 0:
        return 0
    return result.target_height - 1


def status_text(result: RecordingResult) -> str:
    """One-line summary of a recording result: OK, CHECK: ..., FAILED: ..., or ERROR: ..."""
    if result.reason.startswith("ERROR"):
        return result.reason
    if result.reason == StopReason.BLACK.value:
        return "FAILED: black image (protected video?)"
    if result.reason == StopReason.FROZEN.value:
        return "FAILED: frozen image (OBS isn't filming the video?)"
    if result.reason in INCOMPLETE_REASONS:
        return f"FAILED: incomplete ({result.reason})"
    problems = []
    if result.image_ok is False:
        problems.append("black image?")
    if result.audio_ok is False:
        problems.append("no audio")
    if result.reason not in (StopReason.ENDED.value, StopReason.TEST_DONE.value):
        problems.append(result.reason)
    return "CHECK: " + ", ".join(problems) if problems else "OK"


def history_status(result: RecordingResult) -> tuple[str, str]:
    """Map a recording result to a (history status, detail) pair."""
    text = status_text(result)
    if text == "OK":
        return history.STATUS_DONE, ""
    if text.startswith("CHECK"):
        return history.STATUS_REVIEW, text.split(":", 1)[1].strip()
    return history.STATUS_FAILED, text
