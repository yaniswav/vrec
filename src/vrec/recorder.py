"""Recording a single video: fullscreen, quality selection, OBS start/stop, live checks."""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import obsws_python as obs
from playwright.sync_api import Page

from vrec import history
from vrec.browser import load_js
from vrec.config import Settings
from vrec.console import human_duration, warn
from vrec.naming import FAILED_BLACK_PREFIX, INCOMPLETE_PREFIX, TEST_PREFIX, clean_title, rename_recording
from vrec.obs_control import AudioMeter, is_black_frame

JS_PLAY = "() => { window.__vrecVideo.play().catch(() => {}); }"
JS_PAUSE = "() => { window.__vrecVideo.pause(); }"
_JS_GET_FORCED_QUALITY = "() => window.__vrecForcedQuality || null"


class StopReason(StrEnum):
    ENDED = "ended"
    TEST_DONE = "test finished"
    TIME_LIMIT = "time limit reached"
    VIDEO_GONE = "video removed from page"
    STALLED = "loading stalled"
    BLACK = "black image"


# Reasons that mean the recording is missing part of the video, not just unverified:
# never fully recorded, so it should be retried rather than merely reviewed.
INCOMPLETE_REASONS = frozenset({StopReason.STALLED, StopReason.VIDEO_GONE})


@dataclass
class RecordingResult:
    number: int
    title: str = ""
    reason: str = ""  # a StopReason value, or "ERROR: ..."
    fullscreen: bool = False
    image_ok: bool = False
    audio_ok: bool | None = None
    file: Path | None = None
    quality: str = ""
    max_resolution: tuple[int, int] = field(default=(0, 0))
    buffering_pauses: int = 0


