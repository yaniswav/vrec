"""vrec's own OBS scene: record from a dedicated scene instead of touching the user's scenes.

The scene (default "vrec") is created if missing with a display capture filling the canvas.
If it already exists, it is reused as is (the user may have added a crop for 360 videos...),
only what's missing is added. During a batch OBS switches to it, then back to the scene the
user was on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import obsws_python as obs

CAPTURE_KIND = "monitor_capture"
# The monitor setting of a display capture: "monitor_id" since OBS 27.2, "monitor" before.
MONITOR_KEYS = ("monitor_id", "monitor")


@dataclass(frozen=True)
class SceneSetup:
    scene: str
    capture: str  # name of the display capture source in that scene
    created_scene: bool = False
    created_capture: bool = False


@dataclass(frozen=True)
class Monitor:
    name: str  # as OBS shows it, e.g. "Generic PnP Monitor: 3840x2160 @ 2560,0"
    value: Any  # what goes into the capture's settings
    key: str  # "monitor_id" or "monitor"


def _items(client: obs.ReqClient, scene: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = client.get_scene_item_list(scene).scene_items
    return items


def scene_names(client: obs.ReqClient) -> list[str]:
    return [s["sceneName"] for s in client.get_scene_list().scenes]


def ensure_scene(client: obs.ReqClient, scene: str) -> SceneSetup:
    """Make sure `scene` exists and holds a display capture; create only what's missing."""
    created_scene = scene not in scene_names(client)
    if created_scene:
        client.create_scene(scene)

    capture = next(
        (i["sourceName"] for i in _items(client, scene) if i.get("inputKind") == CAPTURE_KIND), None
    )
    if capture:
        return SceneSetup(scene, capture, created_scene=created_scene)

    capture = f"{scene} screen"
    existing = [i["inputName"] for i in client.get_input_list(CAPTURE_KIND).inputs]
    if capture in existing:
        item_id = client.create_scene_item(scene, capture, True).scene_item_id
    else:
        item_id = client.create_input(
            scene, capture, CAPTURE_KIND, {"capture_cursor": False}, True
        ).scene_item_id
    fit_to_canvas(client, scene, item_id)
    return SceneSetup(scene, capture, created_scene=created_scene, created_capture=True)


def fit_to_canvas(client: obs.ReqClient, scene: str, item_id: int) -> None:
    """Scale the item to fill the canvas (keeping its aspect ratio), like "Fit to screen"."""
    video = client.get_video_settings()
    client.set_scene_item_transform(
        scene,
        item_id,
        {
            "positionX": 0,
            "positionY": 0,
            "alignment": 5,  # top-left
            "boundsType": "OBS_BOUNDS_SCALE_INNER",
            "boundsAlignment": 0,  # centered in the bounds
            "boundsWidth": float(video.base_width),
            "boundsHeight": float(video.base_height),
        },
    )


def ensure_in_scene(client: obs.ReqClient, scene: str, source: str) -> None:
    """Add an existing source (e.g. the VB-CABLE audio input) to the scene if it isn't there."""
    if not any(i["sourceName"] == source for i in _items(client, scene)):
        client.create_scene_item(scene, source, True)


def switch_to(client: obs.ReqClient, scene: str) -> str:
    """Make `scene` the program scene. Returns the scene that was on air before."""
    previous: str = client.get_scene_list().current_program_scene_name
    if previous != scene:
        client.set_current_program_scene(scene)
    return previous


def monitors(client: obs.ReqClient, capture: str) -> list[Monitor]:
    """The screens the display capture can show, as OBS lists them."""
    for key in MONITOR_KEYS:
        try:
            items = client.get_input_properties_list_property_items(capture, key).property_items
        except Exception:
            continue
        if items:
            return [Monitor(i["itemName"], i["itemValue"], key) for i in items if i.get("itemEnabled", True)]
    return []


def current_monitor(client: obs.ReqClient, capture: str) -> Any:
    settings: dict[str, Any] = client.get_input_settings(capture).input_settings
    return next((settings[k] for k in MONITOR_KEYS if k in settings), None)


def set_monitor(client: obs.ReqClient, capture: str, monitor: Monitor) -> None:
    client.set_input_settings(capture, {monitor.key: monitor.value}, True)


def grab(client: obs.ReqClient, source: str, width: int = 64, height: int = 36) -> bytes | None:
    """A tiny PNG of what the source shows right now, or None if OBS can't provide it."""
    import base64

    try:
        data: str = client.get_source_screenshot(source, "png", width, height, -1).image_data
        return base64.b64decode(data.split(",", 1)[1])
    except Exception:
        return None
