"""`vrec --doctor`: read-only preflight checks.

Doctor never starts a recording, never touches OBS or Chrome's state (beyond, like a normal
run, possibly saving the OBS WebSocket password the first time it's typed in), and never
takes the instance lock, so it can run happily alongside, or instead of, a normal run.
"""

from __future__ import annotations

import json
import shutil
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import obsws_python as obs

from vrec import display, launcher, obs_control, obs_scene
from vrec.config import Paths, Settings, load_settings
from vrec.console import first_line
from vrec.errors import VrecError
from vrec.features import FeatureSet
from vrec.playlist import read_playlist

_AUDIO_SOURCE_KIND = "wasapi_input_capture"
_MIN_OBS_VERSION = 28
_MIN_WEBSOCKET_VERSION = 5
_MIN_FREE_GB = 20.0


@dataclass
class Check:
    """The result of one doctor check."""

    status: str  # "ok", "warn", "fail", or "info"
    title: str
    detail: str = ""
    hint: str = ""


@dataclass
class _Context:
    """Shared state a check function may need, including a lazily connected OBS client."""

    settings: Settings
    paths: Paths
    features: FeatureSet
    _client: obs.ReqClient | None = field(default=None, init=False, repr=False)
    _client_error: str | None = field(default=None, init=False, repr=False)
    _client_unreachable: bool = field(default=False, init=False, repr=False)
    _tried: bool = field(default=False, init=False, repr=False)

    def client(self) -> obs.ReqClient | None:
        """The OBS client, connecting (and caching the result, good or bad) on first use."""
        if not self._tried:
            self._tried = True
            try:
                if not launcher.port_listening(self.settings.obs_host, self.settings.obs_port):
                    raise obs_control.ObsUnreachable()  # fast: no 2 s connection attempt on a closed port
                self._client, _password = obs_control.connect(self.settings, self.paths)
            except obs_control.ObsUnreachable as e:
                self._client_error = str(e)
                self._client_unreachable = True
            except VrecError as e:
                self._client_error = str(e)
        return self._client


def _skip(title: str) -> Check:
    return Check("info", title, detail="Skipped: OBS isn't reachable.")


# ---------------------------------------------------------------------------
# Checks. Each takes the shared context and returns one Check. Add more by
# appending to CHECKS below. The order there is the order they're printed in.
# ---------------------------------------------------------------------------


def check_data_dir(ctx: _Context) -> Check:
    """Data directory & playlist."""
    path = ctx.paths.videos
    if not path.exists():
        return Check(
            "fail",
            "Playlist file",
            detail=f"Not found: {path}",
            hint=f"Copy videos.example.txt to {path} and add your links.",
        )
    videos = read_playlist(path)
    if not videos:
        return Check(
            "fail",
            "Playlist file",
            detail=f"{path.name} contains no links.",
            hint=f"Copy videos.example.txt to {path} and add your links.",
        )
    return Check("ok", "Playlist file", detail=f"{len(videos)} link(s) in {path.name}.")


def check_config(ctx: _Context) -> Check:
    """Config file."""
    path = ctx.paths.config
    if not path.exists():
        return Check("ok", "Config file", detail="Not found: using built-in defaults.")
    try:
        load_settings(path)
    except VrecError as e:
        return Check("fail", "Config file", detail=first_line(e))
    return Check("ok", "Config file", detail=f"{path.name} is valid.")


def check_features(ctx: _Context) -> Check:
    """Feature toggles."""
    off = ctx.features.disabled()
    if not off:
        return Check("info", "Feature toggles", detail="All features enabled.")
    return Check("info", "Feature toggles", detail=f"Disabled: {', '.join(off)}.")


def check_obs_connection(ctx: _Context) -> Check:
    """OBS connection."""
    client = ctx.client()
    if client is not None:
        return Check("ok", "OBS connection", detail="Connected, password OK.")
    if ctx._client_unreachable and ctx.features.enabled("auto_start_obs"):
        return _obs_not_open_check(ctx)
    return Check(
        "fail",
        "OBS connection",
        detail=ctx._client_error or "Can't reach OBS.",
        hint="Open OBS and enable the WebSocket server (Tools > WebSocket Server Settings).",
    )


