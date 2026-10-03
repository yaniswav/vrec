"""app.py pins the recording Chrome on all virtual desktops and unpins only what it pinned."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from vrec import app, vdesktop
from vrec.config import Paths, Settings
from vrec.features import FeatureSet

SHOWN = "Chrome is shown on all virtual desktops while recording: you can switch desktops."
HINT = (
    "To switch desktops while recording: Win+Tab, right-click the Chrome window, "
    '"Show this window on all desktops".'
)


class FakeDesktops:
    """Stands in for vdesktop: one window, which are pinned, and what was asked."""

    def __init__(self, hwnd: int | None = 42, pinned: set[int] | None = None, can_pin: bool = True) -> None:
        self.hwnd, self.pinned, self.can_pin = hwnd, pinned or set(), can_pin
        self.calls: list[tuple[str, int]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(vdesktop, "find_chrome_hwnd", lambda page, allow_marker=False: self.hwnd)
        monkeypatch.setattr(vdesktop, "is_pinned", lambda hwnd: hwnd in self.pinned)
        monkeypatch.setattr(vdesktop, "pin", self._pin)
        monkeypatch.setattr(vdesktop, "unpin", self._unpin)

    def _pin(self, hwnd: int) -> bool:
        self.calls.append(("pin", hwnd))
        if self.can_pin:
            self.pinned.add(hwnd)
        return self.can_pin

    def _unpin(self, hwnd: int) -> bool:
        self.calls.append(("unpin", hwnd))
        self.pinned.discard(hwnd)
        return True


def make_batch(tmp_path: Path, features: FeatureSet | None = None) -> app.Batch:
    return app.Batch(
        paths=Paths(data_dir=tmp_path, config=tmp_path / "config.toml"),
        settings=Settings(),
        features=features or FeatureSet(),
        test_mode=False,
        client=SimpleNamespace(),  # type: ignore[arg-type]
        page=SimpleNamespace(),  # type: ignore[arg-type]
        browser=SimpleNamespace(),  # type: ignore[arg-type]
    )


def test_pins_and_unpins_what_it_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeDesktops()
    fake.install(monkeypatch)
    batch = make_batch(tmp_path)
    app._pin_window(batch)
    assert fake.pinned == {42} and batch.pinned_hwnd == 42 and batch.chrome_hwnd == 42
    assert SHOWN in capsys.readouterr().out
    app._unpin_window(batch)
    assert fake.pinned == set() and batch.pinned_hwnd is None


def test_leaves_a_window_the_user_pinned_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeDesktops(pinned={42})
    fake.install(monkeypatch)
    batch = make_batch(tmp_path)
    app._pin_window(batch)
    app._unpin_window(batch)
    assert fake.calls == []
    assert fake.pinned == {42}
    assert SHOWN in capsys.readouterr().out


def test_warns_when_pinning_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    FakeDesktops(can_pin=False).install(monkeypatch)
    batch = make_batch(tmp_path)
    app._pin_window(batch)
    out = capsys.readouterr().out
    assert "Couldn't show Chrome on all virtual desktops (Windows refused)." in out
    assert HINT in out
    assert batch.pinned_hwnd is None


def test_warns_when_the_window_is_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeDesktops(hwnd=None)
    fake.install(monkeypatch)
    app._pin_window(make_batch(tmp_path))
    assert "Couldn't show Chrome on all virtual desktops (window not found)." in capsys.readouterr().out
    assert fake.calls == []


def test_feature_off_does_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDesktops()
    fake.install(monkeypatch)
    batch = make_batch(tmp_path, FeatureSet({"pin_all_desktops": False}))
    app._pin_window(batch)
    assert fake.calls == [] and batch.chrome_hwnd is None


def test_a_different_window_after_a_tab_re_pick(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDesktops()
    fake.install(monkeypatch)
    batch = make_batch(tmp_path)
    app._pin_window(batch)
    fake.hwnd = 43
    app._pin_window(batch)
    assert fake.calls == [("pin", 42), ("unpin", 42), ("pin", 43)]
    assert batch.pinned_hwnd == 43


def test_unpin_without_a_pin_does_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDesktops()
    fake.install(monkeypatch)
    app._unpin_window(make_batch(tmp_path))
    assert fake.calls == []


def test_ensure_page_re_pins_after_a_re_pick(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    old, new = SimpleNamespace(is_closed=lambda: True), SimpleNamespace(is_closed=lambda: False)
    batch = make_batch(tmp_path)
    batch.page = old  # type: ignore[assignment]
    batch.browser = SimpleNamespace(is_connected=lambda: True)  # type: ignore[assignment]
    monkeypatch.setattr(app, "pick_page", lambda browser, latest=False: new)
    monkeypatch.setattr(app, "_prepare_window", lambda b: seen.append("prepare"))
    monkeypatch.setattr(app, "_pin_window", lambda b: seen.append("pin"))
    app._ensure_page(batch, redo_window=True)
    assert seen == ["prepare", "pin"]


def run_with_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record_batch: Any) -> int:
    """Run `_run_locked` with every step faked except pin/unpin."""

    @contextmanager
    def connect(port: int, quality_filter: bool = True, audio_sink: bool = True) -> Any:
        yield SimpleNamespace(), SimpleNamespace()

    client = SimpleNamespace(get_record_status=lambda: SimpleNamespace(output_active=False))
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
        "_ensure_page",
        "_prepare_window",
        "_restore_window",
        "_preflight",
    ):
        monkeypatch.setattr(app, step, lambda batch: None)
    monkeypatch.setattr(app, "_choose_selection", lambda *a: ([("https://x/1", "One")], False))
    monkeypatch.setattr(app, "connect_browser", connect)
    monkeypatch.setattr(app, "_record_batch", record_batch)
    monkeypatch.setattr(app, "_final_report", lambda batch: 0)
    monkeypatch.setattr(app, "_handle_keyboard_interrupt", lambda batch: None)
    paths = Paths(data_dir=tmp_path, config=tmp_path / "config.toml")
    return app._run_locked(paths, Settings(), FeatureSet({"keep_awake": False}), False, True, None)


def test_run_unpins_at_the_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDesktops()
    fake.install(monkeypatch)
    run_with_steps(tmp_path, monkeypatch, lambda batch, selection: None)
    assert fake.calls == [("pin", 42), ("unpin", 42)]


def test_run_unpins_after_ctrl_c(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDesktops()
    fake.install(monkeypatch)

    def interrupted(batch: app.Batch, selection: list[Any]) -> None:
        raise KeyboardInterrupt

    run_with_steps(tmp_path, monkeypatch, interrupted)
    assert fake.calls == [("pin", 42), ("unpin", 42)]
    assert fake.pinned == set()


def test_run_does_not_unpin_a_window_pinned_by_the_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeDesktops(pinned={42})
    fake.install(monkeypatch)
    run_with_steps(tmp_path, monkeypatch, lambda batch, selection: None)
    assert fake.calls == []
    assert fake.pinned == {42}
