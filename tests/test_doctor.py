"""Tests for vrec.doctor: every check's ok/warn/fail/info paths, and the exit code."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from vrec import doctor, obs_control
from vrec.config import Paths, Settings
from vrec.errors import VrecError
from vrec.features import FeatureSet


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chrome's debug port is never really contacted: tests that need it install their own fake."""
    import urllib.error
    import urllib.request

    def refuse(url: str, timeout: float = 3) -> None:
        raise urllib.error.URLError("connection refused (test)")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    # Ports count as listening: each test decides through its fake client / fake HTTP answer.
    monkeypatch.setattr(doctor.launcher, "port_listening", lambda host, port, timeout=0.3: True)


class FakeClient:
    """Stand-in for obsws_python.ReqClient, covering only what doctor.py calls."""

    def __init__(
        self,
        *,
        obs_version: str = "30.0.0",
        ws_version: str = "5.3.0",
        recording: bool = False,
        mode: str = "Simple",
        simple_quality: str = "High",
        adv_encoder: str = "x264",
        rec_format: str = "hybrid_mp4",
        record_directory: str | None = None,
        scene_name: str = "Scene",
        scene_items: list[dict] | None = None,
        wasapi_inputs: list[dict] | None = None,
        device_items: list[dict] | None = None,
    ) -> None:
        self.obs_version = obs_version
        self.ws_version = ws_version
        self.recording = recording
        self.mode = mode
        self.simple_quality = simple_quality
        self.adv_encoder = adv_encoder
        self.rec_format = rec_format
        self.record_directory = record_directory
        self.scene_name = scene_name
        self.scene_items = scene_items if scene_items is not None else []
        self.wasapi_inputs = wasapi_inputs if wasapi_inputs is not None else []
        self.device_items = device_items if device_items is not None else []

    def get_version(self) -> SimpleNamespace:
        return SimpleNamespace(obs_version=self.obs_version, obs_web_socket_version=self.ws_version)

    def get_record_status(self) -> SimpleNamespace:
        return SimpleNamespace(output_active=self.recording)

    def get_profile_parameter(self, category: str, name: str) -> SimpleNamespace:
        values = {
            ("Output", "Mode"): self.mode,
            ("SimpleOutput", "RecQuality"): self.simple_quality,
            ("SimpleOutput", "RecFormat2"): self.rec_format,
            ("AdvOut", "RecEncoder"): self.adv_encoder,
            ("AdvOut", "RecFormat2"): self.rec_format,
        }
        return SimpleNamespace(parameter_value=values.get((category, name), ""))

    def get_record_directory(self) -> SimpleNamespace:
        return SimpleNamespace(record_directory=self.record_directory)

    def get_video_settings(self) -> SimpleNamespace:
        return SimpleNamespace(base_width=1920, base_height=1080, output_width=1280, output_height=720)

    def get_current_program_scene(self) -> SimpleNamespace:
        return SimpleNamespace(current_program_scene_name=self.scene_name)

    def get_scene_item_list(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(scene_items=self.scene_items)

    def get_scene_list(self) -> SimpleNamespace:
        scenes = [{"sceneName": self.scene_name}, {"sceneName": "vrec"}]
        return SimpleNamespace(scenes=scenes, current_program_scene_name=self.scene_name)

    def get_input_list(self, kind: str | None = None) -> SimpleNamespace:
        inputs = self.wasapi_inputs
        if kind is not None:
            inputs = [i for i in inputs if i.get("inputKind") == kind]
        return SimpleNamespace(inputs=inputs)

    def get_input_properties_list_property_items(self, name: str, prop: str) -> SimpleNamespace:
        return SimpleNamespace(property_items=self.device_items)


def _paths(tmp_path: Path) -> Paths:
    return Paths(data_dir=tmp_path, config=tmp_path / "config.toml")


def _ctx(tmp_path: Path, client: FakeClient | None, monkeypatch: pytest.MonkeyPatch) -> doctor._Context:
    if client is None:

        def fail_connect(settings, paths):
            raise VrecError("Can't reach OBS: open OBS and enable the WebSocket server.")

        monkeypatch.setattr(obs_control, "connect", fail_connect)
    else:
        monkeypatch.setattr(obs_control, "connect", lambda settings, paths: (client, "secret"))
    return doctor._Context(settings=Settings(), paths=_paths(tmp_path), features=FeatureSet())


# ---------------------------------------------------------------------------
# check_data_dir
# ---------------------------------------------------------------------------


def test_check_data_dir_missing_file_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, None, monkeypatch)
    check = doctor.check_data_dir(ctx)
    assert check.status == "fail"
    assert "videos.example.txt" in check.hint


