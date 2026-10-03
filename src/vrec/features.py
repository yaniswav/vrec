"""Feature flags: turn optional behavior on or off without touching code.

Toggles live in data/features.toml, edited through `vrec --enable`/`--disable`, the
"Features on/off" menu, or by hand. A missing file, or a missing key, means "use the
default". See REGISTRY below for what each feature does and its default.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from vrec.errors import VrecError


@dataclass(frozen=True)
class Feature:
    name: str
    description: str
    default: bool = True


REGISTRY: tuple[Feature, ...] = (
    Feature("quality_filter", "Force the best stream quality from the first second"),
    Feature("quality_retry", "Retry a stalled video once at the next lower quality"),
    Feature("buffer_pause", "Pause OBS while the player buffers, so no frozen frames are recorded"),
    Feature("black_check", "Detect a black image and give up on protected videos"),
    Feature("frozen_check", "Detect a frozen image (OBS filming a still picture) and give up"),
    Feature("audio_check", "Warn when no audio reaches OBS"),
    Feature("obs_audio_routing", "Set up OBS audio automatically (capture VB-CABLE, mute desktop and mic)"),
    Feature("wall_clock_cap", "Hard wall-clock time limit per video"),
    Feature("circuit_breaker", "Stop the batch when Chrome or OBS is gone, or after 3 errors in a row"),
    Feature("run_logs", "Write a log file for each run in data/logs"),
    Feature("auto_place_window", "Move the recording Chrome window to the virtual display automatically"),
    Feature(
        "manage_virtual_display",
        "Turn the virtual display on before a batch and off after it",
        default=False,
    ),
    Feature(
        "pin_all_desktops", "Show the recording Chrome on all virtual desktops, so you can switch desktops"
    ),
    Feature(
        "desktop_pause",
        "Pause the recording while Chrome isn't on the current virtual desktop",
    ),
    Feature("audio_sink", "Send only the recorded video's sound to CABLE Input (no Windows mixer setup)"),
    Feature("auto_start_obs", "Start OBS if it isn't open (it stays open afterwards)"),
    Feature("auto_start_chrome", "Start the recording Chrome if it isn't open (it stays open afterwards)"),
    Feature("obs_scene", "Record from vrec's own OBS scene (created if missing), then switch back"),
    Feature("preflight_check", "Before a batch, check screen, window, OBS capture and sound end to end"),
    Feature("protect_console", "Stop a click in the vrec window from pausing it (Windows QuickEdit)"),
    Feature("disk_space_guard", "Stop the batch before a video when the recording disk is almost full"),
    Feature("keep_awake", "Keep Windows from sleeping or turning the screens off while recording"),
    Feature("notify_when_done", "Show a Windows notification when a batch or a test is over"),
)

_BY_NAME: dict[str, Feature] = {feature.name: feature for feature in REGISTRY}


def feature_names() -> list[str]:
    """Every known feature name, in registry order."""
    return [feature.name for feature in REGISTRY]


class FeatureSet:
    """The on/off state of every known feature."""

    def __init__(self, overrides: Mapping[str, bool] | None = None) -> None:
        self._values: dict[str, bool] = {feature.name: feature.default for feature in REGISTRY}
        for name, value in (overrides or {}).items():
            if name not in _BY_NAME:
                raise KeyError(name)
            self._values[name] = value

    def enabled(self, name: str) -> bool:
        if name not in _BY_NAME:
            raise KeyError(name)
        return self._values[name]

    def set(self, name: str, value: bool) -> None:
        if name not in _BY_NAME:
            raise KeyError(name)
        self._values[name] = value

    def items(self) -> list[tuple[Feature, bool]]:
        """Every feature with its current state, in registry order."""
        return [(feature, self._values[feature.name]) for feature in REGISTRY]

    def disabled(self) -> list[str]:
        """Names of the features currently off, in registry order."""
        return [feature.name for feature in REGISTRY if not self._values[feature.name]]

    def overrides(self) -> dict[str, bool]:
        """Only the values that differ from their default: what needs saving."""
        return {
            feature.name: self._values[feature.name]
            for feature in REGISTRY
            if self._values[feature.name] != feature.default
        }


_NAME_WIDTH = max(len(feature.name) for feature in REGISTRY) + 4

LEGEND = "[ON]/[OFF] = current state, * = different from the default"


def render_lines(features: FeatureSet, *, numbered: bool = False) -> list[str]:
    """Aligned on/off list, one line per feature, in registry order.

    With numbered=True each line starts with its 1-based position (for the menu,
    where the user picks features by number); otherwise it's a plain list (for the CLI).
    """
    lines = []
    for i, (feature, value) in enumerate(features.items(), 1):
        state = "ON " if value else "OFF"
        marker = " *" if value != feature.default else ""
        prefix = f"{i:3d} - " if numbered else "  "
        lines.append(f"{prefix}[{state}] {feature.name:<{_NAME_WIDTH}}{feature.description}{marker}")
    return lines


def load_features(path: Path) -> tuple[FeatureSet, list[str]]:
    """Load feature toggles from a TOML file. A missing file means "use the defaults".

    Returns the resulting FeatureSet plus a list of friendly warnings for anything
    ignored (unknown names, non-bool values). Invalid TOML raises VrecError.
    """
    if not path.exists():
        return FeatureSet(), []

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise VrecError(f"Invalid features file {path}: {e}") from e

    entries = data.get("features", {})
    if not isinstance(entries, dict):
        raise VrecError(f"Invalid features file {path}: [features] must be a table")

    warnings: list[str] = []
    overrides: dict[str, bool] = {}
    for name, value in entries.items():
        if name not in _BY_NAME:
            warnings.append(f"Unknown feature in features.toml, ignored: {name}")
            continue
        if not isinstance(value, bool):
            warnings.append(f"Feature '{name}' in features.toml isn't true/false, ignored: {value!r}")
            continue
        overrides[name] = value

    return FeatureSet(overrides), warnings


_HEADER = """# vrec feature toggles.
#
# This file is managed by `vrec --enable`/`--disable` and the "Features on/off"
# menu, but you can also edit it by hand: set a feature to false to turn it off,
# true to turn it on. A missing key keeps its default value.

[features]
"""


def save_features(path: Path, features: FeatureSet) -> None:
    """Write every feature's state atomically (write to a temp file, then replace)."""
    lines = [_HEADER.rstrip("\n")]
    for feature, value in features.items():
        state = "on" if feature.default else "off"
        lines.append(f"# {feature.description} (default: {state})")
        lines.append(f"{feature.name} = {'true' if value else 'false'}")
        lines.append("")
    content = "\n".join(lines).rstrip("\n") + "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    tmp.replace(path)
