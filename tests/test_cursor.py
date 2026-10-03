"""The mouse cursor setting of vrec's own OBS capture ([obs] capture_cursor)."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_obs_scene import FakeObs

from vrec import obs_scene
from vrec.config import load_settings
from vrec.errors import VrecError


def test_cursor_off_in_a_new_capture() -> None:
    fake = FakeObs()
    obs_scene.ensure_scene(fake, "vrec")
    assert fake.inputs["vrec screen"]["settings"]["capture_cursor"] is False


def test_cursor_on_in_a_new_capture_when_asked() -> None:
    fake = FakeObs()
    obs_scene.ensure_scene(fake, "vrec", capture_cursor=True)
    assert fake.inputs["vrec screen"]["settings"]["capture_cursor"] is True


def test_cursor_setting_is_applied_to_an_existing_own_capture() -> None:
    fake = FakeObs()
    fake.inputs["vrec screen"] = {"kind": "monitor_capture", "settings": {"capture_cursor": True}}
    obs_scene.ensure_scene(fake, "vrec")
    assert fake.inputs["vrec screen"]["settings"]["capture_cursor"] is False
    obs_scene.ensure_scene(fake, "vrec", capture_cursor=True)
    assert fake.inputs["vrec screen"]["settings"]["capture_cursor"] is True


def test_cursor_untouched_on_a_capture_the_user_made() -> None:
    fake = FakeObs()
    fake.inputs["My capture"] = {"kind": "monitor_capture", "settings": {"capture_cursor": True}}
    fake.scenes["vrec"] = [
        {
            "sourceName": "My capture",
            "inputKind": "monitor_capture",
            "sceneItemId": 1,
            "sceneItemEnabled": True,
        }
    ]
    setup = obs_scene.ensure_scene(fake, "vrec")
    assert setup.capture == "My capture"
    assert fake.inputs["My capture"]["settings"]["capture_cursor"] is True


def test_window_capture_uses_the_cursor_key() -> None:
    fake = FakeObs()
    obs_scene.ensure_scene(fake, "vrec", "window", capture_cursor=True)
    assert fake.inputs["vrec window"]["settings"]["cursor"] is True
    obs_scene.ensure_scene(fake, "vrec", "window", capture_cursor=False)
    assert fake.inputs["vrec window"]["settings"]["cursor"] is False


def test_capture_cursor_setting(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    assert load_settings(path).obs_capture_cursor is False
    path.write_text("[obs]\ncapture_cursor = true\n", encoding="utf-8")
    assert load_settings(path).obs_capture_cursor is True
    path.write_text('[obs]\ncapture_cursor = "yes"\n', encoding="utf-8")
    with pytest.raises(VrecError, match="true or false"):
        load_settings(path)
