"""_run_locked happy path: every external step faked, the order of the steps asserted."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from vrec import app
from vrec.config import Paths, Settings
from vrec.errors import VrecError
from vrec.features import FeatureSet

VIDEOS = [("https://x.test/1", "One"), ("https://x.test/2", "Two")]


class Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self.order: list[str] = []
        self.paths = Paths(data_dir=tmp_path, config=tmp_path / "config.toml")
        self.recording_active = False
        self.selection: list[Any] = []
        order = self.order

        client = SimpleNamespace(
            get_record_status=lambda: order.append("idle check") or SimpleNamespace(output_active=False)
        )
        self.client = client
        monkeypatch.setattr(app, "_load_inputs", lambda paths: (list(VIDEOS), {}))
        monkeypatch.setattr(app, "_connect_obs", lambda *a: (client, "pw"))
        monkeypatch.setattr(app.obs_control, "current_scene", lambda c: "Main")

        steps = {
            "_virtual_display_on": "virtual display",
            "_start_chrome_if_needed": "chrome start",
            "_place_chrome_early": "early placement",
            "_prepare_scene": "scene",
            "_prepare_audio": "audio",
            "_prepare_window": "window prep",
            "_preflight": "preflight",
            "_restore_window": "window restore",
            "_cleanup_audio": "audio cleanup",
            "_restore_scene": "scene restore",
            "_virtual_display_off": "display off",
        }
        for func, label in steps.items():
            monkeypatch.setattr(app, func, lambda batch, label=label: order.append(label))

        def choose(*args: Any) -> tuple[list[Any], bool]:
            order.append("menu")
            return list(VIDEOS), False

        def record_batch(batch: app.Batch, selection: list[Any]) -> None:
            order.append("batch")
            self.selection = selection

        @contextmanager
        def connect(port: int, quality_filter: bool = True, audio_sink: bool = True):  # type: ignore[no-untyped-def]
            order.append("browser connection")
            try:
                yield "browser", "page"
            finally:
                order.append("browser closed")

        monkeypatch.setattr(app, "_choose_selection", choose)
        monkeypatch.setattr(app, "_record_batch", record_batch)
        monkeypatch.setattr(app, "connect_browser", connect)
        monkeypatch.setattr(app.power, "stay_awake", lambda: order.append("keep-awake") or True)
        monkeypatch.setattr(app.power, "allow_sleep", lambda: order.append("allow sleep"))

    def run(self, features: FeatureSet | None = None) -> int:
        return app._run_locked(self.paths, Settings(), features or FeatureSet(), False, False, None)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Harness:
    return Harness(monkeypatch, tmp_path)


def test_full_order_of_a_successful_run(harness: Harness) -> None:
    assert harness.run() == 0
    assert harness.order == [
        "virtual display",
        "chrome start",
        "early placement",
        "menu",
        "keep-awake",
        "scene",
        "audio",
        "browser connection",
        "idle check",
        "window prep",
        "preflight",
        "batch",
        "window restore",
        "browser closed",
        "audio cleanup",
        "scene restore",
        "display off",
        "allow sleep",
    ]
    assert harness.selection == VIDEOS


def test_keep_awake_off_skips_both_ends(harness: Harness) -> None:
    assert harness.run(FeatureSet({"keep_awake": False})) == 0
    assert "keep-awake" not in harness.order and "allow sleep" not in harness.order


def test_nothing_selected_only_runs_the_cleanup(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    def nothing(*args: Any) -> tuple[list[Any], bool]:
        harness.order.append("menu")
        return [], True

    monkeypatch.setattr(app, "_choose_selection", nothing)
    assert harness.run() == 0
    assert harness.order == [
        "virtual display",
        "chrome start",
        "early placement",
        "menu",
        "audio cleanup",
        "scene restore",
        "display off",
    ]


def test_a_recording_started_meanwhile_stops_the_run_but_cleans_up(harness: Harness) -> None:
    harness.client.get_record_status = lambda: SimpleNamespace(output_active=True)  # type: ignore[attr-defined]
    with pytest.raises(VrecError, match="started meanwhile"):
        harness.run()
    assert harness.order[-4:] == ["audio cleanup", "scene restore", "display off", "allow sleep"]
    assert "batch" not in harness.order
    assert "window restore" not in harness.order  # the window was never prepared


def test_a_failing_batch_still_restores_the_window_and_everything_else(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(batch: app.Batch, selection: list[Any]) -> None:
        raise VrecError("batch broke")

    monkeypatch.setattr(app, "_record_batch", explode)
    with pytest.raises(VrecError, match="batch broke"):
        harness.run()
    assert harness.order[-6:] == [
        "window restore",
        "browser closed",
        "audio cleanup",
        "scene restore",
        "display off",
        "allow sleep",
    ]


def test_exit_code_follows_the_batch_outcome(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    def stopped(batch: app.Batch, selection: list[Any]) -> None:
        batch.stopped_early = True

    monkeypatch.setattr(app, "_record_batch", stopped)
    assert harness.run() == 1
