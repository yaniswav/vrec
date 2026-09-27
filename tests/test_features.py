"""Tests for vrec.features: registry sanity, load/save, CLI flags, and the menu."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from vrec import menu
from vrec.__main__ import main
from vrec.errors import VrecError
from vrec.features import (
    REGISTRY,
    FeatureSet,
    feature_names,
    load_features,
    save_features,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src" / "vrec"
README = REPO_ROOT / "README.md"

_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_names_are_unique() -> None:
    names = feature_names()
    assert len(names) == len(set(names))


def test_registry_names_are_snake_case() -> None:
    for feature in REGISTRY:
        assert _SNAKE_CASE.match(feature.name), feature.name


def test_registry_descriptions_are_non_empty() -> None:
    for feature in REGISTRY:
        assert feature.description.strip()


def test_defaults_are_on_except_manage_virtual_display() -> None:
    fs = FeatureSet()
    for feature in REGISTRY:
        expected = feature.name != "manage_virtual_display"
        assert fs.enabled(feature.name) is expected


def test_manage_virtual_display_default_is_off() -> None:
    by_name = {f.name: f for f in REGISTRY}
    assert by_name["manage_virtual_display"].default is False


# ---------------------------------------------------------------------------
# FeatureSet
# ---------------------------------------------------------------------------


def test_enabled_unknown_name_raises() -> None:
    with pytest.raises(KeyError):
        FeatureSet().enabled("qualiti_filter")


def test_set_unknown_name_raises() -> None:
    with pytest.raises(KeyError):
        FeatureSet().set("qualiti_filter", False)


def test_constructor_unknown_override_raises() -> None:
    with pytest.raises(KeyError):
        FeatureSet({"qualiti_filter": False})


def test_set_and_enabled_round_trip() -> None:
    fs = FeatureSet()
    fs.set("quality_filter", False)
    assert fs.enabled("quality_filter") is False


def test_items_are_in_registry_order() -> None:
    fs = FeatureSet()
    assert [feature.name for feature, _ in fs.items()] == feature_names()


def test_disabled_lists_only_off_features_in_registry_order() -> None:
    fs = FeatureSet()
    fs.set("audio_check", False)
    assert fs.disabled() == ["audio_check", "manage_virtual_display"]


def test_overrides_only_reports_differences_from_default() -> None:
    fs = FeatureSet()
    assert fs.overrides() == {}
    fs.set("quality_filter", False)
    fs.set("manage_virtual_display", True)
    assert fs.overrides() == {"quality_filter": False, "manage_virtual_display": True}


# ---------------------------------------------------------------------------
# load_features / save_features
# ---------------------------------------------------------------------------


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    fs, warnings = load_features(tmp_path / "features.toml")
    assert warnings == []
    for feature in REGISTRY:
        assert fs.enabled(feature.name) is feature.default


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "features.toml"
    fs = FeatureSet()
    fs.set("quality_filter", False)
    fs.set("manage_virtual_display", True)
    save_features(path, fs)

    reloaded, warnings = load_features(path)
    assert warnings == []
    assert reloaded.overrides() == {"quality_filter": False, "manage_virtual_display": True}


def test_save_writes_a_comment_for_every_feature(tmp_path: Path) -> None:
    path = tmp_path / "features.toml"
    save_features(path, FeatureSet())
    text = path.read_text(encoding="utf-8")

    assert "managed by" in text
    for feature in REGISTRY:
        assert f"# {feature.description} (default: {'on' if feature.default else 'off'})" in text
        assert re.search(rf"^{feature.name} = (true|false)$", text, re.MULTILINE)


def test_save_uses_lf_line_endings(tmp_path: Path) -> None:
    path = tmp_path / "features.toml"
    save_features(path, FeatureSet())
    raw = path.read_bytes()
    assert b"\r\n" not in raw


def test_save_is_atomic_leaves_no_tmp_file(tmp_path: Path) -> None:
    path = tmp_path / "features.toml"
    save_features(path, FeatureSet())
    assert not path.with_suffix(".tmp").exists()
    assert path.exists()


def test_unknown_name_in_file_is_a_warning_and_ignored(tmp_path: Path) -> None:
    path = tmp_path / "features.toml"
    path.write_text("[features]\nfoo = true\n", encoding="utf-8")
    fs, warnings = load_features(path)
    assert warnings == ["Unknown feature in features.toml, ignored: foo"]
    assert fs.overrides() == {}


def test_non_bool_value_is_a_warning_and_keeps_default(tmp_path: Path) -> None:
    path = tmp_path / "features.toml"
    path.write_text('[features]\nquality_filter = "nope"\n', encoding="utf-8")
    fs, warnings = load_features(path)
    assert len(warnings) == 1
    assert "quality_filter" in warnings[0]
    assert fs.enabled("quality_filter") is True


def test_invalid_toml_raises(tmp_path: Path) -> None:
    path = tmp_path / "features.toml"
    path.write_text("this is not [valid toml\n", encoding="utf-8")
    with pytest.raises(VrecError, match="Invalid features file"):
        load_features(path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_features_lists_everything(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--features", "--data-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "[ON ]" in out
    assert "[OFF]" in out
    for feature in REGISTRY:
        assert feature.name in out
    assert "default" in out.lower()


def test_cli_enable_updates_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--enable", "manage_virtual_display", "--data-dir", str(tmp_path)])
    assert code == 0
    fs, warnings = load_features(tmp_path / "features.toml")
    assert warnings == []
    assert fs.enabled("manage_virtual_display") is True
    out = capsys.readouterr().out
    assert "manage_virtual_display" in out


def test_cli_disable_updates_file(tmp_path: Path) -> None:
    code = main(["--disable", "quality_filter", "audio_check", "--data-dir", str(tmp_path)])
    assert code == 0
    fs, warnings = load_features(tmp_path / "features.toml")
    assert warnings == []
    assert fs.enabled("quality_filter") is False
    assert fs.enabled("audio_check") is False


def test_cli_unknown_name_exits_1_and_lists_valid_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--enable", "not_a_feature", "--data-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 1
    assert "not_a_feature" in out
    assert "Valid names" in out
    for name in feature_names():
        assert name in out


def test_cli_does_not_take_the_instance_lock(tmp_path: Path) -> None:
    """--features must work even while a real run would be locked out."""
    from vrec.lock import InstanceLock

    with InstanceLock(tmp_path / "vrec.lock"):
        code = main(["--features", "--data-dir", str(tmp_path)])
    assert code == 0


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------


def test_menu_toggle_flow_saves_immediately(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    features_path = tmp_path / "features.toml"
    fs = FeatureSet()
    assert fs.enabled("quality_retry") is True

    answers = iter(["2", ""])  # switch #2 (quality_retry), then Enter to go back
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    menu._features_menu(fs, features_path)

    assert fs.enabled("quality_retry") is False
    reloaded, warnings = load_features(features_path)
    assert warnings == []
    assert reloaded.enabled("quality_retry") is False


def test_menu_toggle_can_switch_several_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    features_path = tmp_path / "features.toml"
    fs = FeatureSet()

    answers = iter(["1,2", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    menu._features_menu(fs, features_path)

    assert fs.enabled("quality_filter") is False
    assert fs.enabled("quality_retry") is False


def test_menu_toggle_invalid_input_does_not_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    features_path = tmp_path / "features.toml"
    fs = FeatureSet()

    answers = iter(["nope", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    menu._features_menu(fs, features_path)  # should not raise

    assert fs.overrides() == {}


# ---------------------------------------------------------------------------
# Consistency checks against the rest of the codebase (wired/documented later)
# ---------------------------------------------------------------------------


def test_every_feature_is_checked_somewhere() -> None:
    text = "\n".join(p.read_text(encoding="utf-8") for p in SRC_DIR.glob("*.py"))
    for name in feature_names():
        assert f'enabled("{name}")' in text, f"{name} is never checked with enabled(...)"


def test_every_feature_is_documented_in_readme() -> None:
    text = README.read_text(encoding="utf-8")
    for name in feature_names():
        assert name in text, f"{name} is not mentioned in README.md"
