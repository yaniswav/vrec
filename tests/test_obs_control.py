"""Tests for vrec.obs_control: audio routing/mute restore, meter, and black-frame check."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from obsws_python.error import OBSSDKError, OBSSDKTimeoutError
from PIL import Image

from vrec import obs_control
from vrec.config import Paths, Settings
from vrec.errors import VrecError
from vrec.obs_control import (
    _AUDIO_SOURCE_KIND,
    AudioMeter,
    ObsUnreachable,
    changed_fraction,
    frame_signature,
    is_black_frame,
    prepare_audio,
    restore_mutes,
)

SCENE = "Scene"
SOURCE_NAME = "Chrome Audio (VB-CABLE)"
CABLE_DEVICE_ID = "cable-output-id"


def _png_data_url(color: int) -> str:
    """A tiny solid-color PNG, as a base64 data URL like OBS's screenshot response."""
    image = Image.new("RGB", (64, 36), (color, color, color))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


class FakeObsClient:
    """Stand-in for obsws_python.ReqClient, covering only what obs_control calls."""

    def __init__(
        self,
        inputs: list[dict],
        device_items: list[dict],
        mute_states: dict[str, bool],
        settings: dict[str, dict],
        restore_file: Path | None = None,
        fail_on_mute: frozenset[str] = frozenset(),
    ) -> None:
        self.all_inputs = inputs
        self.device_items = device_items
        self.mute_states = dict(mute_states)
        self.settings = {name: dict(s) for name, s in settings.items()}
        self.restore_file = restore_file
        self.fail_on_mute = fail_on_mute
        self.mute_calls: list[tuple[str, bool]] = []
        self.restore_file_existed_before_first_mute: bool | None = None
        self.monitor_types: dict[str, str] = {}
        self.created: list[str] = []
        self.screenshot_data: str | None = None
        self.screenshot_should_fail = False

    def get_input_list(self, kind: str | None = None) -> SimpleNamespace:
        if kind is None:
            return SimpleNamespace(inputs=self.all_inputs)
        return SimpleNamespace(inputs=[i for i in self.all_inputs if i.get("inputKind") == kind])

    def create_input(self, scene, name, kind, input_settings, enabled) -> None:
        self.all_inputs.append({"inputName": name, "inputKind": kind})
        self.settings[name] = dict(input_settings)
        self.created.append(name)

    def get_input_properties_list_property_items(self, name: str, prop: str) -> SimpleNamespace:
        return SimpleNamespace(property_items=self.device_items)

    def get_input_settings(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(input_settings=self.settings.get(name, {}))

    def set_input_settings(self, name: str, input_settings: dict, overlay: bool) -> None:
        self.settings.setdefault(name, {}).update(input_settings)

    def get_input_mute(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(input_muted=self.mute_states.get(name, False))

    def set_input_mute(self, name: str, muted: bool) -> None:
        if self.restore_file_existed_before_first_mute is None:
            self.restore_file_existed_before_first_mute = (
                self.restore_file.exists() if self.restore_file else False
            )
        self.mute_calls.append((name, muted))
        if name in self.fail_on_mute:
            raise RuntimeError(f"could not mute {name}")
        self.mute_states[name] = muted

    def set_input_audio_monitor_type(self, name: str, monitor_type: str) -> None:
        self.monitor_types[name] = monitor_type

    def get_source_screenshot(self, scene, fmt, width, height, quality) -> SimpleNamespace:
        if self.screenshot_should_fail:
            raise RuntimeError("screenshot failed")
        return SimpleNamespace(image_data=self.screenshot_data)


def _happy_path_client(restore_file: Path, fail_on_mute: frozenset[str] = frozenset()) -> FakeObsClient:
    inputs = [
        {"inputName": SOURCE_NAME, "inputKind": _AUDIO_SOURCE_KIND},
        {"inputName": "Mic/Aux", "inputKind": _AUDIO_SOURCE_KIND},
        {"inputName": "Some Browser Source", "inputKind": "browser_source"},
    ]
    device_items = [
        {"itemName": "Default", "itemValue": "default"},
        {"itemName": "CABLE Output (VB-Audio Virtual Cable)", "itemValue": CABLE_DEVICE_ID},
    ]
    settings = {
        SOURCE_NAME: {"device_id": CABLE_DEVICE_ID},
        "Mic/Aux": {"device_id": "mic-device-id"},
    }
    mute_states = {SOURCE_NAME: True, "Mic/Aux": False}
    return FakeObsClient(
        inputs, device_items, mute_states, settings, restore_file=restore_file, fail_on_mute=fail_on_mute
    )


def test_prepare_audio_happy_path(tmp_path: Path) -> None:
    restore_file = tmp_path / "obs_restore.json"
    client = _happy_path_client(restore_file)
    mutes: dict[str, bool] = {}

    source = prepare_audio(client, SCENE, Settings(), mutes, restore_file)

    assert source == SOURCE_NAME
    # Original mute states captured before anything changed.
    assert mutes == {SOURCE_NAME: True, "Mic/Aux": False}
    # The chosen source ends up unmuted and not monitored...
    assert client.mute_states[SOURCE_NAME] is False
    assert client.monitor_types[SOURCE_NAME] == "OBS_MONITORING_TYPE_NONE"
    # ...while every other wasapi source ends up muted.
    assert client.mute_states["Mic/Aux"] is True
    # A source of a different kind is left alone entirely.
    assert all(name != "Some Browser Source" for name, _ in client.mute_calls)

    # The restore file was written before the first mute call, so a crash mid-way
    # still leaves the pre-change state recoverable on disk.
    assert client.restore_file_existed_before_first_mute is True
    on_disk = json.loads(restore_file.read_text(encoding="utf-8"))
    assert on_disk == {SOURCE_NAME: True, "Mic/Aux": False}


def test_prepare_audio_missing_cable_raises(tmp_path: Path) -> None:
    restore_file = tmp_path / "obs_restore.json"
    inputs = [{"inputName": SOURCE_NAME, "inputKind": _AUDIO_SOURCE_KIND}]
    device_items = [{"itemName": "Default", "itemValue": "default"}]  # no CABLE Output
    client = FakeObsClient(inputs, device_items, {}, {SOURCE_NAME: {}}, restore_file=restore_file)

    with pytest.raises(RuntimeError, match="VB-CABLE"):
        prepare_audio(client, SCENE, Settings(), {}, restore_file)


def test_prepare_audio_failure_during_muting_leaves_full_restore_file(tmp_path: Path) -> None:
    restore_file = tmp_path / "obs_restore.json"
    client = _happy_path_client(restore_file, fail_on_mute=frozenset({"Mic/Aux"}))
    mutes: dict[str, bool] = {}

    with pytest.raises(RuntimeError, match="Mic/Aux"):
        prepare_audio(client, SCENE, Settings(), mutes, restore_file)

    # Even though muting "Mic/Aux" blew up, the restore file already holds the
    # complete original state (it was written before any mute call was made).
    on_disk = json.loads(restore_file.read_text(encoding="utf-8"))
    assert on_disk == {SOURCE_NAME: True, "Mic/Aux": False}


def test_restore_mutes_deletes_file_after_full_restore(tmp_path: Path) -> None:
    restore_file = tmp_path / "obs_restore.json"
    restore_file.write_text(json.dumps({SOURCE_NAME: True, "Mic/Aux": False}), encoding="utf-8")
    client = FakeObsClient([], [], {}, {})

    restore_mutes(client, {SOURCE_NAME: True, "Mic/Aux": False}, restore_file)

    assert client.mute_states == {SOURCE_NAME: True, "Mic/Aux": False}
    assert not restore_file.exists()


def test_restore_mutes_keeps_file_when_a_restore_fails(tmp_path: Path) -> None:
    restore_file = tmp_path / "obs_restore.json"
    restore_file.write_text(json.dumps({SOURCE_NAME: True, "Mic/Aux": False}), encoding="utf-8")
    client = FakeObsClient([], [], {}, {}, fail_on_mute=frozenset({"Mic/Aux"}))

    restore_mutes(client, {SOURCE_NAME: True, "Mic/Aux": False}, restore_file)

    assert client.mute_states.get(SOURCE_NAME) is True
    assert restore_file.exists()


def test_audio_meter_tracks_peak_of_named_source() -> None:
    meter = AudioMeter(source=SOURCE_NAME)
    data = SimpleNamespace(
        inputs=[
            {"inputName": "Other Source", "inputLevelsMul": [[0.9, 0.9, 0.9]]},
            {"inputName": SOURCE_NAME, "inputLevelsMul": [[0.1, 0.2, 0.05], [0.01, 0.5, 0.01]]},
        ]
    )
    meter.on_input_volume_meters(data)
    assert meter.peak == 0.5


def _meters(peak_second_value: float) -> SimpleNamespace:
    return SimpleNamespace(inputs=[{"inputName": SOURCE_NAME, "inputLevelsMul": [[0, peak_second_value]]}])


def test_audio_meter_peak_keeps_max_across_calls() -> None:
    meter = AudioMeter(source=SOURCE_NAME)
    meter.on_input_volume_meters(_meters(0.7))
    meter.on_input_volume_meters(_meters(0.2))
    assert meter.peak == 0.7


def test_audio_meter_reset() -> None:
    meter = AudioMeter(source=SOURCE_NAME)
    meter.on_input_volume_meters(_meters(0.7))
    meter.reset()
    assert meter.peak == 0.0


def test_audio_meter_ignores_channels_without_second_value() -> None:
    meter = AudioMeter(source=SOURCE_NAME)
    data = SimpleNamespace(inputs=[{"inputName": SOURCE_NAME, "inputLevelsMul": [[0.9]]}])
    meter.on_input_volume_meters(data)
    assert meter.peak == 0.0


def test_is_black_frame_true_for_black_image() -> None:
    client = FakeObsClient([], [], {}, {})
    client.screenshot_data = _png_data_url(0)
    assert is_black_frame(client, SCENE, Settings()) is True


def test_is_black_frame_false_for_white_image() -> None:
    client = FakeObsClient([], [], {}, {})
    client.screenshot_data = _png_data_url(255)
    assert is_black_frame(client, SCENE, Settings()) is False


def test_is_black_frame_none_on_exception() -> None:
    client = FakeObsClient([], [], {}, {})
    client.screenshot_should_fail = True
    assert is_black_frame(client, SCENE, Settings()) is None


# ---------------------------------------------------------------------------
# wait_until_ready: OBS accepts connections before it has finished loading
# ---------------------------------------------------------------------------


class _LoadingObs:
    def __init__(self, not_ready_times: int, code: int = 207) -> None:
        self.left, self.code, self.calls = not_ready_times, code, 0

    def get_record_status(self) -> object:
        from obsws_python.error import OBSSDKRequestError

        self.calls += 1
        if self.left:
            self.left -= 1
            raise OBSSDKRequestError("GetRecordStatus", self.code, "OBS is not ready to perform the request.")
        return object()


def test_wait_until_ready_waits_while_obs_loads(capsys: pytest.CaptureFixture[str]) -> None:
    client = _LoadingObs(not_ready_times=3)
    sleeps: list[float] = []
    obs_control.wait_until_ready(client, 60, sleep=sleeps.append, clock=lambda: 0.0)  # type: ignore[arg-type]
    assert client.calls == 4 and sleeps == [1, 1, 1]
    assert capsys.readouterr().out.count("Waiting for OBS to finish loading...") == 1


def test_wait_until_ready_returns_at_once_when_ready() -> None:
    client = _LoadingObs(not_ready_times=0)
    obs_control.wait_until_ready(client, 60, sleep=lambda s: pytest.fail("no wait"), clock=lambda: 0.0)  # type: ignore[arg-type]
    assert client.calls == 1


def test_wait_until_ready_gives_up() -> None:
    times = iter([0.0, 10.0, 70.0])
    with pytest.raises(VrecError, match="still loading after 60 s"):
        obs_control.wait_until_ready(
            _LoadingObs(not_ready_times=99),
            60,
            sleep=lambda s: None,
            clock=lambda: next(times),  # type: ignore[arg-type]
        )


def test_wait_until_ready_lets_other_errors_through() -> None:
    from obsws_python.error import OBSSDKRequestError

    with pytest.raises(OBSSDKRequestError):
        obs_control.wait_until_ready(
            _LoadingObs(not_ready_times=1, code=500),
            60,
            sleep=lambda s: None,
            clock=lambda: 0.0,  # type: ignore[arg-type]
        )


# connect: only a real authentication failure counts as a wrong password


def _connect_with(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception, env: str = "") -> Paths:
    paths = Paths(data_dir=tmp_path, config=tmp_path / "config.toml")
    paths.password.write_text("secret", encoding="utf-8")
    if env:
        monkeypatch.setenv("VREC_OBS_PASSWORD", env)
    else:
        monkeypatch.delenv("VREC_OBS_PASSWORD", raising=False)

    def fake_client(**_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(obs_control.obs, "ReqClient", fake_client)
    return paths


@pytest.mark.parametrize(
    "error", [TimeoutError(), ConnectionRefusedError(), OSError("reset"), OBSSDKTimeoutError("t")]
)
def test_connect_keeps_the_password_when_obs_is_unreachable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception
) -> None:
    paths = _connect_with(monkeypatch, tmp_path, error)
    with pytest.raises(ObsUnreachable):
        obs_control.connect(Settings(), paths)
    assert paths.password.exists()


def test_connect_deletes_the_password_on_auth_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = _connect_with(monkeypatch, tmp_path, OBSSDKError("auth"))
    with pytest.raises(VrecError, match="password is probably wrong") as info:
        obs_control.connect(Settings(), paths)
    assert not isinstance(info.value, ObsUnreachable)
    assert not paths.password.exists()


def test_connect_never_deletes_when_the_password_comes_from_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = _connect_with(monkeypatch, tmp_path, OBSSDKError("auth"), env="fromenv")
    with pytest.raises(VrecError, match="password is probably wrong"):
        obs_control.connect(Settings(), paths)
    assert paths.password.exists()


def test_get_password_non_interactive_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("VREC_OBS_PASSWORD", raising=False)
    monkeypatch.setattr(obs_control.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    paths = Paths(data_dir=tmp_path, config=tmp_path / "config.toml")
    with pytest.raises(VrecError, match="VREC_OBS_PASSWORD"):
        obs_control.get_password(paths)
    assert not paths.password.exists()


def test_get_password_prompts_without_echo_and_saves(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("VREC_OBS_PASSWORD", raising=False)
    monkeypatch.setattr(obs_control.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(obs_control.getpass, "getpass", lambda prompt="": " hunter2 ")
    paths = Paths(data_dir=tmp_path, config=tmp_path / "config.toml")
    assert obs_control.get_password(paths) == ("hunter2", False)
    assert paths.password.read_text(encoding="utf-8") == "hunter2"


def test_get_password_getpass_failure_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def broken(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.delenv("VREC_OBS_PASSWORD", raising=False)
    monkeypatch.setattr(obs_control.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(obs_control.getpass, "getpass", broken)
    with pytest.raises(VrecError, match="No OBS WebSocket password saved"):
        obs_control.get_password(Paths(data_dir=tmp_path, config=tmp_path / "config.toml"))


# stop_if_recording


class _StopClient:
    def __init__(self, status: object = True, stop: object = "C:/v.mkv") -> None:
        self.status, self.stop = status, stop
        self.stop_calls = 0

    def get_record_status(self) -> SimpleNamespace:
        if isinstance(self.status, Exception):
            raise self.status
        return SimpleNamespace(output_active=self.status)

    def stop_record(self) -> SimpleNamespace:
        self.stop_calls += 1
        if isinstance(self.stop, Exception):
            raise self.stop
        return SimpleNamespace(output_path=self.stop)


def test_stop_if_recording_returns_the_path() -> None:
    assert obs_control.stop_if_recording(_StopClient()) == "C:/v.mkv"  # type: ignore[arg-type]


def test_stop_if_recording_idle_does_nothing() -> None:
    client = _StopClient(status=False)
    assert obs_control.stop_if_recording(client) is None  # type: ignore[arg-type]
    assert client.stop_calls == 0


def test_stop_if_recording_warns_when_stopping_fails(capsys: pytest.CaptureFixture[str]) -> None:
    client = _StopClient(stop=RuntimeError("boom\nmore"))
    assert obs_control.stop_if_recording(client) is None  # type: ignore[arg-type]
    assert "Couldn't stop the OBS recording: boom. Stop it in OBS." in capsys.readouterr().out


def test_stop_if_recording_still_stops_after_a_status_timeout() -> None:
    client = _StopClient(status=OBSSDKTimeoutError("slow"))
    assert obs_control.stop_if_recording(client) == "C:/v.mkv"  # type: ignore[arg-type]
    assert client.stop_calls == 1


def test_stop_if_recording_warns_when_status_and_stop_both_fail(capsys: pytest.CaptureFixture[str]) -> None:
    client = _StopClient(status=TimeoutError("slow"), stop=OBSSDKTimeoutError("slow too"))
    assert obs_control.stop_if_recording(client) is None  # type: ignore[arg-type]
    assert client.stop_calls == 1
    assert "Couldn't stop the OBS recording" in capsys.readouterr().out


def test_frame_signature_is_a_grayscale_byte_per_pixel() -> None:
    client = FakeObsClient([], [], {}, {})
    client.screenshot_data = _png_data_url(200)
    sig = frame_signature(client, SCENE)
    assert sig == bytes([200]) * (64 * 36)


def test_frame_signature_none_on_failure() -> None:
    client = FakeObsClient([], [], {}, {})
    client.screenshot_should_fail = True
    assert frame_signature(client, SCENE) is None
    client.screenshot_should_fail = False
    client.screenshot_data = "not an image"
    assert frame_signature(client, SCENE) is None


def test_changed_fraction_identical_images() -> None:
    a = bytes(range(100))
    assert changed_fraction(a, a) == 0.0


def test_changed_fraction_ignores_small_noise() -> None:
    a = bytes([100] * 100)
    assert changed_fraction(a, bytes([108] * 100)) == 0.0
    assert changed_fraction(a, bytes([111] * 100)) == 1.0


def test_changed_fraction_counts_a_moving_region() -> None:
    a = bytes([50] * 100)
    b = bytes([50] * 90 + [200] * 10)
    assert changed_fraction(a, b) == pytest.approx(0.1)


def test_changed_fraction_size_mismatch_counts_as_changed() -> None:
    assert changed_fraction(b"\x00" * 10, b"\x00" * 12) == 1.0
    assert changed_fraction(b"", b"") == 1.0