def _obs_not_open_check(ctx: _Context) -> Check:
    """OBS isn't reachable and auto_start_obs is on: report what vrec would do, without doing it."""
    if launcher.obs_running():
        return Check(
            "fail",
            "OBS connection",
            detail="OBS is open but its WebSocket server doesn't answer.",
            hint="In OBS: Tools > WebSocket Server Settings > Enable WebSocket server.",
        )
    exe = launcher.find_obs(ctx.settings)
    if exe is None:
        return Check(
            "fail",
            "OBS connection",
            detail="OBS isn't open and wasn't found.",
            hint="Open it yourself, or set [obs] path in config.toml.",
        )
    return Check("info", "OBS connection", detail=f"Not open: vrec will start it ({exe}).")


def check_obs_version(ctx: _Context) -> Check:
    """OBS / obs-websocket version."""
    client = ctx.client()
    if client is None:
        return _skip("OBS / obs-websocket version")
    version = client.get_version()
    obs_major = int(str(version.obs_version).split(".")[0])
    ws_major = int(str(version.obs_web_socket_version).split(".")[0])
    detail = f"OBS {version.obs_version}, obs-websocket {version.obs_web_socket_version}."
    if obs_major < _MIN_OBS_VERSION or ws_major < _MIN_WEBSOCKET_VERSION:
        return Check(
            "warn",
            "OBS / obs-websocket version",
            detail=detail,
            hint=f"Update OBS to {_MIN_OBS_VERSION}+ (it bundles obs-websocket {_MIN_WEBSOCKET_VERSION}+).",
        )
    return Check("ok", "OBS / obs-websocket version", detail=detail)


def check_recording_idle(ctx: _Context) -> Check:
    """Recording status."""
    client = ctx.client()
    if client is None:
        return _skip("Recording status")
    if client.get_record_status().output_active:
        return Check(
            "warn", "Recording status", detail="OBS is already recording.", hint="Stop it before starting."
        )
    return Check("ok", "Recording status", detail="Idle.")


def _profile_value(client: obs.ReqClient, category: str, name: str) -> str:
    return str(client.get_profile_parameter(category, name).parameter_value or "")


def _output_mode(client: obs.ReqClient) -> str:
    return _profile_value(client, "Output", "Mode") or "Simple"


def check_output_mode(ctx: _Context) -> Check:
    """Recording output mode."""
    client = ctx.client()
    if client is None:
        return _skip("Recording output mode")
    mode = _output_mode(client)
    if mode.lower().startswith("adv"):
        same_as_stream = _profile_value(client, "AdvOut", "RecEncoder") == "none"
    else:
        same_as_stream = _profile_value(client, "SimpleOutput", "RecQuality") == "Stream"
    if same_as_stream:
        return Check(
            "warn",
            "Recording output mode",
            detail=f'{mode} mode, recording quality set to "Same as stream".',
            hint="OBS can't pause this recording.",
        )
    return Check("ok", "Recording output mode", detail=f"{mode} mode.")


def check_recording_format(ctx: _Context) -> Check:
    """Recording format."""
    client = ctx.client()
    if client is None:
        return _skip("Recording format")
    mode = _output_mode(client)
    section = "AdvOut" if mode.lower().startswith("adv") else "SimpleOutput"
    fmt = _profile_value(client, section, "RecFormat2")
    if fmt == "mp4":
        return Check(
            "warn",
            "Recording format",
            detail="Plain mp4.",
            hint="A crash mid-recording corrupts the file; use hybrid MP4 or MKV instead.",
        )
    return Check("info", "Recording format", detail=fmt or "unknown")


def check_recording_folder(ctx: _Context) -> Check:
    """Recording folder."""
    client = ctx.client()
    if client is None:
        return _skip("Recording folder")
    folder = client.get_record_directory().record_directory
    if not Path(folder).exists():
        return Check("fail", "Recording folder", detail=f"Not found: {folder}")
    free_gb = shutil.disk_usage(folder).free / 1_000_000_000
    detail = f"{folder} ({free_gb:.1f} GB free)."
    if free_gb < _MIN_FREE_GB:
        return Check("warn", "Recording folder", detail=detail, hint="Free up some disk space.")
    return Check("ok", "Recording folder", detail=detail)


