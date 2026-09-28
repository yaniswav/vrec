"""Windows notifications: argument and environment building, with a fake runner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from vrec import app, notify
from vrec.config import Paths, Settings
from vrec.features import FeatureSet
from vrec.recorder import RecordingResult


def test_notify_passes_texts_through_the_environment(monkeypatch):
    monkeypatch.setattr(notify.sys, "platform", "win32")
    calls: list[tuple[list[str], dict[str, str]]] = []

    def run(args: Sequence[str], env: Mapping[str, str]) -> int:
        calls.append((list(args), dict(env)))
        return 0

    assert notify.notify("vrec: done", 'it\'s "quoted" & fine', run=run) is True
    args, env = calls[0]
    assert args[:4] == ["powershell", "-NoProfile", "-NonInteractive", "-Command"]
    assert "it's" not in args[4]  # texts never go through the command line
    assert env["VREC_NOTIFY_TITLE"] == "vrec: done"
    assert env["VREC_NOTIFY_TEXT"] == 'it\'s "quoted" & fine'


def test_notify_failure_is_silent(monkeypatch):
    monkeypatch.setattr(notify.sys, "platform", "win32")
    assert notify.notify("t", "x", run=lambda args, env: 1) is False


def test_notify_off_windows(monkeypatch):
    monkeypatch.setattr(notify.sys, "platform", "linux")
    assert notify.notify("t", "x", run=lambda args, env: pytest.fail("not on Windows")) is False


def _batch(tmp_path: Path, test_mode: bool, **features: bool) -> app.Batch:
    batch = app.Batch(
        paths=Paths(data_dir=tmp_path, config=tmp_path / "config.toml"),
        settings=Settings(),
        features=FeatureSet(features),
        test_mode=test_mode,
    )
    batch.results.append(RecordingResult(number=1, title="A", reason="ended", image_ok=True, duration_s=60))
    return batch


def test_batch_end_sends_the_totals(tmp_path):
    sent: list[tuple[str, str]] = []
    app._final_report(_batch(tmp_path, False), send=lambda t, x: sent.append((t, x)) or True)
    [(title, text)] = sent
    assert title == "vrec: batch finished"
    assert text.startswith("1/1 OK - 1:00 of video - done in ")


def test_test_end_sends_the_status(tmp_path):
    sent: list[tuple[str, str]] = []
    app._final_report(_batch(tmp_path, True), send=lambda t, x: sent.append((t, x)) or True)
    assert sent == [("vrec: test finished", "OK")]


def test_notification_feature_off(tmp_path):
    app._final_report(_batch(tmp_path, False, notify_when_done=False), send=lambda t, x: pytest.fail("off"))
