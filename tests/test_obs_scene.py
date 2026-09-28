"""vrec's dedicated OBS scene, against an in-memory fake of the OBS WebSocket client."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest

from vrec import obs_scene
from vrec.obs_scene import Monitor


class FakeObs:
    def __init__(self) -> None:
        self.scenes: dict[str, list[dict[str, Any]]] = {
            "Main": [{"sourceName": "Webcam", "inputKind": "dshow"}]
        }
        self.inputs: dict[str, dict[str, Any]] = {"Webcam": {"kind": "dshow", "settings": {}}}
        self.program = "Main"
        self.transforms: list[tuple[str, int, dict[str, Any]]] = []
        self.monitor_items: dict[str, list[dict[str, Any]]] = {
            "monitor_id": [
                {
                    "itemName": "Generic Monitor: 2560x1440 @ 0,0 (Primary Monitor)",
                    "itemValue": "id-main",
                    "itemEnabled": True,
                },
                {"itemName": "Virtual: 3840x2160 @ 2560,0", "itemValue": "id-virtual", "itemEnabled": True},
            ]
        }
        self.next_id = 10
        self.screenshot: str | None = None

    # --- scenes ---
    def get_scene_list(self) -> SimpleNamespace:
        return SimpleNamespace(
            scenes=[{"sceneName": n} for n in self.scenes], current_program_scene_name=self.program
        )

    def create_scene(self, name: str) -> None:
        self.scenes[name] = []

    def get_scene_item_list(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(scene_items=self.scenes[name])

    def set_current_program_scene(self, name: str) -> None:
        self.program = name

    def _add_item(self, scene: str, source: str) -> SimpleNamespace:
        self.next_id += 1
        self.scenes[scene].append(
            {"sourceName": source, "inputKind": self.inputs[source]["kind"], "sceneItemId": self.next_id}
        )
        return SimpleNamespace(scene_item_id=self.next_id)

    def create_scene_item(self, scene: str, source: str, enabled: bool | None = None) -> SimpleNamespace:
        return self._add_item(scene, source)

    def set_scene_item_transform(self, scene: str, item_id: int, transform: dict[str, Any]) -> None:
        self.transforms.append((scene, item_id, transform))

    def get_video_settings(self) -> SimpleNamespace:
        return SimpleNamespace(base_width=3840, base_height=2160)

    # --- inputs ---
    def get_input_list(self, kind: str | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            inputs=[
                {"inputName": n, "inputKind": i["kind"]}
                for n, i in self.inputs.items()
                if kind in (None, i["kind"])
            ]
        )

    def create_input(
        self, scene: str, name: str, kind: str, settings: dict[str, Any], enabled: bool
    ) -> SimpleNamespace:
        self.inputs[name] = {"kind": kind, "settings": dict(settings)}
        return self._add_item(scene, name)

    def get_input_settings(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(input_settings=self.inputs[name]["settings"])

    def set_input_settings(self, name: str, settings: dict[str, Any], overlay: bool) -> None:
        self.inputs[name]["settings"].update(settings)

    def get_input_properties_list_property_items(self, name: str, prop: str) -> SimpleNamespace:
        if prop not in self.monitor_items:
            raise RuntimeError("unknown property")
        return SimpleNamespace(property_items=self.monitor_items[prop])

    def get_source_screenshot(self, *args: Any) -> SimpleNamespace:
        if self.screenshot is None:
            raise RuntimeError("not available")
        return SimpleNamespace(image_data=self.screenshot)


@pytest.fixture
def fake() -> FakeObs:
    return FakeObs()


def test_creates_the_scene_with_a_fitted_display_capture(fake):
    setup = obs_scene.ensure_scene(fake, "vrec")
    assert setup == obs_scene.SceneSetup("vrec", "vrec screen", created_scene=True, created_capture=True)
    assert fake.inputs["vrec screen"] == {"kind": "monitor_capture", "settings": {"capture_cursor": False}}
    scene, item_id, transform = fake.transforms[0]
    assert scene == "vrec" and item_id == fake.scenes["vrec"][0]["sceneItemId"]
    assert transform["boundsType"] == "OBS_BOUNDS_SCALE_INNER"
    assert (transform["boundsWidth"], transform["boundsHeight"]) == (3840.0, 2160.0)
    assert fake.scenes["Main"] == [{"sourceName": "Webcam", "inputKind": "dshow"}]  # user's scene untouched


def test_reuses_an_existing_scene_as_is(fake):
    fake.inputs["My capture"] = {"kind": "monitor_capture", "settings": {"monitor_id": "id-virtual"}}
    fake.scenes["vrec"] = [{"sourceName": "My capture", "inputKind": "monitor_capture", "sceneItemId": 3}]
    setup = obs_scene.ensure_scene(fake, "vrec")
    assert setup == obs_scene.SceneSetup("vrec", "My capture")
    assert fake.transforms == []  # the user's layout (crop, position) is kept


def test_existing_scene_without_capture_gets_one(fake):
    fake.scenes["vrec"] = []
    setup = obs_scene.ensure_scene(fake, "vrec")
    assert setup.created_capture and not setup.created_scene


def test_reuses_a_leftover_capture_input(fake):
    fake.inputs["vrec screen"] = {"kind": "monitor_capture", "settings": {"monitor_id": "id-virtual"}}
    obs_scene.ensure_scene(fake, "vrec")
    assert fake.inputs["vrec screen"]["settings"] == {"monitor_id": "id-virtual"}  # not recreated
    assert [i["sourceName"] for i in fake.scenes["vrec"]] == ["vrec screen"]


def test_ensure_in_scene_adds_only_once(fake):
    fake.inputs["Chrome Audio (VB-CABLE)"] = {"kind": "wasapi_input_capture", "settings": {}}
    fake.scenes["vrec"] = []
    obs_scene.ensure_in_scene(fake, "vrec", "Chrome Audio (VB-CABLE)")
    obs_scene.ensure_in_scene(fake, "vrec", "Chrome Audio (VB-CABLE)")
    assert [i["sourceName"] for i in fake.scenes["vrec"]] == ["Chrome Audio (VB-CABLE)"]


def test_switch_to_returns_the_previous_scene(fake):
    fake.scenes["vrec"] = []
    assert obs_scene.switch_to(fake, "vrec") == "Main"
    assert fake.program == "vrec"
    assert obs_scene.switch_to(fake, "vrec") == "vrec"


def test_monitors_and_selection(fake):
    obs_scene.ensure_scene(fake, "vrec")
    found = obs_scene.monitors(fake, "vrec screen")
    assert [m.value for m in found] == ["id-main", "id-virtual"]
    assert found[0].key == "monitor_id"
    obs_scene.set_monitor(fake, "vrec screen", found[1])
    assert obs_scene.current_monitor(fake, "vrec screen") == "id-virtual"


def test_monitors_on_old_obs_use_the_monitor_index(fake):
    fake.monitor_items = {
        "monitor": [{"itemName": "Display 1", "itemValue": 0}, {"itemName": "Display 2", "itemValue": 1}]
    }
    obs_scene.ensure_scene(fake, "vrec")
    assert obs_scene.monitors(fake, "vrec screen") == [
        Monitor("Display 1", 0, "monitor"),
        Monitor("Display 2", 1, "monitor"),
    ]


def test_grab(fake):
    assert obs_scene.grab(fake, "vrec screen") is None
    fake.screenshot = "data:image/png;base64," + base64.b64encode(b"PNGDATA").decode()
    assert obs_scene.grab(fake, "vrec screen") == b"PNGDATA"
