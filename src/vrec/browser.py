"""Chrome connection (via CDP) and window-state helpers."""

from __future__ import annotations

import importlib.resources
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from functools import cache

from playwright.sync_api import Browser, Page, sync_playwright

from vrec.errors import VrecError


@cache
def load_js(name: str) -> str:
    """Load one of the bundled JS snippets (cached after the first read)."""
    return importlib.resources.files("vrec").joinpath("js", name).read_text(encoding="utf-8")


@contextmanager
def connect_browser(chrome_port: int, quality_filter: bool = True) -> Iterator[tuple[Browser, Page]]:
    """Connect to the Chrome instance started by launch_chrome.bat and pick a page to use.

    `quality_filter` installs the manifest filter (quality_filter.js) in every page loaded from now on.
    """
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(f"http://localhost:{chrome_port}", timeout=10000)
        except Exception as e:
            raise VrecError("Chrome not found: run launch_chrome.bat first.") from e
        context = browser.contexts[0]
        if quality_filter:
            context.add_init_script(load_js("quality_filter.js"))
        pages = [pg for pg in context.pages if pg.url.startswith(("http", "about:blank", "chrome://newtab"))]
        page = pages[0] if pages else context.new_page()
        page.bring_to_front()
        yield browser, page


def window_state(browser: Browser, page: Page, state: str | None = None) -> str:
    """Read (and optionally change) the Chrome window state: normal, maximized, fullscreen."""
    target_id = page.context.new_cdp_session(page).send("Target.getTargetInfo")["targetInfo"]["targetId"]
    session = browser.new_browser_cdp_session()
    window = session.send("Browser.getWindowForTarget", {"targetId": target_id})
    previous = window["bounds"].get("windowState", "normal")
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
