"""Pre-flight check: prove the whole chain works right before a batch records anything.

Chrome shows full-screen solid colors from a tiny local page and OBS must see them (so the
capture points at the right screen and Chrome is in front); the page then plays a short tone
and OBS's audio meter must move (so the sound path works end to end).

The page is served from http://127.0.0.1 (a secure context for Chrome), which lets it pick
the VB-CABLE output like the recorded pages do.
"""

from __future__ import annotations

import io
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import obsws_python as obs
from PIL import Image
from playwright.sync_api import Browser, Page

from vrec import obs_scene
from vrec.browser import get_window_bounds, load_js, microphone_permission, origin_of
from vrec.display import Screen
from vrec.obs_control import AudioMeter

# Two unusual colors: a match on both can't be a coincidence (desktop, player, black screen...).
TEST_COLORS: tuple[tuple[int, int, int], ...] = ((255, 0, 255), (0, 255, 255))
# Share of the captured image that must show the test color.
MIN_COLOR_SHARE = 0.6
COLOR_TOLERANCE = 60

TEST_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>vrec check</title>
<style>html,body{margin:0;height:100%;background:#000;cursor:none}</style></head>
<body></body></html>
"""


@dataclass(frozen=True)
class CheckResult:
    ok: bool | None  # None: not checked
    title: str
    detail: str = ""
    hint: str = ""

    def line(self) -> str:
        tag = {True: "[ OK ]", False: "[FAIL]", None: "[SKIP]"}[self.ok]
        text = f"  {tag} {self.title}"
        if self.detail:
            text += f": {self.detail}"
        if self.ok is False and self.hint:
            text += f"\n         -> {self.hint}"
        return text


def hex_color(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def color_share(png: bytes, rgb: tuple[int, int, int], tolerance: int = COLOR_TOLERANCE) -> float:
    """Share (0..1) of the image's pixels within `tolerance` of `rgb` on every channel."""
    data = Image.open(io.BytesIO(png)).convert("RGB").tobytes()
    count = len(data) // 3
    if not count:
        return 0.0
    r, g, b = rgb
    close = sum(
        1
        for i in range(0, count * 3, 3)
        if abs(data[i] - r) <= tolerance
        and abs(data[i + 1] - g) <= tolerance
        and abs(data[i + 2] - b) <= tolerance
    )
    return close / count


def shows_colors(
    set_color: Callable[[str], None],
    grab: Callable[[], bytes | None],
    settle: Callable[[], None],
    colors: tuple[tuple[int, int, int], ...] = TEST_COLORS,
) -> bool:
    """Show each test color and check the capture shows it. False as soon as one is missing."""
    for rgb in colors:
        set_color(hex_color(rgb))
        settle()
        png = grab()
        if png is None or color_share(png, rgb) < MIN_COLOR_SHARE:
            return False
    return True


class _PageHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        body = TEST_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - http.server API
        pass  # keep the console clean


@contextmanager
def serve_test_page() -> Iterator[str]:
    """Serve the test page on a free local port for the duration of the block; yields its URL."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PageHandler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


# ---------- The checks ----------


@dataclass
class Context:
    """What the checks need, gathered by app.py once Chrome is placed and OBS is ready."""

    page: Page
    browser: Browser
    client: obs.ReqClient
    capture: str  # OBS source to grab: vrec's display capture, or the program scene
    can_retarget: bool  # True when `capture` is vrec's own display capture (its screen may be changed)
    screen: Screen | None  # the screen Chrome should be on
    meter: AudioMeter | None
    audio_output: str  # "" to play the tone on the default output
    audio_level: float
    wait: Callable[[float], None] = time.sleep


def check_screen(ctx: Context) -> CheckResult:
    if ctx.screen is None:
        return CheckResult(
            False,
            "Virtual screen",
            "not found",
            hint="Turn the virtual display on, or set [display] screen in config.toml.",
        )
    return CheckResult(True, "Virtual screen", ctx.screen.describe())


def check_window(ctx: Context) -> CheckResult:
    title = "Chrome window"
    if ctx.screen is None:
        return CheckResult(None, title, "no virtual screen to compare with")
    b = get_window_bounds(ctx.browser, ctx.page)
    on_screen = ctx.screen.contains(b.left + b.width / 2, b.top + b.height / 2)
    if not on_screen:
        return CheckResult(
            False,
            title,
            f"not on {ctx.screen.name}",
            hint="Move it there with Win+Shift+Arrow, or check [display] screen in config.toml.",
        )
    if b.state != "fullscreen":
        return CheckResult(False, title, f"on {ctx.screen.name} but not fullscreen ({b.state})")
    return CheckResult(True, title, f"on {ctx.screen.name}, fullscreen")


def check_capture(ctx: Context) -> CheckResult:
    """Chrome shows solid colors; OBS must see them. Retargets vrec's display capture if needed."""
    title = "OBS sees Chrome"

    def set_color(color: str) -> None:
        ctx.page.evaluate(load_js("preflight_color.js"), color)

    def grab() -> bytes | None:
        return obs_scene.grab(ctx.client, ctx.capture)

    def settle() -> None:
        ctx.wait(0.8)

    if shows_colors(set_color, grab, settle):
        return CheckResult(True, title, f"'{ctx.capture}' shows the Chrome window")
    if ctx.can_retarget:
        original = obs_scene.current_monitor(ctx.client, ctx.capture)
        candidates = [m for m in obs_scene.monitors(ctx.client, ctx.capture) if m.value != original]
        for monitor in candidates:
            obs_scene.set_monitor(ctx.client, ctx.capture, monitor)
            ctx.wait(1.5)
            if shows_colors(set_color, grab, settle):
                return CheckResult(True, title, f"'{ctx.capture}' now films {monitor.name}")
        if candidates and original is not None:
            key = candidates[0].key
            obs_scene.set_monitor(ctx.client, ctx.capture, obs_scene.Monitor("", original, key))
    return CheckResult(
        False,
        title,
        f"'{ctx.capture}' doesn't show the Chrome window",
        hint="In OBS, point the display capture at the virtual screen, and keep Chrome in front on it.",
    )


def check_sound(ctx: Context) -> CheckResult:
    """A short tone played by the page must reach OBS's VB-CABLE source."""
    title = "Sound reaches OBS"
    if ctx.meter is None:
        return CheckResult(None, title, "audio check unavailable")
    ctx.meter.reset()
    if ctx.audio_output:
        with microphone_permission(ctx.page, origin_of(ctx.page.url)):
            played = ctx.page.evaluate(load_js("preflight_tone.js"), [ctx.audio_output, 1.0])
    else:
        played = ctx.page.evaluate(load_js("preflight_tone.js"), ["", 1.0])
    ctx.wait(0.5)
    if ctx.meter.peak >= ctx.audio_level:
        return CheckResult(True, title, f"tone played on {played.get('sink', 'default')}")
    hint = (
        "Check that the OBS audio source uses CABLE Output (vrec --doctor)."
        if ctx.audio_output
        else "Route Chrome to CABLE Input in the Windows volume mixer (audio_sink is off)."
    )
    return CheckResult(False, title, "OBS heard nothing", hint=hint)


CHECKS: list[Callable[[Context], CheckResult]] = [check_screen, check_window, check_capture, check_sound]


def run_checks(
    ctx: Context, checks: list[Callable[[Context], CheckResult]] | None = None
) -> list[CheckResult]:
    """Run every check on vrec's local test page. A crashing check fails with its error."""
    results: list[CheckResult] = []
    with serve_test_page() as url:
        ctx.page.goto(url, wait_until="load", timeout=15000)
        for check in checks or CHECKS:
            try:
                results.append(check(ctx))
            except Exception as e:  # a check must never break the batch by itself
                message = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
                results.append(CheckResult(False, check.__name__.removeprefix("check_").title(), message))
    return results
