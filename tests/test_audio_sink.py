"""set_audio_sink.js / audio_contexts.js through Node, and the Python-side helpers."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from vrec.browser import origin_of

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "js" / "audio_sink_harness.mjs"
JS_DIR = ROOT / "src" / "vrec" / "js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run(scenario: str) -> dict:
    out = subprocess.run(
        ["node", str(HARNESS), str(JS_DIR), scenario], capture_output=True, text=True, check=True, timeout=30
    )
    return json.loads(out.stdout)


@needs_node
def test_routes_the_video_and_every_audio_context():
    data = run("ok")
    assert data["result"] == {"ok": True, "label": "CABLE Input (VB-Audio Virtual Cable)", "contexts": 1}
    assert data["videoSink"] == "cable"
    assert data["earlySink"] == "cable"  # context created before routing
    assert data["lateSink"] == "cable"  # context created after routing
    assert data["tracked"] == 2
    assert data["isSubclass"] is True


@needs_node
def test_prefers_the_device_over_the_default_alias():
    data = run("aliased")
    assert data["videoSink"] == "cable"
    assert data["result"]["label"] == "CABLE Input (VB-Audio Virtual Cable)"


@needs_node
def test_missing_output_name():
    data = run("missing")
    assert data["result"] == {"ok": False, "reason": 'no audio output named "Nope"'}
    assert data["videoSink"] == ""


@needs_node
def test_without_permission_names_are_hidden():
    data = run("unlabelled")
    assert data["result"] == {"ok": False, "reason": "no permission to list audio outputs"}


@needs_node
def test_refused_by_the_browser():
    data = run("refused")
    assert data["result"] == {"ok": False, "reason": "NotAllowedError"}


@needs_node
def test_no_video_picked():
    data = run("unsupported")
    assert data["result"] == {"ok": False, "reason": "not supported by this browser"}


@pytest.mark.parametrize(
    ("url", "origin"),
    [
        ("https://example.com/videos/1?x=2#t", "https://example.com"),
        ("http://localhost:8080/a", "http://localhost:8080"),
    ],
)
def test_origin_of(url, origin):
    assert origin_of(url) == origin