def record_one(
    page: Page,
    client: obs.ReqClient,
    meter: AudioMeter | None,
    scene: str,
    settings: Settings,
    number: int,
    total: int,
    url: str,
    title: str | None,
    test_mode: bool,
) -> RecordingResult:
    result = RecordingResult(number=number)
    print(f"[{number}/{total}] {title or url}")

    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    duration = page.evaluate(load_js("pick_video.js"))
    result.title = title or clean_title(page.title())
    print(f"   {result.title if not title else url}  -  duration {human_duration(duration)}")

    result.fullscreen = page.evaluate(load_js("fill_window.js"))
    if not result.fullscreen:
        warn("The video doesn't fill the whole screen, recording continues anyway")

    forced = page.evaluate(_JS_GET_FORCED_QUALITY)
    if forced:
        target_height = int(re.sub(r"\D", "", forced.split("x")[-1]) or 0)
        quality_info = {"method": "stream quality list", "target": target_height}
        result.quality = forced
        print(f"   Quality: {forced} forced from the start")
    else:
        quality_info = page.evaluate(load_js("max_quality.js"))
        if quality_info["method"]:
            result.quality = f"{quality_info['target']}p" if quality_info["target"] else "max"
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
    if streaming and not forced:
        warn("Unrecognized streaming format: couldn't force the max quality in advance")
        if test_mode:
            print("   Player requests (include these in a bug report):")
            for address in page.evaluate(load_js("player_requests.js")):
                print(f"     {address}")

    client.start_record()
    page.wait_for_timeout(int(settings.lead_in_s * 1000))
    if meter:
        meter.reset()
    page.evaluate(JS_PLAY)

    start = time.time()
    if test_mode:
        limit = settings.test_duration_s
    elif duration and math.isfinite(duration):
        limit = duration + 120
    else:
        limit = 4 * 3600
    last_t, last_progress = -1.0, time.time()
    audio_warned = stall_warned = quality_warned = False
    resumes = 0
    next_black_check = start + 3
    buffering, buffering_start, paused_time, pause_allowed = False, 0.0, 0.0, True

    while True:
        page.wait_for_timeout(500)
        now = time.time()
        elapsed = now - start - paused_time - (now - buffering_start if buffering else 0)
        state = page.evaluate(load_js("state.js"))
        if state is None:
            result.reason = StopReason.VIDEO_GONE.value
            break
        if state["ended"]:
            result.reason = StopReason.ENDED.value
            break
        if elapsed > limit:
            result.reason = StopReason.TEST_DONE.value if test_mode else StopReason.TIME_LIMIT.value
            break

        video_duration = state["d"]
        remaining = (
            video_duration - state["t"] if video_duration and math.isfinite(video_duration) else float("inf")
        )
        enough_buffered = state["buffer"] >= min(settings.resume_at_s, remaining - 0.5)

        # Buffering: both the video and the recording are paused.
        if buffering:
            waited = now - buffering_start
            print(
                f"\r   Buffering... {state['buffer']:4.1f} s in reserve (recording paused)   ",
                end="",
                flush=True,
            )
            if enough_buffered:
                client.resume_record()
                page.evaluate(JS_PLAY)
                paused_time += waited
                buffering = False
            elif waited > settings.max_stall_s:
                result.reason = StopReason.STALLED.value
                break
            continue

        # Buffer almost empty: pause everything BEFORE the image freezes.
        if (
            pause_allowed
            and state["buffer"] < settings.pause_below_s
            and remaining > settings.pause_below_s + 1
        ):
            page.evaluate(JS_PAUSE)
            try:
                client.pause_record()
                buffering, buffering_start = True, now
                result.buffering_pauses += 1
                print()
            except Exception:
                pause_allowed = False
                page.evaluate(JS_PLAY)
                warn("OBS refuses to pause: buffering will be recorded (frozen image).")
            continue

        if video_duration and math.isfinite(video_duration):
            print(
                f"\r   Playing {state['t'] / video_duration:6.1%}  "
                f"({human_duration(state['t'])} / {human_duration(video_duration)})"
                "                              ",
                end="",
                flush=True,
            )

        if state["paused"] and resumes < 5:  # the page paused on its own
            page.evaluate(JS_PLAY)
            resumes += 1

        if state["h"] > result.max_resolution[1]:
            result.max_resolution = (state["w"], state["h"])
        if (
            quality_info["target"]
            and not quality_warned
            and elapsed > 20
            and result.max_resolution[1] < quality_info["target"] * 0.9
        ):
            warn(
                f"Quality lower than expected ({result.max_resolution[0]}x{result.max_resolution[1]}). "
                "Connection too slow?"
            )
            quality_warned = True

        if state["t"] != last_t:
            last_t, last_progress = state["t"], time.time()
            resumes = 0
        elif time.time() - last_progress > 15 and not stall_warned:
            warn("The video isn't advancing (loading?). Recording continues.")
            stall_warned = True

        if not result.image_ok and time.time() >= next_black_check:
            black = is_black_frame(client, scene, settings)
            if black is None:
                next_black_check = time.time() + 5  # screenshot failed: retry later, don't abort
            elif black:
                next_black_check = time.time() + 5
                if elapsed > settings.abort_if_black_after_s:
                    result.reason = StopReason.BLACK.value
                    break
            else:
                result.image_ok = True

        if meter and not audio_warned and elapsed > 20 and meter.peak < settings.audio_level:
            warn("No audio detected. Check that Chrome is routed to CABLE Input.")
            audio_warned = True

    print()
    if result.buffering_pauses:
        print(f"   {result.buffering_pauses} buffering pause(s), excluded from the recording")
    if result.reason not in (StopReason.BLACK.value, StopReason.STALLED.value):
        page.wait_for_timeout(int(settings.tail_s * 1000))
    path = client.stop_record().output_path
    if meter:
        result.audio_ok = meter.peak >= settings.audio_level

    if test_mode:
        stem = f"{TEST_PREFIX} - {result.title}"
    elif result.reason == StopReason.BLACK.value:
        stem = f"{FAILED_BLACK_PREFIX} - {result.title}"
    elif result.reason in INCOMPLETE_REASONS:
        stem = f"{INCOMPLETE_PREFIX} - {result.title}"
    else:
        stem = result.title
    result.file = rename_recording(path, stem)
    if result.file == Path(path):
        warn(f"Couldn't rename the recording, it keeps its OBS name: {result.file.name}")

    return result


def status_text(result: RecordingResult) -> str:
    """One-line summary of a recording result: OK, CHECK: ..., FAILED: ..., or ERROR: ..."""
    if result.reason.startswith("ERROR"):
        return result.reason
    if result.reason == StopReason.BLACK.value:
        return "FAILED: black image (protected video?)"
    if result.reason in INCOMPLETE_REASONS:
        return f"FAILED: incomplete ({result.reason})"
    problems = []
    if not result.image_ok:
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
