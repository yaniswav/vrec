"""OBS WebSocket control: connecting, routing audio, and checking the recorded image."""

from __future__ import annotations

import base64
import contextlib
import io
import os
from dataclasses import dataclass, field
from typing import Any

import obsws_python as obs
from obsws_python.subs import Subs
from PIL import Image

from vrec.config import Paths, Settings
from vrec.errors import VrecError

_AUDIO_SOURCE_KIND = "wasapi_input_capture"


def get_password(paths: Paths) -> tuple[str, bool]:
    """Resolve the OBS WebSocket password: env var, then the password file, then a prompt.

    Returns (password, from_env).
    """
    env = os.environ.get("VREC_OBS_PASSWORD")
    if env:
        return env, True
    if paths.password.exists():
        return paths.password.read_text(encoding="utf-8").strip(), False
    print("In OBS: Tools > WebSocket Server Settings > Show Connect Info")
    password = input("Paste the WebSocket server password here: ").strip()
    paths.password.write_text(password, encoding="utf-8")
    return password, False


def connect(settings: Settings, paths: Paths) -> tuple[obs.ReqClient, str]:
    """Connect to OBS over its WebSocket API. Raises VrecError with a friendly message on failure."""
    password, from_env = get_password(paths)
    try:
        client = obs.ReqClient(host=settings.obs_host, port=settings.obs_port, password=password, timeout=10)
    except ConnectionRefusedError as e:
        raise VrecError(
            "Can't reach OBS: open OBS and enable the WebSocket server (Tools > WebSocket Server Settings)."
        ) from e
    except Exception as e:
        if not from_env:
            paths.password.unlink(missing_ok=True)
        raise VrecError(
            "OBS refused the connection: the password is probably wrong. Restart, it will be asked again."
        ) from e
    return client, password


def connect_audio_listener(settings: Settings, password: str, meter: AudioMeter) -> obs.EventClient | None:
    """Connect the event client used to track live audio levels. Returns None if unavailable."""
    try:
        listener = obs.EventClient(
            host=settings.obs_host, port=settings.obs_port, password=password, subs=Subs.INPUTVOLUMEMETERS
        )
        listener.callback.register(meter.on_input_volume_meters)
        return listener
    except Exception:
        return None


@dataclass
class AudioMeter:
    """Tracks the peak audio level of the CABLE source during a recording."""

    source: str
    peak: float = field(default=0.0)

    def reset(self) -> None:
        self.peak = 0.0

    def on_input_volume_meters(self, data: Any) -> None:
        for entry in data.inputs:
            if entry.get("inputName") == self.source:
                for channel in entry.get("inputLevelsMul") or []:
                    if len(channel) > 1:
                        self.peak = max(self.peak, channel[1])


def current_scene(client: obs.ReqClient) -> str:
    r = client.get_current_program_scene()
    return getattr(r, "scene_name", None) or r.current_program_scene_name


def prepare_audio(client: obs.ReqClient, scene: str, settings: Settings) -> tuple[str, dict[str, bool]]:
    """Find or create the audio source routed to VB-CABLE, and mute every other sound.

    Returns (source name, mute states to restore once done).
    """
    inputs = client.get_input_list(_AUDIO_SOURCE_KIND).inputs
    names = [i["inputName"] for i in inputs]
    created = False
    if not inputs:
        client.create_input(scene, settings.audio_source_name, _AUDIO_SOURCE_KIND, {}, True)
        created = True
        names = [settings.audio_source_name]

    devices = client.get_input_properties_list_property_items(names[0], "device_id").property_items
    cable = next((d for d in devices if "cable output" in d["itemName"].lower()), None)
    if cable is None:
        raise RuntimeError("VB-CABLE not found. Install it and restart the PC.")

    source = None
    for name in names:
        if client.get_input_settings(name).input_settings.get("device_id") == cable["itemValue"]:
            source = name
            break
    if source is None:
        if created or settings.audio_source_name in names:
            client.set_input_settings(settings.audio_source_name, {"device_id": cable["itemValue"]}, True)
        else:
            client.create_input(
                scene, settings.audio_source_name, _AUDIO_SOURCE_KIND, {"device_id": cable["itemValue"]}, True
            )
            created = True
        source = settings.audio_source_name

    to_restore = {source: True if created else client.get_input_mute(source).input_muted}
    client.set_input_mute(source, False)
    client.set_input_audio_monitor_type(source, "OBS_MONITORING_TYPE_NONE")

    # Mute desktop audio, the microphone, and every other audio capture source.
    for entry in client.get_input_list().inputs:
        name = entry["inputName"]
        if entry.get("inputKind", "").startswith("wasapi") and name != source:
            to_restore[name] = client.get_input_mute(name).input_muted
            client.set_input_mute(name, True)
    return source, to_restore


def restore_mutes(client: obs.ReqClient, mutes: dict[str, bool]) -> None:
    """Put the mute state of every affected source back to what it was."""
    for name, was_muted in mutes.items():
        with contextlib.suppress(Exception):
            client.set_input_mute(name, was_muted)


def is_black_frame(client: obs.ReqClient, scene: str, settings: Settings) -> bool | None:
    """Grab a tiny screenshot of the scene and check whether it's essentially black.

    Returns None if the screenshot itself failed (not evidence either way).
    """
    try:
        r = client.get_source_screenshot(scene, "png", 64, 36, -1)
        data = base64.b64decode(r.image_data.split(",", 1)[1])
        return Image.open(io.BytesIO(data)).convert("L").getextrema()[1] < settings.black_level
    except Exception:
        return None


def stop_if_recording(client: obs.ReqClient) -> str | None:
    """Stop OBS recording if one is active. Returns the output path, or None."""
    try:
        if client.get_record_status().output_active:
            return client.stop_record().output_path
    except Exception:
        pass
    return None
