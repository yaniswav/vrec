"""vrec hides the other captures of its own OBS scene while recording, and shows them again."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_capture_mode import WindowObs

from vrec import app, doctor, obs_scene, preflight
from vrec.config import Paths, Settings
from vrec.features import FeatureSet


def make_obs() -> WindowObs:
    fake = WindowObs()
    fake.add("vrec", "vrec screen", "monitor_capture")
    fake.add("vrec", "chrome fenetre", "window_capture")
    return fake


def make_batch(tmp_path: Path, client: WindowObs, **features: bool) -> app.Batch:
    return app.Batch(
        paths=Paths(data_dir=tmp_path, config=tmp_path / "config.toml"),
        settings=Settings(),
        features=FeatureSet(features),
        test_mode=False,
        client=client,  # type: ignore[arg-type]
        scene="Main",
    )


def enabled(fake: WindowObs, source: str) -> bool:
    return next(i["sceneItemEnabled"] for i in fake.scenes["vrec"] if i["sourceName"] == source)


@pytest.fixture(autouse=True)
def on_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.obs_control, "current_scene", lambda client: getattr(client, "program", "Main"))


def test_other_captures_are_hidden_then_shown_again(tmp_path, capsys):
    fake = make_obs()
    batch = make_batch(tmp_path, fake)
    app._prepare_scene(batch)
    assert not enabled(fake, "chrome fenetre") and enabled(fake, "vrec screen")
    out = capsys.readouterr().out
    assert "Hid 'chrome fenetre' in the OBS scene 'vrec' while recording (shown again afterwards)." in out
    marker = json.loads(batch.paths.obs_restore_sources.read_text(encoding="utf-8"))
    assert marker["scene"] == "vrec" and marker["items"][0]["source"] == "chrome fenetre"
    app._restore_scene(batch)
    assert enabled(fake, "chrome fenetre")
    assert fake.program == "Main"
    assert not batch.paths.obs_restore_sources.exists()


def test_marker_is_written_before_hiding(tmp_path):
    fake = make_obs()
    batch = make_batch(tmp_path, fake)
    seen: list[bool] = []
    original = fake.set_scene_item_enabled

    def spy(scene: str, item_id: int, on: bool) -> None:
        seen.append(batch.paths.obs_restore_sources.exists())
        original(scene, item_id, on)

    fake.set_scene_item_enabled = spy  # type: ignore[method-assign]
    app._prepare_scene(batch)
    assert seen == [True]


def test_restore_runs_when_the_batch_is_interrupted(tmp_path):
    fake = make_obs()
    batch = make_batch(tmp_path, fake)
    app._prepare_scene(batch)
    # Same shape as run(): the error propagates, the finally block restores.
    with pytest.raises(KeyboardInterrupt):
        try:
            raise KeyboardInterrupt
        finally:
            app._restore_scene(batch)
    assert enabled(fake, "chrome fenetre") and fake.program == "Main"


def test_a_hiding_error_midway_still_restores(tmp_path):
    fake = make_obs()
    fake.add("vrec", "game", "game_capture")
    batch = make_batch(tmp_path, fake)
    original = fake.set_scene_item_enabled
    calls: list[int] = []

    def flaky(scene: str, item_id: int, on: bool) -> None:
        calls.append(item_id)
        if len(calls) == 2 and not on:
            raise RuntimeError("OBS went away")
        original(scene, item_id, on)

    fake.set_scene_item_enabled = flaky  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        app._prepare_scene(batch)
    app._restore_scene(batch)
    assert enabled(fake, "chrome fenetre") and enabled(fake, "game")
    assert not batch.paths.obs_restore_sources.exists()


def test_a_restore_error_warns_and_keeps_the_marker(tmp_path, capsys):
    fake = make_obs()
    batch = make_batch(tmp_path, fake)
    app._prepare_scene(batch)

    def broken(*args: Any) -> None:
        raise RuntimeError("boom")

    fake.set_scene_item_enabled = broken  # type: ignore[method-assign]
    app._restore_scene(batch)
    assert batch.paths.obs_restore_sources.exists()
    assert fake.program == "Main"  # the scene switch-back still happens
    assert "Couldn't show the sources hidden in the OBS scene 'vrec' again: boom" in capsys.readouterr().out


def test_nothing_is_hidden_with_obs_scene_off(tmp_path):
    fake = make_obs()
    batch = make_batch(tmp_path, fake, obs_scene=False)
    app._prepare_scene(batch)
    app._restore_scene(batch)
    assert enabled(fake, "chrome fenetre") and fake.enabled_calls == []
    assert not batch.paths.obs_restore_sources.exists()


def write_marker(path: Path, items: list[dict[str, Any]], scene: str = "vrec") -> None:
    path.write_text(json.dumps({"scene": scene, "items": items}), encoding="utf-8")


def test_leftover_marker_is_applied_at_startup(tmp_path, capsys):
    fake = make_obs()
    item = next(i for i in fake.scenes["vrec"] if i["sourceName"] == "chrome fenetre")
    fake.set_scene_item_enabled("vrec", item["sceneItemId"], False)
    marker = tmp_path / "m.json"
    write_marker(marker, [{"source": "chrome fenetre", "id": item["sceneItemId"]}])
    app._restore_leftover_sources(fake, marker)  # type: ignore[arg-type]
    assert enabled(fake, "chrome fenetre") and not marker.exists()
    out = capsys.readouterr().out
    assert "Showed again 'chrome fenetre' in the OBS scene 'vrec', hidden by an interrupted run." in out


def test_leftover_marker_refinds_by_name_and_skips_missing(tmp_path, capsys):
    fake = make_obs()
    item = next(i for i in fake.scenes["vrec"] if i["sourceName"] == "chrome fenetre")
    fake.set_scene_item_enabled("vrec", item["sceneItemId"], False)
    marker = tmp_path / "m.json"
    write_marker(marker, [{"source": "chrome fenetre", "id": 999}, {"source": "gone", "id": 5}])
    app._restore_leftover_sources(fake, marker)  # type: ignore[arg-type]
    assert enabled(fake, "chrome fenetre") and not marker.exists()
    out = capsys.readouterr().out
    assert "'chrome fenetre'" in out and "gone" not in out


def test_leftover_marker_for_a_deleted_scene_or_garbage_is_dropped(tmp_path):
    fake = make_obs()
    marker = tmp_path / "m.json"
    write_marker(marker, [{"source": "x", "id": 1}], scene="Deleted")
    app._restore_leftover_sources(fake, marker)  # type: ignore[arg-type]
    assert not marker.exists()
    marker.write_text("not json", encoding="utf-8")
    app._restore_leftover_sources(fake, marker)  # type: ignore[arg-type]
    assert not marker.exists()


def test_leftover_marker_keeps_the_file_on_an_obs_error(tmp_path, capsys):
    fake = make_obs()
    marker = tmp_path / "m.json"
    write_marker(marker, [{"source": "chrome fenetre", "id": 1}])

    def down() -> None:
        raise RuntimeError("down")

    fake.get_scene_list = down  # type: ignore[assignment]
    app._restore_leftover_sources(fake, marker)  # type: ignore[arg-type]
    assert marker.exists() and "down" in capsys.readouterr().out


# ---------- pre-flight safety net ----------


def make_ctx(client: WindowObs, scene: str, capture: str) -> preflight.Context:
    return preflight.Context(
        page=SimpleNamespace(evaluate=lambda *a: None),  # type: ignore[arg-type]
        browser=object(),  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        capture=capture,
        scene=scene,
        can_retarget=False,
        screen=None,
        meter=None,
        audio_output="",
        audio_level=0.003,
        wait=lambda s: None,
    )


def test_preflight_fails_when_another_source_is_visible():
    result = preflight.check_capture(make_ctx(make_obs(), "vrec", "vrec screen"))
    assert result.ok is False
    assert result.detail == "'chrome fenetre' is also visible in the scene and would be recorded over Chrome"
    assert result.hint == "Hide it in OBS (eye icon), or remove it from that scene."


def test_preflight_fails_in_the_users_scene_too():
    # obs_scene off: the capture is the scene name, the first visible picture counts as the recorded one.
    result = preflight.check_capture(make_ctx(make_obs(), "vrec", "vrec"))
    assert result.ok is False and "'chrome fenetre'" in result.detail


def test_preflight_ignores_hidden_sources(monkeypatch):
    fake = make_obs()
    item = next(i for i in fake.scenes["vrec"] if i["sourceName"] == "chrome fenetre")
    fake.set_scene_item_enabled("vrec", item["sceneItemId"], False)
    monkeypatch.setattr(preflight, "shows_colors", lambda *a, **k: True)
    monkeypatch.setattr(preflight, "load_js", lambda name: name)
    assert preflight.check_capture(make_ctx(fake, "vrec", "vrec screen")).ok is True


# ---------- doctor ----------


class DoctorClient:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items

    def get_scene_item_list(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(scene_items=self.items)

    def get_current_program_scene(self) -> SimpleNamespace:
        return SimpleNamespace(current_program_scene_name="Main", scene_name="Main")

    def get_scene_list(self) -> SimpleNamespace:
        return SimpleNamespace(scenes=[{"sceneName": "vrec"}], current_program_scene_name="Main")


def doctor_ctx(own_scene: bool) -> Any:
    items = [
        {"inputKind": "monitor_capture", "sourceName": "vrec screen"},
        {"inputKind": "window_capture", "sourceName": "chrome fenetre"},
    ]
    return SimpleNamespace(
        settings=Settings(),
        features=FeatureSet({"obs_scene": own_scene}),
        client=lambda: DoctorClient(items),
    )


def test_doctor_is_info_when_vrec_hides_the_other_sources():
    check = doctor.check_scene_capture(doctor_ctx(True))
    assert check.status == "info" and "vrec hides 'chrome fenetre' while recording" in check.detail


def test_doctor_warns_without_own_scene():
    check = doctor.check_scene_capture(doctor_ctx(False))
    assert check.status == "warn" and "recorded too" in check.detail


def test_other_pictures_helper_excludes_the_capture():
    fake = make_obs()
    assert [n for n, _ in obs_scene.other_pictures(fake, "vrec", "vrec screen")] == ["chrome fenetre"]  # type: ignore[arg-type]