def test_check_data_dir_empty_file_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, None, monkeypatch)
    ctx.paths.videos.write_text("# nothing but comments\n", encoding="utf-8")
    check = doctor.check_data_dir(ctx)
    assert check.status == "fail"


def test_check_data_dir_with_links_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, None, monkeypatch)
    ctx.paths.videos.write_text("https://example.com/a\nhttps://example.com/b\n", encoding="utf-8")
    check = doctor.check_data_dir(ctx)
    assert check.status == "ok"
    assert "2 link" in check.detail


# ---------------------------------------------------------------------------
# check_config
# ---------------------------------------------------------------------------


def test_check_config_missing_is_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, None, monkeypatch)
    check = doctor.check_config(ctx)
    assert check.status == "ok"
    assert "defaults" in check.detail


def test_check_config_valid_is_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, None, monkeypatch)
    ctx.paths.config.write_text("[obs]\nport = 1234\n", encoding="utf-8")
    check = doctor.check_config(ctx)
    assert check.status == "ok"


def test_check_config_invalid_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, None, monkeypatch)
    ctx.paths.config.write_text("[nope]\nfoo = 1\n", encoding="utf-8")
    check = doctor.check_config(ctx)
    assert check.status == "fail"
    assert "Unknown config section" in check.detail


# ---------------------------------------------------------------------------
# check_features
# ---------------------------------------------------------------------------


def test_check_features_all_enabled_is_info(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, None, monkeypatch)
    ctx.features.set("manage_virtual_display", True)  # the only feature that is off by default
    check = doctor.check_features(ctx)
    assert check.status == "info"
    assert "All features enabled" in check.detail


def test_check_features_lists_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, None, monkeypatch)
    ctx.features.set("run_logs", False)
    check = doctor.check_features(ctx)
    assert check.status == "info"
    assert "run_logs" in check.detail


# ---------------------------------------------------------------------------
# check_obs_connection / check_obs_version
# ---------------------------------------------------------------------------


def test_check_obs_connection_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, FakeClient(), monkeypatch)
    check = doctor.check_obs_connection(ctx)
    assert check.status == "ok"


def test_check_obs_connection_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, None, monkeypatch)
    check = doctor.check_obs_connection(ctx)
    assert check.status == "fail"
    assert "WebSocket" in check.hint


def _unreachable_ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> doctor._Context:
    """A context whose OBS connection fails with obs_control.ObsUnreachable specifically."""

    def fail_connect(settings: Settings, paths: Paths) -> tuple[FakeClient, str]:
        raise obs_control.ObsUnreachable("Can't reach OBS: open OBS and enable the WebSocket server.")

    monkeypatch.setattr(obs_control, "connect", fail_connect)
    return doctor._Context(settings=Settings(), paths=_paths(tmp_path), features=FeatureSet())


def test_check_obs_connection_not_open_and_found_is_info(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _unreachable_ctx(tmp_path, monkeypatch)
    monkeypatch.setattr(doctor.launcher, "obs_running", lambda: False)
    monkeypatch.setattr(doctor.launcher, "find_obs", lambda settings: Path(r"C:\obs\obs64.exe"))
    check = doctor.check_obs_connection(ctx)
    assert check.status == "info"
    assert "vrec will start it" in check.detail
    assert "obs64.exe" in check.detail


def test_check_obs_connection_open_but_not_answering_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _unreachable_ctx(tmp_path, monkeypatch)
    monkeypatch.setattr(doctor.launcher, "obs_running", lambda: True)
    monkeypatch.setattr(doctor.launcher, "find_obs", lambda settings: pytest.fail("should not look it up"))
    check = doctor.check_obs_connection(ctx)
    assert check.status == "fail"
    assert "doesn't answer" in check.detail


def test_check_obs_connection_not_found_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _unreachable_ctx(tmp_path, monkeypatch)
    monkeypatch.setattr(doctor.launcher, "obs_running", lambda: False)
    monkeypatch.setattr(doctor.launcher, "find_obs", lambda settings: None)
    check = doctor.check_obs_connection(ctx)
    assert check.status == "fail"
    assert "wasn't found" in check.detail


def test_check_obs_connection_unreachable_feature_off_uses_generic_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _unreachable_ctx(tmp_path, monkeypatch)
    ctx.features.set("auto_start_obs", False)
    monkeypatch.setattr(doctor.launcher, "obs_running", lambda: pytest.fail("should not check"))
    check = doctor.check_obs_connection(ctx)
    assert check.status == "fail"
    assert "WebSocket" in check.hint


def test_check_obs_version_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, FakeClient(obs_version="30.1.2", ws_version="5.3.0"), monkeypatch)
    check = doctor.check_obs_version(ctx)
    assert check.status == "ok"


