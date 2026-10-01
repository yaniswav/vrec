"""Virtual display and window placement steps of a batch (display and browser calls are faked)."""

from __future__ import annotations

from pathlib import Path

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
        browser=object(),  # type: ignore[arg-type]
        page=object(),  # type: ignore[arg-type]
    )


REAL_SIDE = Screen("DISPLAY2", -1920, 0, 1920, 1080, primary=False)  # a real second monitor


@pytest.fixture
def display_calls(monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(app.display, "set_virtual_display", lambda on: calls.append(on))
    monkeypatch.setattr(app.display, "wait_for_new_screen", lambda before: VIRTUAL)
    return calls


def test_display_left_alone_when_feature_off(tmp_path, monkeypatch, display_calls):
    monkeypatch.setattr(app.display, "virtual_display_enabled", lambda: pytest.fail("should not ask"))
    batch = make_batch(tmp_path)  # manage_virtual_display is off by default
    app._virtual_display_on(batch)
    app._virtual_display_off(batch)
    assert display_calls == []


def test_display_already_on_is_not_touched(tmp_path, monkeypatch, display_calls):
    monkeypatch.setattr(app.display, "virtual_display_enabled", lambda: True)
    batch = make_batch(tmp_path, manage_virtual_display=True)
    app._virtual_display_on(batch)
    app._virtual_display_off(batch)
    assert display_calls == []
    assert not batch.display_turned_on


def test_a_real_second_monitor_is_not_mistaken_for_the_virtual_display(
    tmp_path, monkeypatch, display_calls, capsys
):
    monkeypatch.setattr(app.display, "list_screens", lambda: [MAIN, REAL_SIDE])
    monkeypatch.setattr(app.display, "virtual_display_enabled", lambda: False)
    batch = make_batch(tmp_path, manage_virtual_display=True)
    app._virtual_display_on(batch)
    assert batch.display_turned_on
    assert batch.virtual_screen == VIRTUAL  # the screen that appeared, not the real side monitor
    app._virtual_display_off(batch)
    assert display_calls == [True, False]
    out = capsys.readouterr().out
    assert "Virtual display turned on: DISPLAY3" in out
    assert "Virtual display turned off." in out


def test_placement_targets_the_screen_that_was_turned_on(tmp_path, monkeypatch):
    moves: list[Screen] = []
    monkeypatch.setattr(app.display, "list_screens", lambda: [MAIN, REAL_SIDE, VIRTUAL])
    monkeypatch.setattr(app, "get_window_bounds", lambda b, p: WindowBounds(0, 0, 800, 600, "normal"))
    monkeypatch.setattr(app, "move_window_to", lambda b, p, s: moves.append(s) or True)
    monkeypatch.setattr(app, "window_state", lambda b, p, state=None: "normal")
    batch = make_batch(tmp_path)
    batch.virtual_screen = VIRTUAL
    app._prepare_window(batch)
    assert moves == [VIRTUAL]


def test_display_helper_missing_only_warns(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(app.display, "list_screens", lambda: [MAIN])
    monkeypatch.setattr(app.display, "virtual_display_enabled", lambda: None)

    def fail(on: bool) -> None:
        raise VrecError("The virtual display helper isn't installed")

    monkeypatch.setattr(app.display, "set_virtual_display", fail)
    batch = make_batch(tmp_path, manage_virtual_display=True)
    app._virtual_display_on(batch)
    assert not batch.display_turned_on
    assert "helper isn't installed" in capsys.readouterr().out


def test_window_moved_then_restored(tmp_path, monkeypatch, capsys):
    before = WindowBounds(10, 20, 800, 600, "normal")
    moves: list[Screen] = []
    restored: list[WindowBounds] = []
    monkeypatch.setattr(app.display, "list_screens", lambda: [MAIN, VIRTUAL])
    monkeypatch.setattr(app, "get_window_bounds", lambda b, p: before)
    monkeypatch.setattr(app, "move_window_to", lambda b, p, s: moves.append(s) or True)
    monkeypatch.setattr(app, "set_window_bounds", lambda b, p, bounds: restored.append(bounds))
    monkeypatch.setattr(app, "window_state", lambda b, p, state=None: "normal")
    batch = make_batch(tmp_path)
    app._prepare_window(batch)
    app._restore_window(batch)
    assert moves == [VIRTUAL]
    assert restored == [before]
    assert "Chrome moved to DISPLAY3" in capsys.readouterr().out


def test_no_virtual_screen_keeps_the_window_where_it_is(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(app.display, "list_screens", lambda: [MAIN, VIRTUAL])
    monkeypatch.setattr(app.display, "pick_screen", lambda screens, wanted: None)
    monkeypatch.setattr(app, "move_window_to", lambda *a: pytest.fail("should not move"))
    monkeypatch.setattr(app, "window_state", lambda b, p, state=None: "maximized")
    batch = make_batch(tmp_path)
    app._prepare_window(batch)
    assert batch.initial_bounds is None
    assert "No virtual screen found" in capsys.readouterr().out


def test_window_placement_feature_off(tmp_path, monkeypatch):
    monkeypatch.setattr(app.display, "list_screens", lambda: pytest.fail("should not look for screens"))
    monkeypatch.setattr(app, "window_state", lambda b, p, state=None: "normal")
    batch = make_batch(tmp_path, auto_place_window=False)
    app._prepare_window(batch)
    assert batch.initial_window_state == "normal"


def test_install_display_helper_cli_needs_a_matching_adapter(monkeypatch, capsys):
    from vrec import __main__ as cli

    monkeypatch.setattr(app.display, "list_display_devices", lambda: ["Intel(R) UHD Graphics"])
    monkeypatch.setattr(app.display, "install_helper", lambda pattern: pytest.fail("should not install"))
    assert cli.main(["--install-display-helper"]) == 1
    out = capsys.readouterr().out
    assert "No display adapter matches '*Virtual*'" in out
    assert "Intel(R) UHD Graphics" in out


def test_install_display_helper_cli(monkeypatch, capsys, tmp_path):
    from vrec import __main__ as cli

    installed: list[str] = []
    monkeypatch.setattr(app.display, "list_display_devices", lambda: ["Virtual Display Driver", "GPU"])
    monkeypatch.setattr(
        app.display, "install_helper", lambda pattern: installed.append(pattern) or tmp_path / "x.ps1"
    )
    assert cli.main(["--install-display-helper", "*virtual display*"]) == 0
    assert installed == ["*virtual display*"]
    assert "vrec --enable manage_virtual_display" in capsys.readouterr().out


def test_uninstall_display_helper_cli(monkeypatch, capsys):
    from vrec import __main__ as cli

    calls: list[bool] = []
    monkeypatch.setattr(app.display, "uninstall_helper", lambda: calls.append(True))
    assert cli.main(["--uninstall-display-helper"]) == 0
    assert calls == [True]
