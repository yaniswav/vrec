"""Tests for src/vrec/js/quality_filter.js, run through a small Node harness."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "js" / "quality_filter_harness.mjs"
QUALITY_FILTER_JS = REPO_ROOT / "src" / "vrec" / "js" / "quality_filter.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _run_harness() -> list[dict]:
    proc = subprocess.run(
        ["node", str(HARNESS), str(QUALITY_FILTER_JS)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"harness failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def results() -> dict[tuple[str, int], dict]:
    return {(r["format"], r["cap"]): r for r in _run_harness()}


def test_harness_runs_and_is_deterministic() -> None:
    first = _run_harness()
    second = _run_harness()
    assert first == second


@pytest.mark.parametrize(
    ("cap", "expected_kept", "expected_forced"),
    [
        (0, ["high"], "4320x2160"),
        (2159, ["mid"], "2880x1440"),
        (1439, ["low"], "1920x960"),
        (500, ["low"], "1920x960"),
    ],
)
def test_hls_variant_selection(results, cap, expected_kept, expected_forced) -> None:
    r = results[("hls", cap)]
    assert r["kept"] == expected_kept
    assert r["forced"] == expected_forced
    assert r["unchanged"] is False


@pytest.mark.parametrize(
    ("cap", "expected_video_kept", "expected_forced"),
    [
        (0, "high", "4320x2160"),
        (2159, "mid", "2880x1440"),
        (1439, "low", "1920x960"),
        (500, "low", "1920x960"),
    ],
)
def test_dash_representation_selection(results, cap, expected_video_kept, expected_forced) -> None:
    r = results[("dash", cap)]
    # The audio-only AdaptationSet has no height, so it's never a filtering target
    # and always survives untouched alongside whichever video representation was kept.
    assert set(r["kept"]) == {expected_video_kept, "audio"}
    assert r["forced"] == expected_forced
    assert r["unchanged"] is False


def test_single_variant_manifest_is_untouched(results) -> None:
    r = results[("hls-single", 0)]
    assert r["unchanged"] is True
    assert r["forced"] is None
    assert r["kept"] == ["only"]


def test_non_manifest_json_is_untouched(results) -> None:
    r = results[("json", 0)]
    assert r["unchanged"] is True
    assert r["forced"] is None
