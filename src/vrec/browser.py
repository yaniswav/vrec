"""Chrome connection (via CDP) and window-state helpers."""

from __future__ import annotations

import importlib.resources
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from functools import cache
from typing import Any, cast
from urllib.parse import urlsplit

from playwright.sync_api import Browser, CDPSession, Page, sync_playwright

from vrec.display import Screen
from vrec.errors import VrecError


@cache
def load_js(name: str) -> str:
    """Load one of the bundled JS snippets (cached after the first read)."""
    return importlib.resources.files("vrec").joinpath("js", name).read_text(encoding="utf-8")


@contextmanager
def connect_browser(
    chrome_port: int, quality_filter: bool = True, audio_sink: bool = True
) -> Iterator[tuple[Browser, Page]]:
    """Connect to the Chrome instance started by launch_chrome.bat and pick a page to use.

    `quality_filter` installs the manifest filter (quality_filter.js) and `audio_sink` the Web Audio
    tracker (audio_contexts.js) in every page loaded from now on.
    """
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(f"http://localhost:{chrome_port}", timeout=10000)
        except Exception as e:
            raise VrecError("Chrome not found: run launch_chrome.bat first.") from e
        context = browser.contexts[0]
        if quality_filter:
            context.add_init_script(load_js("quality_filter.js"))
        if audio_sink:
            context.add_init_script(load_js("audio_contexts.js"))
        pages = [pg for pg in context.pages if pg.url.startswith(("http", "about:blank", "chrome://newtab"))]
        page = pages[0] if pages else context.new_page()
        page.bring_to_front()
        yield browser, page


def window_state(browser: Browser, page: Page, state: str | None = None) -> str:
    """Read (and optionally change) the Chrome window state: normal, maximized, fullscreen."""
    target_id = page.context.new_cdp_session(page).send("Target.getTargetInfo")["targetInfo"]["targetId"]
    session = browser.new_browser_cdp_session()
    window = session.send("Browser.getWindowForTarget", {"targetId": target_id})
    # CDPSession.send() returns an untyped JSON dict (Any); the CDP protocol guarantees this field.
    previous = cast(str, window["bounds"].get("windowState", "normal"))
    if state and state != previous:
        if previous != "normal":  # Chrome prefers going through "normal" between two states
            session.send(
                "Browser.setWindowBounds",
                {"windowId": window["windowId"], "bounds": {"windowState": "normal"}},
            )
            time.sleep(0.5)
        if state != "normal":
            session.send(
                "Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {"windowState": state}}
            )
        time.sleep(1)
    return previous


@contextmanager
def document_script(page: Page, source: str) -> Iterator[None]:
    """Run `source` in every document the page loads while the block is active (CDP-level init script)."""
    session = page.context.new_cdp_session(page)
    script_id = session.send("Page.addScriptToEvaluateOnNewDocument", {"source": source})["identifier"]
    try:
        yield
    finally:
        with suppress(Exception):
            session.send("Page.removeScriptToEvaluateOnNewDocument", {"identifier": script_id})
            session.detach()


# ---------- Window placement ----------


@dataclass(frozen=True)
class WindowBounds:
    left: int
    top: int
    width: int
    height: int
    state: str


def _window(browser: Browser, page: Page) -> tuple[CDPSession, int, dict[str, Any]]:
    target_id = page.context.new_cdp_session(page).send("Target.getTargetInfo")["targetInfo"]["targetId"]
    session = browser.new_browser_cdp_session()
    window = session.send("Browser.getWindowForTarget", {"targetId": target_id})
    return session, window["windowId"], window["bounds"]


def get_window_bounds(browser: Browser, page: Page) -> WindowBounds:
    _, _, b = _window(browser, page)
    return WindowBounds(b["left"], b["top"], b["width"], b["height"], b.get("windowState", "normal"))


def set_window_bounds(browser: Browser, page: Page, bounds: WindowBounds) -> None:
    """Put the window back to the given position, size and state."""
    session, window_id, current = _window(browser, page)
    if current.get("windowState", "normal") != "normal":
        session.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": "normal"}})
        time.sleep(0.5)
    rect = {"left": bounds.left, "top": bounds.top, "width": bounds.width, "height": bounds.height}
    session.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": rect})
    if bounds.state != "normal":
        session.send(
            "Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": bounds.state}}
        )
    time.sleep(0.5)


def move_window_to(browser: Browser, page: Page, screen: Screen) -> bool:
    """Move the Chrome window onto `screen` and maximize it there. Returns whether it landed on it.

    Window coordinates only need to fall inside the target screen: the window is placed around
    the screen's center, then maximized, which snaps it to that screen whatever the DPI scaling.
    """
    session, window_id, current = _window(browser, page)
    if current.get("windowState", "normal") != "normal":
        session.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": "normal"}})
        time.sleep(0.5)
    cx, cy = screen.center
    rect = {"left": cx - 400, "top": cy - 300, "width": 800, "height": 600}
    session.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": rect})
    session.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": "maximized"}})
    time.sleep(1)
    b = get_window_bounds(browser, page)
    return screen.contains(b.left + b.width / 2, b.top + b.height / 2)


# ---------- Audio output of the recorded page ----------


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


@contextmanager
def microphone_permission(page: Page, origin: str) -> Iterator[None]:
    """Grant the microphone permission to `origin` only for the duration of the block.

    Chrome only reveals audio output names to pages allowed to use the microphone, and vrec needs
    the names to find "CABLE Input". The permission is set back to "ask" right after.
    """
    browser = page.context.browser
    if browser is None:
        yield
        return
    session = browser.new_browser_cdp_session()
    descriptor = {"name": "microphone"}
    session.send("Browser.setPermission", {"permission": descriptor, "setting": "granted", "origin": origin})
    try:
        yield
    finally:
        with suppress(Exception):
            session.send(
                "Browser.setPermission", {"permission": descriptor, "setting": "prompt", "origin": origin}
            )
            session.detach()


def route_audio_to(page: Page, output_label: str) -> tuple[bool, str]:
    """Send the recorded video's sound (only this page) to the audio output named like `output_label`.

    Returns (ok, detail): the device name on success, the reason otherwise.
    """
    try:
        with microphone_permission(page, origin_of(page.url)):
            result = page.evaluate(load_js("set_audio_sink.js"), output_label)
    except Exception as e:  # CDP permission API missing, page gone...
        return False, str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    if result.get("ok"):
        return True, result.get("label", output_label)
    return False, result.get("reason", "unknown reason")
