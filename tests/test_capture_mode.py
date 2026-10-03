"""Recording from the screen or from the Chrome window ([obs] capture)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_obs_scene import FakeObs

from vrec import app, obs_scene, preflight
from vrec.config import Paths, Settings, load_settings
from vrec.errors import VrecError
from vrec.features import FeatureSet

CHROME = ":Chrome_WidgetWin_1:chrome.exe"


class WindowObs(FakeObs):
    """FakeObs plus scene item visibility and OBS's window list."""

    def __init__(self, windows: list[str] | None = None) -> None:
        super().__init__()
        self.windows = windows or []
        self.monitor_items["window"] = []
        self.enabled_calls: list[tuple[str, int, bool]] = []

    def set_scene_item_enabled(self, scene: str, item_id: int, enabled: bool) -> None:
        self.enabled_calls.append((scene, item_id, enabled))
        for item in self.scenes[scene]:
            if item.get("sceneItemId") == item_id:
                item["sceneItemEnabled"] = enabled

    def get_input_properties_list_property_items(self, name: str, prop: str) -> SimpleNamespace:
        if prop == "window":
            items = [{"itemName": f"[x]: {w}", "itemValue": w, "itemEnabled": True} for w in self.windows]
            return SimpleNamespace(property_items=items)
        return super().get_input_properties_list_property_items(name, prop)

    def add(self, scene: str, name: str, kind: str, enabled: bool = True, **settings: Any) -> None:
        self.inputs[name] = {"kind": kind, "settings": dict(settings)}
        self.scenes.setdefault(scene, [])
        self._add_item(scene, name)
        self.scenes[scene][-1]["sceneItemEnabled"] = enabled


# ---------- scene setup ----------


def test_window_mode_creates_a_safe_window_capture():
    fake = WindowObs()
    setup = obs_scene.ensure_scene(fake, "vrec", "window")
    assert setup.capture == "vrec window" and setup.created_capture and setup.created_scene
    settings = fake.inputs["vrec window"]
    assert settings["kind"] == "window_capture"
    # "Windows 10" method (BitBlt is black with Chrome), exact title only, no cursor.
    assert settings["settings"] == {
        "method": 2,
        "priority": 1,
        "cursor": False,
        "capture_audio": False,
        "client_area": True,
    }
    assert fake.transforms  # fitted to the canvas


def test_a_visible_capture_of_the_right_kind_is_used_as_is():
    fake = WindowObs()
    fake.add("vrec", "chrome fenetre", "window_capture")
    setup = obs_scene.ensure_scene(fake, "vrec", "window")
    assert setup.capture == "chrome fenetre" and not setup.created_capture and not setup.shown_capture


def test_a_hidden_capture_is_not_used_and_vrecs_own_is_shown():
    # The situation found on a real setup: vrec's display capture hidden, a window capture visible.
    fake = WindowObs()
    fake.add("vrec", "vrec screen", "monitor_capture", enabled=False)
    fake.add("vrec", "chrome fenetre", "window_capture")
    setup = obs_scene.ensure_scene(fake, "vrec", "screen")
    assert setup.capture == "vrec screen" and setup.shown_capture
    assert fake.enabled_calls and fake.enabled_calls[0][2] is True
    assert setup.also_visible == ("chrome fenetre",)


def test_a_hidden_capture_that_isnt_vrecs_own_stays_hidden():
    fake = WindowObs()
    fake.add("vrec", "my screen", "monitor_capture", enabled=False)
    setup = obs_scene.ensure_scene(fake, "vrec", "screen")
    assert setup.capture == "vrec screen" and setup.created_capture
    assert fake.enabled_calls == []
    assert setup.also_visible == ()


def test_window_capture_ignores_display_captures_and_reports_them():
    fake = WindowObs()
    fake.add("vrec", "vrec screen", "monitor_capture")
    setup = obs_scene.ensure_scene(fake, "vrec", "window")
    assert setup.capture == "vrec window"
    assert setup.also_visible == ("vrec screen",)


# ---------- finding the Chrome window ----------


def test_chrome_window_exact_title_wins():
    fake = WindowObs(
        [
            "My clip extended - Google Chrome" + CHROME,
            "My clip - Google Chrome" + CHROME,
            "My clip - Notepad:Notepad:notepad.exe",
        ]
    )
    assert obs_scene.chrome_window(fake, "cap", "My clip") == "My clip - Google Chrome" + CHROME


def test_chrome_window_prefix_and_encoded_colons():
    fake = WindowObs(["Part 1#3A The Start (HD) - Google Chrome" + CHROME])
    assert obs_scene.chrome_window(fake, "cap", "Part 1: The Start") == fake.windows[0]


