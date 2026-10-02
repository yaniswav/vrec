"""Application settings and filesystem paths."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vrec.errors import VrecError


@dataclass(frozen=True)
class Settings:
    """Tunable knobs, all with the values the original script used."""

    obs_host: str = "127.0.0.1"
    obs_port: int = 4455
    audio_source_name: str = "Chrome Audio (VB-CABLE)"
    obs_path: str = ""
    obs_start_timeout_s: float = 60
    obs_scene_name: str = "vrec"
    obs_capture: str = "screen"
    chrome_port: int = 9222
    chrome_path: str = ""
    chrome_profile: str = ""
    chrome_start_timeout_s: float = 30
    lead_in_s: float = 2
    tail_s: float = 2
    fullscreen_settle_s: float = 2
    test_duration_s: float = 30
    black_level: int = 20
    abort_if_black_after_s: float = 60
    audio_level: float = 0.003
    pause_below_s: float = 2
    resume_at_s: float = 10
    max_stall_s: float = 300
    max_height: int = 0
    display_screen: str = "auto"
    audio_output: str = "CABLE Input"
    max_wall_factor: float = 3
    max_wall_extra_s: float = 600
    min_free_gb: float = 5


@dataclass(frozen=True)
class Paths:
    """Where vrec reads and writes its data."""

    data_dir: Path
    config: Path

    @property
    def videos(self) -> Path:
        return self.data_dir / "videos.txt"

    @property
    def history(self) -> Path:
        return self.data_dir / "history.json"

    @property
    def password(self) -> Path:
        return self.data_dir / "obs_password.txt"

    @property
    def lock(self) -> Path:
        return self.data_dir / "vrec.lock"

    @property
    def obs_restore(self) -> Path:
        return self.data_dir / "obs_restore.json"

    @property
    def obs_scene_restore(self) -> Path:
        return self.data_dir / "obs_restore_scene.txt"

    @property
    def features(self) -> Path:
        return self.data_dir / "features.toml"


# Maps [toml section][toml key] -> (Settings field name, expected type).
_SCHEMA: dict[str, dict[str, tuple[str, type]]] = {
    "obs": {
        "host": ("obs_host", str),
        "port": ("obs_port", int),
        "audio_source_name": ("audio_source_name", str),
        "path": ("obs_path", str),
        "start_timeout": ("obs_start_timeout_s", float),
        "scene": ("obs_scene_name", str),
        "capture": ("obs_capture", str),
    },
    "chrome": {
        "debug_port": ("chrome_port", int),
        "path": ("chrome_path", str),
        "profile": ("chrome_profile", str),
        "start_timeout": ("chrome_start_timeout_s", float),
    },
    "recording": {
        "lead_in": ("lead_in_s", float),
        "tail": ("tail_s", float),
        "fullscreen_settle": ("fullscreen_settle_s", float),
        "test_duration": ("test_duration_s", float),
        "max_wall_factor": ("max_wall_factor", float),
        "max_wall_extra": ("max_wall_extra_s", float),
        "min_free_gb": ("min_free_gb", float),
    },
    "checks": {
        "black_level": ("black_level", int),
        "abort_if_black_after": ("abort_if_black_after_s", float),
        "audio_level": ("audio_level", float),
    },
    "buffering": {
        "pause_below": ("pause_below_s", float),
        "resume_at": ("resume_at_s", float),
        "max_stall": ("max_stall_s", float),
    },
    "quality": {
        "max_height": ("max_height", int),
    },
    "display": {
        "screen": ("display_screen", str),
    },
    "audio": {
        "output": ("audio_output", str),
    },
}


def load_settings(path: Path | None) -> Settings:
    """Load settings from a TOML file. A missing file means "use the defaults"."""
    if path is None or not path.exists():
        return Settings()

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise VrecError(f"Invalid config file {path}: {e}") from e

    # Any: the dict is assembled dynamically from the schema below (mixed str/int/float
    # fields), then unpacked into Settings(**values); _coerce validates each value against
    # its expected type at runtime.
    values: dict[str, Any] = {}
    for section, entries in data.items():
        schema = _SCHEMA.get(section)
        if schema is None:
            raise VrecError(f"Unknown config section: [{section}]")
        if not isinstance(entries, dict):
            raise VrecError(f"Invalid config section: [{section}]")
        for key, value in entries.items():
            field = schema.get(key)
            if field is None:
                raise VrecError(f"Unknown config key: [{section}] {key}")
            field_name, field_type = field
            values[field_name] = _coerce(section, key, value, field_type)

    if values.get("obs_capture", "screen") not in ("screen", "window"):
        raise VrecError('Invalid value for [obs] capture: expected "screen" or "window"')
    return Settings(**values)


def _coerce(section: str, key: str, value: object, field_type: type) -> object:
    if field_type is float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise VrecError(f"Invalid value for [{section}] {key}: expected a number")
        return float(value)
    if field_type is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise VrecError(f"Invalid value for [{section}] {key}: expected a whole number")
        return value
    if field_type is str:
        if not isinstance(value, str):
            raise VrecError(f"Invalid value for [{section}] {key}: expected text")
        return value
    return value
