"""Contract tests for state.js, pick_video.js, wait_can_play.js and resolution.js (Node harness).

recorder.py and monitor.py read these scripts' return values by key/position, so the shapes matter.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from vrec.monitor import PlayerState

TESTS = Path(__file__).resolve().parent
HARNESS = TESTS / "js" / "player_scripts_harness.mjs"
JS_DIR = TESTS.parent / "src" / "vrec" / "js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@pytest.fixture(scope="module")
def out() -> dict[str, Any]:
    proc = subprocess.run(
        ["node", str(HARNESS), str(JS_DIR)], capture_output=True, text=True, timeout=60, check=False
    )
    assert proc.returncode == 0, f"harness failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    return json.loads(proc.stdout)


def test_state_has_every_key_the_monitor_reads(out: dict[str, Any]) -> None:
    assert set(out["state"]) == set(PlayerState.__annotations__)
    assert set(out["state"]) == {"ended", "t", "d", "paused", "w", "h", "buffer"}


def test_state_values(out: dict[str, Any]) -> None:
    assert out["state"] == {
        "ended": False,
        "t": 12.5,
        "d": 100,
        "paused": False,
        "w": 3840,
        "h": 1920,
        "buffer": 30.0,
    }


def test_state_buffer_is_zero_without_a_range_around_the_playhead(out: dict[str, Any]) -> None:
    assert out["stateNoBuffer"]["buffer"] == 0
    assert out["stateNoBuffer"]["paused"] is True and out["stateNoBuffer"]["ended"] is True
    assert out["stateSecondRange"]["buffer"] == 18  # only the range containing t=12 counts


def test_state_is_null_when_the_video_is_gone(out: dict[str, Any]) -> None:
    assert out["stateNoVideo"] is None
    assert out["stateDetached"] is None


def test_pick_video_returns_the_duration_of_the_longest_video(out: dict[str, Any]) -> None:
    assert out["pick"] == {"status": "found", "duration": 600}
    assert out["pickedTag"] == "main"


def test_pick_video_statuses_when_nothing_loads(out: dict[str, Any]) -> None:
    assert out["pickNone"] == {"status": "none"}
    assert out["pickStuck"] == [{"status": "loading"}] * 2
    assert out["pickStuckLate"] == [{"status": "loading"}] * 3
    assert out["stuckNotStored"] is True  # a video that isn't loaded is not stored


def test_pick_video_kicks_a_muted_play_once_after_3_seconds(out: dict[str, Any]) -> None:
    assert out["playsBefore"] == 0
    assert out["playsAfter"] == 1
    assert out["stuckMuted"] is True


def test_wait_can_play_is_a_quick_ready_check(out: dict[str, Any]) -> None:
    assert out["canPlayNot"] is False
    assert out["canPlay"] is True


def test_resolution_is_a_width_height_streaming_triple(out: dict[str, Any]) -> None:
    assert out["resolutionFile"] == [3840, 1920, False]
    assert out["resolutionBlob"] == [1920, 960, True]
    assert out["resolutionNoSrc"][2] is False
    assert all(len(out[k]) == 3 for k in ("resolutionFile", "resolutionBlob", "resolutionNoSrc"))
