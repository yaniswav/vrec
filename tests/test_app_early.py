"""Chrome is started and placed before the menu; `vrec --launch-chrome`."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from vrec import app
from vrec.browser import WindowBounds
from vrec.config import Paths, Settings
from vrec.display import Screen
from vrec.errors import VrecError
from vrec.features import FeatureSet

MAIN = Screen("DISPLAY1", 0, 0, 2560, 1440, primary=True)
VIRTUAL = Screen("DISPLAY3", 2560, 0, 3840, 2160, primary=False)


def make_batch(tmp_path: Path, **features: bool) -> app.Batch:
    return app.Batch(
        paths=Paths(data_dir=tmp_path, config=tmp_path / "config.toml"),
        settings=Settings(),
        features=FeatureSet(features),
        test_mode=False,
    )


@pytest.fixture
def fake_chrome(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "bounds": WindowBounds(100, 100, 800, 600, "normal"),
        "moves": [],
        "scripts": None,
    }

    @contextmanager
    def connect(port: int, quality_filter: bool = True, audio_sink: bool = True):  # type: ignore[no-untyped-def]
        state["scripts"] = (quality_filter, audio_sink)
        yield object(), object()

    def move(browser: object, page: object, screen: Screen) -> bool:
        state["moves"].append(screen)
        state["bounds"] = WindowBounds(screen.left, screen.top, screen.width, screen.height, "maximized")
        return True

    monkeypatch.setattr(app, "connect_browser", connect)
    monkeypatch.setattr(app, "get_window_bounds", lambda b, p: state["bounds"])
    monkeypatch.setattr(app, "move_window_to", move)
    monkeypatch.setattr(app.display, "list_screens", lambda: [MAIN, VIRTUAL])
    return state


def test_chrome_is_placed_before_the_menu_without_page_scripts(tmp_path, fake_chrome, capsys):
    batch = make_batch(tmp_path)
    app._place_chrome_early(batch)
    assert fake_chrome["moves"] == [VIRTUAL]
    assert fake_chrome["scripts"] == (False, False)  # a plain connection, not the recording one
    assert batch.browser is None and batch.page is None and batch.initial_bounds is None
    assert "Chrome moved to DISPLAY3" in capsys.readouterr().out


def test_a_window_already_on_the_screen_is_not_moved_again(tmp_path, fake_chrome, capsys):
    fake_chrome["bounds"] = WindowBounds(2560, 0, 3840, 2160, "maximized")
    batch = make_batch(tmp_path)
    app._place_chrome_early(batch)
    assert fake_chrome["moves"] == []
    assert "Chrome moved" not in capsys.readouterr().out


def test_early_placement_feature_off(tmp_path, fake_chrome):
    app._place_chrome_early(make_batch(tmp_path, auto_place_window=False))
    assert fake_chrome["scripts"] is None


def test_early_placement_ignores_an_unreachable_chrome(tmp_path, monkeypatch):
    @contextmanager
    def connect(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        raise VrecError("Chrome not found: run launch_chrome.bat first.")
        yield

    monkeypatch.setattr(app, "connect_browser", connect)
    app._place_chrome_early(make_batch(tmp_path))  # no exception: the recording step reports it


def test_launch_chrome_command_starts_and_places(tmp_path, fake_chrome, monkeypatch, capsys):
    started: list[bool] = []
    monkeypatch.setattr(
        app.launcher,
        "ensure_chrome",
        lambda settings, features: started.append(features.enabled("auto_start_chrome")),
    )
    (tmp_path / "features.toml").write_text("[features]\nauto_start_chrome = false\n", encoding="utf-8")
    assert app.launch_chrome(tmp_path, tmp_path / "config.toml") == 0
    assert started == [True]  # the command always starts it, whatever the feature says
    assert fake_chrome["moves"] == [VIRTUAL]
    assert "Log in to the site" in capsys.readouterr().out


def test_cli_launch_chrome(tmp_path, monkeypatch):
    from vrec import __main__ as cli

    calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(app, "launch_chrome", lambda data_dir, config: calls.append((data_dir, config)) or 0)
    assert cli.main(["--launch-chrome", "--data-dir", str(tmp_path)]) == 0
    assert calls == [(tmp_path, tmp_path / "config.toml")]


def test_screen_and_chrome_are_ready_before_the_menu(tmp_path, monkeypatch):
    """Order of the steps, and cleanup even when the menu is left without choosing anything."""
    order: list[str] = []
    client = SimpleNamespace(get_current_program_scene=lambda: SimpleNamespace(scene_name="Main"))
    monkeypatch.setattr(app, "_load_inputs", lambda paths: ([("https://x.test/1", "One")], {}))
    monkeypatch.setattr(app, "_connect_obs", lambda *a: (client, "pw"))
    monkeypatch.setattr(app.obs_control, "current_scene", lambda c: "Main")
    for step in ("_virtual_display_on", "_start_chrome_if_needed", "_place_chrome_early", "_prepare_scene"):
        monkeypatch.setattr(app, step, lambda batch, step=step: order.append(step))
    for step in ("_cleanup_audio", "_restore_scene", "_virtual_display_off"):
        monkeypatch.setattr(app, step, lambda batch, step=step: order.append(step))

    def choose(*args: Any) -> tuple[list[Any], bool]:
        order.append("menu")
        return [], True  # the user quits the menu

    monkeypatch.setattr(app, "_choose_selection", choose)
    paths = Paths(data_dir=tmp_path, config=tmp_path / "config.toml")
    assert app._run_locked(paths, Settings(), FeatureSet(), False, False, None) == 0
    assert order == [
        "_virtual_display_on",
        "_start_chrome_if_needed",
        "_place_chrome_early",
        "menu",
        "_cleanup_audio",
        "_restore_scene",
        "_virtual_display_off",
    ]
