"""Tests for vrec.config: defaults, TOML loading, and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from vrec.config import Settings, load_settings
from vrec.errors import VrecError

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_CONFIG = REPO_ROOT / "config.example.toml"


def test_defaults() -> None:
    s = Settings()
    assert s.obs_host == "localhost"
    assert s.obs_port == 4455
    assert s.audio_source_name == "Chrome Audio (VB-CABLE)"
    assert s.chrome_port == 9222
    assert s.lead_in_s == 2
    assert s.tail_s == 2
    assert s.fullscreen_settle_s == 2
    assert s.test_duration_s == 30
    assert s.black_level == 20
    assert s.abort_if_black_after_s == 60
    assert s.audio_level == 0.003
    assert s.pause_below_s == 2
    assert s.resume_at_s == 10
    assert s.max_stall_s == 300
    assert s.max_height == 0
    assert s.max_wall_factor == 3
    assert s.max_wall_extra_s == 600


def test_example_config_matches_defaults() -> None:
    assert EXAMPLE_CONFIG.exists()
    assert load_settings(EXAMPLE_CONFIG) == Settings()


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert load_settings(tmp_path / "does-not-exist.toml") == Settings()


def test_none_path_gives_defaults() -> None:
    assert load_settings(None) == Settings()


def test_unknown_section_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[nope]\nfoo = 1\n", encoding="utf-8")
    with pytest.raises(VrecError, match="Unknown config section"):
        load_settings(path)


def test_unknown_key_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[obs]\nnope = 1\n", encoding="utf-8")
    with pytest.raises(VrecError, match="Unknown config key"):
        load_settings(path)


def test_wrong_type_for_string_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[obs]\nhost = 42\n", encoding="utf-8")
    with pytest.raises(VrecError, match="expected text"):
        load_settings(path)


def test_wrong_type_for_int_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[obs]\nport = "4455"\n', encoding="utf-8")
    with pytest.raises(VrecError, match="expected a whole number"):
        load_settings(path)


def test_wrong_type_for_float_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[recording]\nlead_in = "2"\n', encoding="utf-8")
    with pytest.raises(VrecError, match="expected a number"):
        load_settings(path)


def test_bool_rejected_for_int_field(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[obs]\nport = true\n", encoding="utf-8")
    with pytest.raises(VrecError, match="expected a whole number"):
        load_settings(path)


def test_bool_rejected_for_float_field(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[recording]\nlead_in = false\n", encoding="utf-8")
    with pytest.raises(VrecError, match="expected a number"):
        load_settings(path)


def test_int_accepted_for_float_field(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[recording]\nlead_in = 5\n", encoding="utf-8")
    settings = load_settings(path)
    assert settings.lead_in_s == 5.0
    assert isinstance(settings.lead_in_s, float)


def test_invalid_section_type_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("obs = 1\n", encoding="utf-8")
    with pytest.raises(VrecError, match="Invalid config section"):
        load_settings(path)


def test_invalid_toml_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("this is not [valid toml\n", encoding="utf-8")
    with pytest.raises(VrecError, match="Invalid config file"):
        load_settings(path)