def test_chrome_window_never_matches_another_program_or_nothing():
    fake = WindowObs(["vrec check - Notepad:Notepad:notepad.exe"])
    assert obs_scene.chrome_window(fake, "cap", "vrec check") is None
    assert obs_scene.chrome_window(fake, "cap", "") is None
    assert obs_scene.chrome_window(WindowObs(["abc - Google Chrome" + CHROME]), "cap", "ab") is None


def test_chrome_window_when_obs_cant_list_windows():
    class Broken(WindowObs):
        def get_input_properties_list_property_items(self, name: str, prop: str) -> SimpleNamespace:
            raise RuntimeError("busy")

    assert obs_scene.chrome_window(Broken(), "cap", "Title") is None


def test_target_window_points_the_capture_and_only_writes_on_change():
    fake = WindowObs(["Clip - Google Chrome" + CHROME])
    fake.add("vrec", "vrec window", "window_capture", window="old", priority=0)
    writes: list[dict[str, Any]] = []
    original = fake.set_input_settings
    fake.set_input_settings = lambda n, st, o: (writes.append(st), original(n, st, o))  # type: ignore[method-assign]
    assert obs_scene.target_window(fake, "vrec window", "Clip") is True
    assert fake.inputs["vrec window"]["settings"]["window"] == "Clip - Google Chrome" + CHROME
    assert fake.inputs["vrec window"]["settings"]["priority"] == 1
    assert obs_scene.target_window(fake, "vrec window", "Clip") is True
    assert len(writes) == 1  # already pointed: no second write
    assert obs_scene.target_window(fake, "vrec window", "Unknown") is False


# ---------- config ----------


def test_capture_setting(tmp_path):
    assert Settings().obs_capture == "screen"
    good = tmp_path / "good.toml"
    good.write_text('[obs]\ncapture = "window"\n', encoding="utf-8")
    assert load_settings(good).obs_capture == "window"
    bad = tmp_path / "bad.toml"
    bad.write_text('[obs]\ncapture = "game"\n', encoding="utf-8")
    with pytest.raises(VrecError, match=r"\[obs\] capture"):
        load_settings(bad)


# ---------- pre-flight in window mode ----------


def test_preflight_points_the_window_capture_at_the_test_page(monkeypatch):
    targeted: list[str] = []
    monkeypatch.setattr(preflight.obs_scene, "target_window", lambda c, cap, t: targeted.append(t) or False)
    ctx = preflight.Context(
        page=SimpleNamespace(evaluate=lambda *a: pytest.fail("no color test without the window")),  # type: ignore[arg-type]
        browser=object(),  # type: ignore[arg-type]
        client=object(),  # type: ignore[arg-type]
        capture="vrec window",
        can_retarget=False,
        screen=None,
        meter=None,
        audio_output="",
        audio_level=0.003,
        wait=lambda s: None,
        capture_mode="window",
    )
    result = preflight.check_capture(ctx)
    assert targeted == [preflight.TEST_TITLE]
    assert result.ok is False and "can't find the recording Chrome window" in result.detail


# ---------- app wiring ----------


def make_batch(tmp_path: Path, **settings: Any) -> app.Batch:
    return app.Batch(
        paths=Paths(data_dir=tmp_path, config=tmp_path / "config.toml"),
        settings=Settings(**settings),
        features=FeatureSet(),
        test_mode=False,
        client=WindowObs(),  # type: ignore[arg-type]
        scene="Main",
    )


def test_prepare_scene_in_window_mode_reports_what_it_did(tmp_path, monkeypatch, capsys):
    setup = obs_scene.SceneSetup("vrec", "vrec screen", shown_capture=True)
    monkeypatch.setattr(app.obs_scene, "ensure_scene", lambda client, name, mode: setup)
    monkeypatch.setattr(app.obs_scene, "switch_to", lambda client, name: "Main")
    monkeypatch.setattr(app.obs_control, "current_scene", lambda client: "Main")
    batch = make_batch(tmp_path)
    app._prepare_scene(batch)
    out = capsys.readouterr().out
    assert "Showed 'vrec screen' in the OBS scene 'vrec' (it was hidden)." in out
    assert batch.capture_mode == "screen"


@pytest.mark.parametrize(
    ("mode", "expected"), [("window", {"window_capture": "vrec window"}), ("screen", {})]
)
def test_record_gets_the_window_capture_only_in_window_mode(tmp_path, mode, expected):
    batch = make_batch(tmp_path, obs_capture=mode)
    batch.page = object()  # type: ignore[assignment]
    batch.capture, batch.capture_mode = f"vrec {mode}", mode
    seen: list[dict[str, Any]] = []

    def record(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return SimpleNamespace(reason="ended", image_ok=True, audio_ok=True, target_height=0)

    app._record_one_with_retry(batch, 1, 1, "https://x.test/1", "One", record)
    assert seen == [expected]