def check_resolution(ctx: _Context) -> Check:
    """Video resolution."""
    client = ctx.client()
    if client is None:
        return _skip("Video resolution")
    v = client.get_video_settings()
    detail = f"Base {v.base_width}x{v.base_height}, output {v.output_width}x{v.output_height}."
    return Check("info", "Video resolution", detail=detail)


def check_scene_capture(ctx: _Context) -> Check:
    """Display capture source."""
    client = ctx.client()
    if client is None:
        return _skip("Display capture source")
    if ctx.features.enabled("obs_scene"):
        scene = ctx.settings.obs_scene_name
        if scene not in obs_scene.scene_names(client):
            return Check(
                "info",
                "Display capture source",
                detail=f"vrec will create its scene '{scene}' with a display capture on the first run.",
            )
    else:
        scene = obs_control.current_scene(client)
    items = client.get_scene_item_list(scene).scene_items
    if any(item.get("inputKind") == "monitor_capture" for item in items):
        return Check("ok", "Display capture source", detail=f"Found in scene '{scene}'.")
    if ctx.features.enabled("obs_scene"):
        return Check(
            "info",
            "Display capture source",
            detail=f"vrec will add a display capture to its scene '{scene}'.",
        )
    return Check(
        "warn",
        "Display capture source",
        detail=f"No display capture source in scene '{scene}'.",
        hint="Add a Display Capture source to that scene.",
    )


def check_vb_cable(ctx: _Context) -> Check:
    """VB-CABLE."""
    client = ctx.client()
    if client is None:
        return _skip("VB-CABLE")
    inputs = client.get_input_list(_AUDIO_SOURCE_KIND).inputs
    if not inputs:
        return Check("info", "VB-CABLE", detail="No audio input source yet: checked on the first recording.")
    name = inputs[0]["inputName"]
    devices = client.get_input_properties_list_property_items(name, "device_id").property_items
    if any("cable output" in d["itemName"].lower() for d in devices):
        return Check("ok", "VB-CABLE", detail=f"Found on input '{name}'.")
    return Check(
        "fail",
        "VB-CABLE",
        detail=f"Not found on input '{name}'.",
        hint="Install VB-CABLE and restart the PC.",
    )


def check_chrome_debug_port(ctx: _Context) -> Check:
    """Chrome debugging port."""
    url = f"http://127.0.0.1:{ctx.settings.chrome_port}/json/version"
    if not launcher.port_listening("127.0.0.1", ctx.settings.chrome_port):
        return _chrome_not_open_check(ctx, url)
    try:
        with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 - local debug port only
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return _chrome_not_open_check(ctx, url)
    return Check("ok", "Chrome debugging port", detail=data.get("Browser", "reachable"))


def _chrome_not_open_check(ctx: _Context, url: str) -> Check:
    """Chrome's debug port doesn't answer: report what vrec would do (if auto_start_chrome is on)."""
    if not ctx.features.enabled("auto_start_chrome"):
        return Check(
            "fail", "Chrome debugging port", detail=f"Can't reach {url}", hint="run launch_chrome.bat"
        )
    exe = launcher.find_chrome(ctx.settings)
    if exe is None:
        return Check(
            "fail",
            "Chrome debugging port",
            detail=f"Can't reach {url}",
            hint="Install Chrome, or set [chrome] path in config.toml.",
        )
    profile = launcher.chrome_profile_dir(ctx.settings)
    return Check(
        "info", "Chrome debugging port", detail=f"Not open: vrec will start it ({exe}, profile {profile})."
    )


