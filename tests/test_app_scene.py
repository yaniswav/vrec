"""The OBS scene and pre-flight steps of a batch (OBS, Chrome and the checks are faked)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vrec import app, preflight
from vrec.config import Paths, Settings
from vrec.errors import VrecError
from vrec.features import FeatureSet
from vrec.obs_scene import SceneSetup


class FakeClient:
    def __init__(self, program: str = "Main") -> None:
        self.program = program
        self.scenes = ["Main", "vrec"]

    def get_current_program_scene(self) -> SimpleNamespace:
        return SimpleNamespace(scene_name=self.program, current_program_scene_name=self.program)

    def get_scene_list(self) -> SimpleNamespace:
        return SimpleNamespace(
            scenes=[{"sceneName": n} for n in self.scenes], current_program_scene_name=self.program
        )

    def set_current_program_scene(self, name: str) -> None:
        self.program = name


def make_batch(tmp_path: Path, client: FakeClient, **features: bool) -> app.Batch:
    return app.Batch(
        paths=Paths(data_dir=tmp_path, config=tmp_path / "config.toml"),
        settings=Settings(),
        features=FeatureSet(features),
        test_mode=False,
        client=client,  # type: ignore[arg-type]
        scene="Main",
        browser=object(),  # type: ignore[arg-type]
        page=object(),  # type: ignore[arg-type]
    )


@pytest.fixture
def scene_created(monkeypatch):  # noqa: ANN201
    setup = SceneSetup("vrec", "vrec screen", created_scene=True, created_capture=True)
    monkeypatch.setattr(app.obs_scene, "ensure_scene", lambda client, name, mode="screen": setup)
    return setup


def test_switches_to_the_vrec_scene_and_back(tmp_path, scene_created, capsys):
    client = FakeClient()
    batch = make_batch(tmp_path, client)
    app._prepare_scene(batch)
    assert client.program == "vrec"
    assert (batch.scene, batch.capture, batch.previous_scene) == ("vrec", "vrec screen", "Main")
    assert batch.paths.obs_scene_restore.read_text(encoding="utf-8") == "Main"
    assert "Created the OBS scene 'vrec'" in capsys.readouterr().out
    app._restore_scene(batch)
    assert client.program == "Main"
    assert not batch.paths.obs_scene_restore.exists()


def test_scene_feature_off_keeps_the_users_scene(tmp_path, monkeypatch):
    monkeypatch.setattr(app.obs_scene, "ensure_scene", lambda *a: pytest.fail("should not touch scenes"))
    client = FakeClient()
    batch = make_batch(tmp_path, client, obs_scene=False)
    app._prepare_scene(batch)
    app._restore_scene(batch)
    assert client.program == "Main" and batch.scene == "Main" and batch.capture == ""


def test_leftover_scene_is_restored_on_next_start(tmp_path, capsys):
    client = FakeClient(program="vrec")
    restore = tmp_path / "obs_restore_scene.txt"
    restore.write_text("Main", encoding="utf-8")
    app._restore_leftover_scene(client, restore)  # type: ignore[arg-type]
    assert client.program == "Main" and not restore.exists()
    assert "Switched OBS back to the scene 'Main'" in capsys.readouterr().out


def test_leftover_scene_that_no_longer_exists_is_dropped(tmp_path, capsys):
    client = FakeClient(program="vrec")
    restore = tmp_path / "obs_restore_scene.txt"
    restore.write_text("Deleted scene", encoding="utf-8")
    app._restore_leftover_scene(client, restore)  # type: ignore[arg-type]
    assert client.program == "vrec" and not restore.exists()
    assert "no longer exists" in capsys.readouterr().out


def fake_checks(monkeypatch: pytest.MonkeyPatch, *oks: bool | None) -> list[preflight.Context]:
    seen: list[preflight.Context] = []

    def run(ctx: preflight.Context) -> list[preflight.CheckResult]:
        seen.append(ctx)
        return [preflight.CheckResult(ok, f"check {i}") for i, ok in enumerate(oks)]

    monkeypatch.setattr(app.preflight, "run_checks", run)
    monkeypatch.setattr(app.display, "list_screens", lambda: [])
    return seen


def test_preflight_passes(tmp_path, monkeypatch):
    seen = fake_checks(monkeypatch, True, None, True)
    batch = make_batch(tmp_path, FakeClient())
    batch.capture, batch.scene = "vrec screen", "vrec"
    app._preflight(batch)
    ctx = seen[0]
    assert (ctx.capture, ctx.can_retarget, ctx.audio_output) == ("vrec screen", True, "CABLE Input")


def test_preflight_without_own_scene_grabs_the_program_scene(tmp_path, monkeypatch):
    seen = fake_checks(monkeypatch, True)
    batch = make_batch(tmp_path, FakeClient(), audio_sink=False)
    app._preflight(batch)
    assert (seen[0].capture, seen[0].can_retarget, seen[0].audio_output) == ("Main", False, "")


def test_preflight_failure_stops_a_non_interactive_batch(tmp_path, monkeypatch):
    fake_checks(monkeypatch, True, False)
    with pytest.raises(VrecError, match="Pre-flight check failed"):
        app._preflight(make_batch(tmp_path, FakeClient()))


@pytest.mark.parametrize(("answer", "goes_on"), [("y", True), ("", False), ("n", False)])
def test_preflight_failure_asks_in_interactive_mode(tmp_path, monkeypatch, answer, goes_on):
    fake_checks(monkeypatch, False)
    monkeypatch.setattr("builtins.input", lambda prompt="": answer)
    batch = make_batch(tmp_path, FakeClient())
    batch.interactive = True
    if goes_on:
        app._preflight(batch)
    else:
        with pytest.raises(VrecError):
            app._preflight(batch)


def test_preflight_feature_off(tmp_path, monkeypatch):
    monkeypatch.setattr(app.preflight, "run_checks", lambda ctx: pytest.fail("should not run"))
    app._preflight(make_batch(tmp_path, FakeClient(), preflight_check=False))
