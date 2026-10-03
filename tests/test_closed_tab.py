"""A Chrome tab closed under vrec: pick_page, _ensure_page, and the startup and batch flows."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from vrec import app, browser
from vrec.config import Paths, Settings
from vrec.features import FeatureSet
from vrec.recorder import RecordingResult, StopReason


class FakePage:
    def __init__(self, url: str = "https://x.test/", closed: bool = False) -> None:
        self.url = url
        self.closed = closed
        self.fronted = False

    def is_closed(self) -> bool:
        return self.closed

    def bring_to_front(self) -> None:
        self.fronted = True

    def wait_for_timeout(self, ms: int) -> None:
        pass


class FakeContext:
    def __init__(self, pages: list[FakePage]) -> None:
        self.pages = pages
        self.created: list[FakePage] = []

    def new_page(self) -> FakePage:
        page = FakePage("about:blank")
        self.created.append(page)
        self.pages.append(page)
        return page


class FakeBrowser:
    def __init__(self, pages: list[FakePage] | None = None, connected: bool = True) -> None:
        self.context = FakeContext(pages if pages is not None else [])
        self.contexts = [self.context]
        self.connected = connected

    def is_connected(self) -> bool:
        return self.connected


# ---------- pick_page ----------


def test_pick_page_first_usable_by_default() -> None:
    a, b = FakePage("https://a/"), FakePage("https://b/")
    assert browser.pick_page(FakeBrowser([a, b])) is a  # type: ignore[arg-type]


def test_pick_page_latest_skips_closed_and_unusable() -> None:
    a = FakePage("https://a/")
    b = FakePage("https://b/")
    closed = FakePage("https://c/", closed=True)
    devtools = FakePage("devtools://x")
    page = browser.pick_page(FakeBrowser([a, b, closed, devtools]), latest=True)  # type: ignore[arg-type]
    assert page is b
    assert b.fronted


def test_pick_page_opens_a_new_one_when_none_is_left() -> None:
    fake = FakeBrowser([FakePage("https://a/", closed=True)])
    page = browser.pick_page(fake, latest=True)  # type: ignore[arg-type]
    assert fake.context.created == [page]
    assert page.fronted


# ---------- _ensure_page ----------


def make_batch(tmp_path: Path, page: FakePage, fake: FakeBrowser, **kw: Any) -> app.Batch:
    return app.Batch(
        paths=Paths(data_dir=tmp_path, config=tmp_path / "config.toml"),
        settings=Settings(),
        features=FeatureSet(),
        test_mode=False,
        client=SimpleNamespace(get_version=lambda: None),  # type: ignore[arg-type]
        page=page,  # type: ignore[arg-type]
        browser=fake,  # type: ignore[arg-type]
        **kw,
    )


def test_ensure_page_repicks_a_closed_page(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    old, new = FakePage(closed=True), FakePage("https://n/")
    batch = make_batch(tmp_path, old, FakeBrowser([old, new]))
    app._ensure_page(batch)
    assert batch.page is new
    assert "The Chrome tab vrec was using was closed: using another one." in capsys.readouterr().out


def test_ensure_page_does_nothing_for_an_open_page(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = FakePage()
    batch = make_batch(tmp_path, old, FakeBrowser([old, FakePage()]))
    app._ensure_page(batch)
    assert batch.page is old
    assert capsys.readouterr().out == ""


def test_ensure_page_ignores_a_disconnected_browser(tmp_path: Path) -> None:
    old = FakePage(closed=True)
    batch = make_batch(tmp_path, old, FakeBrowser([FakePage()], connected=False))
    app._ensure_page(batch)
    assert batch.page is old


# ---------- startup flow ----------


def test_startup_survives_a_tab_closed_during_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old, new = FakePage(), FakePage("https://n/")
    fake = FakeBrowser([old, new])
    seen: list[Any] = []
    client = SimpleNamespace(get_record_status=lambda: SimpleNamespace(output_active=False))

    @contextmanager
    def connect(port: int, quality_filter: bool = True, audio_sink: bool = True):  # type: ignore[no-untyped-def]
        yield fake, old

    monkeypatch.setattr(app, "_load_inputs", lambda paths: ([("https://x/1", "One")], {}))
    monkeypatch.setattr(app, "_connect_obs", lambda *a: (client, "pw"))
    monkeypatch.setattr(app.obs_control, "current_scene", lambda c: "Main")
    for step in (
        "_virtual_display_on",
        "_start_chrome_if_needed",
        "_place_chrome_early",
        "_prepare_scene",
        "_prepare_audio",
        "_cleanup_audio",
        "_restore_scene",
        "_virtual_display_off",
        "_restore_window",
    ):
        monkeypatch.setattr(app, step, lambda batch: None)
    monkeypatch.setattr(app, "_choose_selection", lambda *a: ([("https://x/1", "One")], True))
    monkeypatch.setattr(app, "connect_browser", connect)
    monkeypatch.setattr(app.menu, "ask", lambda prompt: setattr(old, "closed", True) or "")
    monkeypatch.setattr(app, "_prepare_window", lambda batch: seen.append(("window", batch.page)))
    monkeypatch.setattr(app, "_preflight", lambda batch: seen.append(("preflight", batch.page)))
    monkeypatch.setattr(app, "_record_batch", lambda batch, sel: seen.append(("batch", batch.page)))
    paths = Paths(data_dir=tmp_path, config=tmp_path / "config.toml")
    code = app._run_locked(paths, Settings(), FeatureSet({"keep_awake": False}), False, False, None)
    assert code == 0
    assert seen == [("window", new), ("preflight", new), ("batch", new)]


# ---------- batch flow ----------


def result() -> RecordingResult:
    return RecordingResult(number=1, title="V", reason=StopReason.ENDED.value, image_ok=True, audio_ok=True)


def test_closed_tab_between_videos_is_recovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    old, new = FakePage(), FakePage("https://n/")
    batch = make_batch(tmp_path, old, FakeBrowser([old, new]), initial_window_state="normal")
    pages: list[Any] = []
    prepared: list[Any] = []

    def record(page: Any, *args: Any) -> RecordingResult:
        pages.append(page)
        if len(pages) == 1:
            old.closed = True
        return result()

    def prepare(b: app.Batch) -> None:
        prepared.append(b.page)
        b.initial_window_state = "fullscreen"

    monkeypatch.setattr(app, "_prepare_window", prepare)
    app._record_batch(batch, [("https://x/1", "A"), ("https://x/2", "B")], record=record)
    assert pages == [old, new]
    assert prepared == [new]
    assert batch.initial_window_state == "normal"  # the original state is kept for the restore
    assert not batch.stopped_early
    assert len(batch.results) == 2
    assert "using another one" in capsys.readouterr().out


def test_disconnected_browser_still_stops_the_batch(tmp_path: Path) -> None:
    old = FakePage()
    fake = FakeBrowser([old])
    batch = make_batch(tmp_path, old, fake)

    def record(page: Any, *args: Any) -> RecordingResult:
        fake.connected = False
        raise RuntimeError("Target closed")

    app._record_batch(batch, [("https://x/1", "A"), ("https://x/2", "B")], record=record)
    assert batch.stopped_early
    assert len(batch.results) == 1
