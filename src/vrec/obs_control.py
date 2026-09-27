"""OBS WebSocket control: connecting, routing audio, and checking the recorded image."""

from __future__ import annotations

import base64
import contextlib
import io
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import obsws_python as obs
from obsws_python.subs import Subs
from PIL import Image

from vrec.config import Paths, Settings
from vrec.errors import VrecError

_AUDIO_SOURCE_KIND = "wasapi_input_capture"

# obsws-python logs a full traceback when OBS isn't reachable; vrec reports it in one line instead.
logging.getLogger("obsws_python").setLevel(logging.CRITICAL)


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
    # obsws_python ships no type hints (it's Any throughout); this is always a str at runtime.
    return cast(str, getattr(r, "scene_name", None) or r.current_program_scene_name)


def prepare_audio(
    client: obs.ReqClient,
    scene: str,
    settings: Settings,
    mutes: dict[str, bool],
    restore_file: Path,
) -> str:
    """Find or create the audio source routed to VB-CABLE, and mute every other sound.

    `mutes` is filled with the mute state of every source this touches -- read *before*
    anything is changed -- and saved to `restore_file` before the first mute call, so a
    crash partway through still leaves enough on disk to undo what was already done.
    Returns the source name.
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

    # Read every current mute state up front and save it, before changing anything.
    other_wasapi = [
        entry["inputName"]
        for entry in client.get_input_list().inputs
        if entry.get("inputKind", "").startswith("wasapi") and entry["inputName"] != source
    ]
    mutes[source] = True if created else client.get_input_mute(source).input_muted
    for name in other_wasapi:
        mutes[name] = client.get_input_mute(name).input_muted
    _save_restore_file(restore_file, mutes)

    client.set_input_mute(source, False)
    client.set_input_audio_monitor_type(source, "OBS_MONITORING_TYPE_NONE")
    # Mute desktop audio, the microphone, and every other audio capture source.
    for name in other_wasapi:
        client.set_input_mute(name, True)
    return source


def restore_mutes(client: obs.ReqClient, mutes: dict[str, bool], restore_file: Path) -> None:
    """Put the mute state of every affected source back to what it was.

    Deletes `restore_file` only once every source was restored; otherwise it is left in
    place so a later run can pick up where this one left off.
    """
    ok = True
    for name, was_muted in mutes.items():
        try:
            client.set_input_mute(name, was_muted)
        except Exception:
            ok = False
    if ok:
        with contextlib.suppress(OSError):
            restore_file.unlink()


def _save_restore_file(path: Path, mutes: dict[str, bool]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(mutes), encoding="utf-8", newline="\n")
    tmp.replace(path)


def is_black_frame(client: obs.ReqClient, scene: str, settings: Settings) -> bool | None:
    """Grab a tiny screenshot of the scene and check whether it's essentially black.

    Returns None if the screenshot itself failed (not evidence either way).
    """
    try:
        r = client.get_source_screenshot(scene, "png", 64, 36, -1)
        data = base64.b64decode(r.image_data.split(",", 1)[1])
        # Pillow types getextrema() as a union covering multi-band images too, but a single
        # "L" (grayscale) band always yields the plain (min, max) form.
        extrema = cast(tuple[float, float], Image.open(io.BytesIO(data)).convert("L").getextrema())
        return extrema[1] < settings.black_level
    except Exception:
        return None


def stop_if_recording(client: obs.ReqClient) -> str | None:
    """Stop OBS recording if one is active. Returns the output path, or None."""
    try:
        if client.get_record_status().output_active:
            return cast(str, client.stop_record().output_path)
    except Exception:
        pass
    return None
