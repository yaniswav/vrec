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
WINDOW_KIND = "window_capture"
# What vrec can record from: the whole virtual screen, or the recording Chrome window.
CAPTURE_MODES = {"screen": CAPTURE_KIND, "window": WINDOW_KIND}
# The monitor setting of a display capture: "monitor_id" since OBS 27.2, "monitor" before.
MONITOR_KEYS = ("monitor_id", "monitor")
# Sources that put a picture in the scene: a visible one next to vrec's capture is recorded too.
PICTURE_KINDS = (CAPTURE_KIND, WINDOW_KIND, "game_capture")

# Settings of the captures vrec creates. Window capture: "Windows 10" method (the BitBlt one shows a
# black image with Chrome), and the window title must match exactly, so OBS never falls back to
# another Chrome window (your usual browser) when the title changes.
_NEW_CAPTURE_SETTINGS: dict[str, dict[str, Any]] = {
    "screen": {"capture_cursor": False},
    "window": {"method": 2, "priority": 1, "cursor": False, "capture_audio": False, "client_area": True},
}
_CHROME_SUFFIX = " - Google Chrome"


@dataclass(frozen=True)
class SceneSetup:
    scene: str
    capture: str  # name of the capture source vrec records from in that scene
    created_scene: bool = False
    created_capture: bool = False
    shown_capture: bool = False  # vrec's own capture was hidden in the scene and has been shown
    also_visible: tuple[str, ...] = ()  # other visible picture sources in the scene (recorded too)


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


def ensure_scene(client: obs.ReqClient, scene: str, mode: str = "screen") -> SceneSetup:
    """Make sure `scene` exists and shows a capture of the right kind; create only what's missing.

    `mode` is "screen" (display capture) or "window" (window capture). A visible capture of that
    kind already in the scene is used as is (yours, if you set one up). Otherwise vrec's own
    capture ("<scene> screen" / "<scene> window") is shown if it was hidden, or created.
    """
    kind = CAPTURE_MODES[mode]
    created_scene = scene not in scene_names(client)
    if created_scene:
        client.create_scene(scene)

    items = _items(client, scene)
    own = f"{scene} {mode}"
    same_kind = [i for i in items if i.get("inputKind") == kind]
    visible = [i for i in same_kind if i.get("sceneItemEnabled", True)]
    created_capture = shown_capture = False
    if visible:
        capture = visible[0]["sourceName"]
    elif own_item := next((i for i in same_kind if i["sourceName"] == own), None):
        client.set_scene_item_enabled(scene, own_item["sceneItemId"], True)
        capture, shown_capture = own, True
    else:
        capture = own
        existing = [i["inputName"] for i in client.get_input_list(kind).inputs]
        if capture in existing:
            item_id = client.create_scene_item(scene, capture, True).scene_item_id
        else:
            item_id = client.create_input(
                scene, capture, kind, dict(_NEW_CAPTURE_SETTINGS[mode]), True
            ).scene_item_id
        fit_to_canvas(client, scene, item_id)
        created_capture = True

    also_visible = tuple(
        i["sourceName"]
        for i in _items(client, scene)
        if i.get("inputKind") in PICTURE_KINDS
        and i.get("sceneItemEnabled", True)
        and i["sourceName"] != capture
    )
    return SceneSetup(
        scene,
        capture,
        created_scene=created_scene,
        created_capture=created_capture,
        shown_capture=shown_capture,
        also_visible=also_visible,
    )


def _decode_title(encoded: str) -> str:
    """OBS stores window titles with ':' as '#3A' and '#' as '#22'."""
    return encoded.replace("#3A", ":").replace("#22", "#")


def chrome_window(client: obs.ReqClient, capture: str, page_title: str) -> str | None:
    """OBS's identifier of the Chrome window showing `page_title`, from OBS's own window list.

    An exact "<title> - Google Chrome" match wins; otherwise a Chrome window whose title starts
    with the page title (Chrome may shorten or decorate it). None if no window matches.
    """
    title = page_title.strip()
    if not title:
        return None
    try:
        items = client.get_input_properties_list_property_items(capture, "window").property_items
    except Exception:
        return None
    windows: list[tuple[str, str]] = []
    for item in items:
        value = str(item.get("itemValue", ""))
        parts = value.rsplit(":", 2)
        if len(parts) == 3 and parts[2].lower() == "chrome.exe":
            windows.append((_decode_title(parts[0]), value))
    for name, value in windows:
        if name == title + _CHROME_SUFFIX:
            return value
    if len(title) >= 4:
        for name, value in windows:
            if name.startswith(title):
                return value
    return None


def target_window(client: obs.ReqClient, capture: str, page_title: str) -> bool:
    """Point a window capture at the Chrome window showing `page_title`. Returns whether it was found."""
    window = chrome_window(client, capture, page_title)
    if window is None:
        return False
    current: dict[str, Any] = client.get_input_settings(capture).input_settings
    if current.get("window") != window or current.get("priority") != 1:
        client.set_input_settings(capture, {"window": window, "priority": 1}, True)
    return True


def item_enabled(client: obs.ReqClient, scene: str, source: str) -> bool | None:
    """Whether `source` is visible in `scene` (None if it isn't in the scene)."""
    item = next((i for i in _items(client, scene) if i["sourceName"] == source), None)
    return None if item is None else bool(item.get("sceneItemEnabled", True))


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