def test_check_obs_version_warns_when_too_old(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, FakeClient(obs_version="27.0.0", ws_version="4.9.0"), monkeypatch)
    check = doctor.check_obs_version(ctx)
    assert check.status == "warn"


def test_check_obs_version_skipped_without_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path, None, monkeypatch)
    check = doctor.check_obs_version(ctx)
    assert check.status == "info"
    assert "Skipped" in check.detail


# ---------------------------------------------------------------------------
# check_recording_idle
# ---------------------------------------------------------------------------


def test_check_recording_idle_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, FakeClient(recording=False), monkeypatch)
    assert doctor.check_recording_idle(ctx).status == "ok"


def test_check_recording_idle_warns_when_active(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, FakeClient(recording=True), monkeypatch)
    assert doctor.check_recording_idle(ctx).status == "warn"


# ---------------------------------------------------------------------------
# check_output_mode
# ---------------------------------------------------------------------------


def test_check_output_mode_simple_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, FakeClient(mode="Simple", simple_quality="High"), monkeypatch)
    assert doctor.check_output_mode(ctx).status == "ok"


def test_check_output_mode_simple_same_as_stream_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path, FakeClient(mode="Simple", simple_quality="Stream"), monkeypatch)
    check = doctor.check_output_mode(ctx)
    assert check.status == "warn"
    assert "pause" in check.hint


def test_check_output_mode_advanced_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, FakeClient(mode="Advanced", adv_encoder="x264"), monkeypatch)
    assert doctor.check_output_mode(ctx).status == "ok"


def test_check_output_mode_advanced_none_encoder_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path, FakeClient(mode="Advanced", adv_encoder="none"), monkeypatch)
    assert doctor.check_output_mode(ctx).status == "warn"


# ---------------------------------------------------------------------------
# check_recording_format
# ---------------------------------------------------------------------------


def test_check_recording_format_plain_mp4_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, FakeClient(mode="Simple", rec_format="mp4"), monkeypatch)
    check = doctor.check_recording_format(ctx)
    assert check.status == "warn"
    assert "MKV" in check.hint


def test_check_recording_format_hybrid_is_info(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, FakeClient(mode="Simple", rec_format="hybrid_mp4"), monkeypatch)
    check = doctor.check_recording_format(ctx)
    assert check.status == "info"


# ---------------------------------------------------------------------------
# check_recording_folder
# ---------------------------------------------------------------------------


def test_check_recording_folder_missing_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, FakeClient(record_directory=str(tmp_path / "nope")), monkeypatch)
    check = doctor.check_recording_folder(ctx)
    assert check.status == "fail"


def test_check_recording_folder_low_space_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, FakeClient(record_directory=str(tmp_path)), monkeypatch)
    monkeypatch.setattr(
        shutil, "disk_usage", lambda path: SimpleNamespace(total=0, used=0, free=1_000_000_000)
    )
    check = doctor.check_recording_folder(ctx)
    assert check.status == "warn"


def test_check_recording_folder_enough_space_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, FakeClient(record_directory=str(tmp_path)), monkeypatch)
    monkeypatch.setattr(
        shutil, "disk_usage", lambda path: SimpleNamespace(total=0, used=0, free=100_000_000_000)
    )
    check = doctor.check_recording_folder(ctx)
    assert check.status == "ok"


# ---------------------------------------------------------------------------
# check_resolution
# ---------------------------------------------------------------------------


def test_check_resolution_is_info(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, FakeClient(), monkeypatch)
    check = doctor.check_resolution(ctx)
    assert check.status == "info"
    assert "1920x1080" in check.detail


# ---------------------------------------------------------------------------
# check_scene_capture
# ---------------------------------------------------------------------------


def test_check_scene_capture_found_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(
        tmp_path,
        FakeClient(scene_items=[{"inputKind": "monitor_capture", "sourceName": "Display"}]),
        monkeypatch,
    )
    assert doctor.check_scene_capture(ctx).status == "ok"


def test_check_scene_capture_missing_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, FakeClient(scene_items=[{"inputKind": "browser_source"}]), monkeypatch)
    ctx.features.set("obs_scene", False)  # the user's own scene must hold the capture
    assert doctor.check_scene_capture(ctx).status == "warn"