def check_virtual_screen(ctx: _Context) -> Check:
    """The screen the recording Chrome window goes to."""
    title = "Virtual screen"
    screens = display.list_screens()
    if not screens:
        return Check("info", title, detail="Screens can only be listed on Windows.")
    wanted = ctx.settings.display_screen
    if ctx.features.enabled("manage_virtual_display"):
        state = display.virtual_display_enabled()
        if state is None:
            return Check(
                "fail",
                title,
                detail="manage_virtual_display is on, but its helper isn't installed or matches no adapter.",
                hint="Run once from an administrator terminal: vrec --install-display-helper",
            )
        if not state:
            return Check("info", title, detail="Off right now: vrec turns it on for each batch.")
    screen = display.pick_screen(screens, wanted)
    if screen:
        detail = f"{screen.describe()}."
        if not ctx.features.enabled("auto_place_window"):
            detail += " auto_place_window is off: move Chrome there by hand."
        others = [s for s in screens if not s.primary]
        hint = ""
        if wanted.strip().lower() in ("", "auto") and (len(others) > 1 or screen.pixels[0] < 2560):
            hint = (
                'If this isn\'t your virtual display, set [display] screen in config.toml (e.g. "DISPLAY3").'
            )
        canvas = _obs_canvas(ctx)
        if canvas and display.smaller_than(screen, canvas):
            return Check(
                "warn",
                title,
                detail=f"{detail} OBS records {canvas[0]}x{canvas[1]}: the image will be upscaled (blurry).",
                hint="Set the virtual display's resolution to at least OBS's (Windows display settings).",
            )
        return Check("ok", title, detail=detail, hint=hint)
    return Check(
        "warn",
        title,
        detail=f'No screen matches [display] screen = "{wanted}" ({len(screens)} screen(s) found).',
        hint="Turn the virtual display on, or see docs/virtual-display.md.",
    )


def _obs_canvas(ctx: _Context) -> tuple[int, int] | None:
    """OBS's base (canvas) resolution, when OBS is reachable."""
    client = ctx.client()
    if client is None:
        return None
    try:
        video = client.get_video_settings()
        return int(video.base_width), int(video.base_height)
    except Exception:
        return None


def check_audio_routing(ctx: _Context) -> Check:
    """How the video's sound reaches VB-CABLE."""
    title = "Video sound"
    output = ctx.settings.audio_output
    if ctx.features.enabled("audio_sink"):
        return Check(
            "info",
            title,
            detail=f'Sent to "{output}" by vrec for the recorded page only (checked at each video).',
        )
    return Check(
        "info",
        title,
        detail="audio_sink is off: Chrome must be routed to CABLE Input in the Windows volume mixer.",
    )


CHECKS: list[Callable[[_Context], Check]] = [
    check_data_dir,
    check_config,
    check_features,
    check_obs_connection,
    check_obs_version,
    check_recording_idle,
    check_output_mode,
    check_recording_format,
    check_recording_folder,
    check_resolution,
    check_scene_capture,
    check_vb_cable,
    check_audio_routing,
    check_chrome_debug_port,
    check_virtual_screen,
]

_LABELS = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]", "info": "[INFO]"}


def _print_check(check: Check) -> None:
    line = _LABELS.get(check.status, "[ ?? ]") + " " + check.title
    if check.detail:
        line += f": {check.detail}"
    print(line)
    if check.hint:
        print(f"       -> {check.hint}")


def run_doctor(settings: Settings, paths: Paths, features: FeatureSet) -> int:
    """Run every registered check and print the results. Returns 1 if any check failed, else 0."""
    ctx = _Context(settings=settings, paths=paths, features=features)
    counts = {"ok": 0, "warn": 0, "fail": 0, "info": 0}
    print("===== VREC DOCTOR =====\n")
    for check_func in CHECKS:
        try:
            check = check_func(ctx)
        except Exception as e:
            check = Check("fail", (check_func.__doc__ or check_func.__name__).strip(), detail=first_line(e))
        _print_check(check)
        counts[check.status] = counts.get(check.status, 0) + 1

    print(
        f"\n{counts['ok']} OK, {counts['warn']} warning(s), {counts['fail']} failure(s), "
        f"{counts['info']} info."
    )
    return 1 if counts["fail"] else 0