def test_check_scene_capture_missing_in_own_scene_is_added(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path, FakeClient(scene_items=[{"inputKind": "browser_source"}]), monkeypatch)
    check = doctor.check_scene_capture(ctx)
    assert check.status == "info" and "add a display capture" in check.detail


# ---------------------------------------------------------------------------
# check_vb_cable
# ---------------------------------------------------------------------------


def test_check_vb_cable_found_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(
        tmp_path,
        FakeClient(
            wasapi_inputs=[{"inputName": "Chrome Audio", "inputKind": "wasapi_input_capture"}],
            device_items=[{"itemName": "CABLE Output (VB-Audio Virtual Cable)", "itemValue": "cable"}],
        ),
        monkeypatch,
    )
    assert doctor.check_vb_cable(ctx).status == "ok"


def test_check_vb_cable_not_installed_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(
        tmp_path,
        FakeClient(
            wasapi_inputs=[{"inputName": "Mic", "inputKind": "wasapi_input_capture"}],
            device_items=[{"itemName": "Default", "itemValue": "default"}],
        ),
        monkeypatch,
    )
    check = doctor.check_vb_cable(ctx)
    assert check.status == "fail"
    assert "VB-CABLE" in check.hint


def test_check_vb_cable_no_input_yet_is_info(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, FakeClient(wasapi_inputs=[]), monkeypatch)
    check = doctor.check_vb_cable(ctx)
    assert check.status == "info"
    assert "first recording" in check.detail


# ---------------------------------------------------------------------------
# check_chrome_debug_port
# ---------------------------------------------------------------------------


class _FakeHttpResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _FakeHttpResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_check_chrome_debug_port_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    ctx = _ctx(tmp_path, None, monkeypatch)
    payload = b'{"Browser": "Chrome/120.0"}'
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=3: _FakeHttpResponse(payload))
    check = doctor.check_chrome_debug_port(ctx)
    assert check.status == "ok"
    assert check.detail == "Chrome/120.0"


def test_check_chrome_debug_port_fails_when_feature_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.request

    def boom(url, timeout=3):
        raise OSError("connection refused")

    ctx = _ctx(tmp_path, None, monkeypatch)
    ctx.features.set("auto_start_chrome", False)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    check = doctor.check_chrome_debug_port(ctx)
    assert check.status == "fail"
    assert check.hint == "run launch_chrome.bat"


def test_check_chrome_debug_port_closed_and_found_is_info(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.request

    def boom(url, timeout=3):
        raise OSError("connection refused")

    ctx = _ctx(tmp_path, None, monkeypatch)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(doctor.launcher, "find_chrome", lambda settings: Path(r"C:\chrome\chrome.exe"))
    monkeypatch.setattr(doctor.launcher, "chrome_profile_dir", lambda settings: Path(r"C:\profile"))
    check = doctor.check_chrome_debug_port(ctx)
    assert check.status == "info"
    assert "vrec will start it" in check.detail
    assert "chrome.exe" in check.detail
    assert "profile" in check.detail


def test_check_chrome_debug_port_closed_and_not_found_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.request

    def boom(url, timeout=3):
        raise OSError("connection refused")

    ctx = _ctx(tmp_path, None, monkeypatch)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(doctor.launcher, "find_chrome", lambda settings: None)
    check = doctor.check_chrome_debug_port(ctx)
    assert check.status == "fail"
    assert "Install Chrome" in check.hint


# ---------------------------------------------------------------------------
# run_doctor: exit code and overall output
# ---------------------------------------------------------------------------


def _passing_client(tmp_path: Path) -> FakeClient:
    return FakeClient(
        record_directory=str(tmp_path),
        scene_items=[{"inputKind": "monitor_capture"}],
        wasapi_inputs=[],
    )


def test_run_doctor_returns_zero_when_nothing_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import urllib.request

    monkeypatch.setattr(obs_control, "connect", lambda settings, paths: (_passing_client(tmp_path), "pw"))
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda url, timeout=3: _FakeHttpResponse(b'{"Browser": "Chrome"}')
    )
    paths = _paths(tmp_path)
    paths.videos.write_text("https://example.com/a\n", encoding="utf-8")

    code = doctor.run_doctor(Settings(), paths, FeatureSet())

    assert code == 0
    out = capsys.readouterr().out
    assert "[FAIL]" not in out
    assert "failure(s)" in out


def test_run_doctor_returns_one_when_a_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(obs_control, "connect", lambda settings, paths: (_passing_client(tmp_path), "pw"))
    monkeypatch.setattr(doctor.launcher, "find_chrome", lambda settings: None)  # no real filesystem/registry
    paths = _paths(tmp_path)
    # videos.txt is left missing on purpose: the data-dir check must fail.

    code = doctor.run_doctor(Settings(), paths, FeatureSet())

    assert code == 1
    assert "[FAIL]" in capsys.readouterr().out


def test_run_doctor_reports_unexpected_exception_as_a_failed_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class ExplodingClient(FakeClient):
        def get_version(self):
            raise RuntimeError("kaboom")

    monkeypatch.setattr(
        obs_control,
        "connect",
        lambda settings, paths: (ExplodingClient(record_directory=str(tmp_path)), "pw"),
    )
    monkeypatch.setattr(doctor.launcher, "find_chrome", lambda settings: None)  # no real filesystem/registry
    paths = _paths(tmp_path)
    paths.videos.write_text("https://example.com/a\n", encoding="utf-8")

    code = doctor.run_doctor(Settings(), paths, FeatureSet())

    assert code == 1
    assert "kaboom" in capsys.readouterr().out


# check_virtual_screen / check_audio_routing

_MAIN = doctor.display.Screen("DISPLAY1", 0, 0, 2560, 1440, primary=True)
_VIRTUAL = doctor.display.Screen("DISPLAY3", 2560, 0, 3840, 2160, primary=False)


def test_virtual_screen_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.display, "list_screens", lambda: [_MAIN, _VIRTUAL])
    check = doctor.check_virtual_screen(_ctx(tmp_path, None, monkeypatch))
    assert check.status == "ok"
    assert "DISPLAY3" in check.detail


def test_virtual_screen_missing_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.display, "list_screens", lambda: [_MAIN])
    check = doctor.check_virtual_screen(_ctx(tmp_path, None, monkeypatch))
    assert check.status == "warn"


def test_virtual_screen_managed_needs_the_helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.display, "list_screens", lambda: [_MAIN])
    ctx = _ctx(tmp_path, None, monkeypatch)
    ctx.features.set("manage_virtual_display", True)
    monkeypatch.setattr(doctor.display, "virtual_display_enabled", lambda: None)
    assert doctor.check_virtual_screen(ctx).status == "fail"
    monkeypatch.setattr(doctor.display, "virtual_display_enabled", lambda: False)
    assert doctor.check_virtual_screen(ctx).status == "info"
    monkeypatch.setattr(doctor.display, "virtual_display_enabled", lambda: True)
    monkeypatch.setattr(doctor.display, "list_screens", lambda: [_MAIN, _VIRTUAL])
    assert doctor.check_virtual_screen(ctx).status == "ok"


def test_virtual_screen_auto_on_a_small_side_monitor_gives_a_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    side = doctor.display.Screen("DISPLAY2", -1920, 0, 1920, 1080, primary=False)
    monkeypatch.setattr(doctor.display, "list_screens", lambda: [_MAIN, side])
    check = doctor.check_virtual_screen(_ctx(tmp_path, None, monkeypatch))
    assert check.status == "ok"
    assert "[display] screen" in check.hint


def test_audio_routing_info(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, None, monkeypatch)
    assert "CABLE Input" in doctor.check_audio_routing(ctx).detail
    ctx.features.set("audio_sink", False)
    assert "volume mixer" in doctor.check_audio_routing(ctx).detail


# check_scene_capture with vrec's own scene (obs_scene feature)


def test_scene_capture_own_scene_missing_is_info(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, _passing_client(tmp_path), monkeypatch)
    monkeypatch.setattr(doctor.obs_scene, "scene_names", lambda client: ["Main"])
    check = doctor.check_scene_capture(ctx)
    assert check.status == "info" and "create its scene 'vrec'" in check.detail


def test_scene_capture_feature_off_checks_the_program_scene(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path, _passing_client(tmp_path), monkeypatch)
    ctx.features.set("obs_scene", False)
    monkeypatch.setattr(doctor.obs_scene, "scene_names", lambda client: pytest.fail("not needed"))
    assert doctor.check_scene_capture(ctx).status in ("ok", "warn")


def test_closed_ports_are_detected_without_connecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doctor.launcher, "port_listening", lambda host, port, timeout=0.3: False)
    monkeypatch.setattr(
        obs_control, "connect", lambda s, p: pytest.fail("no connection attempt on a closed port")
    )
    ctx = doctor._Context(
        settings=Settings(), paths=_paths(tmp_path), features=FeatureSet({"auto_start_obs": False})
    )
    assert ctx.client() is None
    assert "Can't reach OBS" in (ctx._client_error or "")
    assert doctor.check_chrome_debug_port(ctx).status in ("fail", "info")
