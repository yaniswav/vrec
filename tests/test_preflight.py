"""Pre-flight helpers: color detection on OBS captures and the local test page."""

from __future__ import annotations

import io
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import pytest
from PIL import Image

from vrec import preflight
from vrec.browser import WindowBounds
from vrec.display import Screen
from vrec.obs_scene import Monitor
from vrec.preflight import CheckResult, Context, color_share, hex_color, serve_test_page, shows_colors

MAGENTA, CYAN = preflight.TEST_COLORS


def png(color: tuple[int, int, int], share: float = 1.0, size: tuple[int, int] = (64, 36)) -> bytes:
    """A capture where `share` of the pixels (top part) have `color`, the rest black."""
    image = Image.new("RGB", size, (0, 0, 0))
    rows = round(size[1] * share)
    for y in range(rows):
        for x in range(size[0]):
            image.putpixel((x, y), color)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_hex_color():
    assert hex_color((255, 0, 255)) == "#ff00ff"


def test_color_share():
    assert color_share(png(MAGENTA), MAGENTA) == 1.0
    assert color_share(png((230, 20, 240)), MAGENTA) == 1.0  # compression / color management drift
    assert color_share(png(MAGENTA, share=0.5), MAGENTA) == 0.5
    assert color_share(png((0, 0, 0)), MAGENTA) == 0.0


class ColorScreen:
    """Fake chain: Chrome shows a color, OBS captures `seen(color)`."""

    def __init__(self, seen) -> None:  # noqa: ANN001
        self.seen = seen
        self.color = "#000000"
        self.shown: list[str] = []

    def set_color(self, color: str) -> None:
        self.color = color
        self.shown.append(color)

    def grab(self) -> bytes | None:
        return self.seen(self.color)


def rgb_of(color: str) -> tuple[int, int, int]:
    return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))


def test_shows_colors_when_obs_sees_chrome():
    screen = ColorScreen(lambda c: png(rgb_of(c)))
    assert shows_colors(screen.set_color, screen.grab, lambda: None)
    assert screen.shown == ["#ff00ff", "#00ffff"]


def test_fails_when_obs_films_another_screen():
    screen = ColorScreen(lambda c: png((40, 40, 40)))
    assert not shows_colors(screen.set_color, screen.grab, lambda: None)
    assert screen.shown == ["#ff00ff"]  # stops at the first missing color


def test_fails_when_something_covers_part_of_chrome():
    screen = ColorScreen(lambda c: png(rgb_of(c), share=0.4))
    assert not shows_colors(screen.set_color, screen.grab, lambda: None)


def test_fails_when_the_screenshot_is_unavailable():
    screen = ColorScreen(lambda c: None)
    assert not shows_colors(screen.set_color, screen.grab, lambda: None)


def test_a_frozen_magenta_image_is_not_enough():
    screen = ColorScreen(lambda c: png(MAGENTA))
    assert not shows_colors(screen.set_color, screen.grab, lambda: None)


def test_page_server_serves_the_test_page():
    with serve_test_page() as url:
        assert url.startswith("http://127.0.0.1:")
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read().decode("utf-8")
            assert response.headers["Content-Type"].startswith("text/html")
    assert "<title>vrec check</title>" in body


def test_check_result_lines():
    assert CheckResult(True, "Chrome", "on DISPLAY3").line() == "  [ OK ] Chrome: on DISPLAY3"
    assert CheckResult(None, "Sound", "not checked").line() == "  [SKIP] Sound: not checked"
    failed = CheckResult(False, "OBS sees Chrome", "no", hint="Check the capture").line()
    assert failed.splitlines() == ["  [FAIL] OBS sees Chrome: no", "         -> Check the capture"]


# ---------- the checks, with fakes ----------

VIRTUAL = Screen("DISPLAY3", 2560, 0, 3840, 2160, primary=False)


@dataclass
class FakeChain:
    """Chrome page + OBS: OBS captures the page color only while its monitor is `good_monitor`."""

    good_monitor: str | None = "id-virtual"
    monitor: str | None = "id-virtual"
    color: str = "#000000"
    tone_reaches_obs: bool = True
    meter: Any = None
    url: str = ""
    evaluated: list[str] = field(default_factory=list)

    # page
    def goto(self, url: str, **_: Any) -> None:
        self.url = url

    def evaluate(self, script: str, arg: Any = None) -> Any:
        self.evaluated.append(script)
        if script == "preflight_color.js":
            self.color = arg
            return None
        if script == "preflight_tone.js":
            if self.tone_reaches_obs and self.meter is not None:
                self.meter.peak = 0.3
            return {"sink": "CABLE Input (VB-Audio Virtual Cable)" if arg[0] else "default"}
        raise AssertionError(script)

    # obs capture
    def grab(self) -> bytes | None:
        if self.monitor != self.good_monitor:
            return png((30, 30, 30))
        return png(rgb_of(self.color))


class Meter:
    def __init__(self) -> None:
        self.peak = 0.0

    def reset(self) -> None:
        self.peak = 0.0


@pytest.fixture
def chain(monkeypatch):  # noqa: ANN201
    fake = FakeChain(meter=Meter())
    monitors = [Monitor("Main", "id-main", "monitor_id"), Monitor("Virtual", "id-virtual", "monitor_id")]
    monkeypatch.setattr(preflight, "load_js", lambda name: name)
    monkeypatch.setattr(preflight.obs_scene, "grab", lambda client, source: fake.grab())
    monkeypatch.setattr(preflight.obs_scene, "current_monitor", lambda client, capture: fake.monitor)
    monkeypatch.setattr(preflight.obs_scene, "monitors", lambda client, capture: monitors)
    monkeypatch.setattr(
        preflight.obs_scene, "set_monitor", lambda client, capture, m: setattr(fake, "monitor", m.value)
    )
    monkeypatch.setattr(preflight, "microphone_permission", lambda page, origin: _null())
    monkeypatch.setattr(
        preflight, "get_window_bounds", lambda browser, page: WindowBounds(2560, 0, 3840, 2160, "fullscreen")
    )
    return fake


class _null:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: object) -> None:
        return None


def make_ctx(chain: FakeChain, **overrides: Any) -> Context:
    values: dict[str, Any] = {
        "page": chain,
        "browser": object(),
        "client": object(),
        "capture": "vrec screen",
        "can_retarget": True,
        "screen": VIRTUAL,
        "meter": chain.meter,
        "audio_output": "CABLE Input",
        "audio_level": 0.003,
        "wait": lambda s: None,
    }
    values.update(overrides)
    return Context(**values)


def test_everything_ok(chain):
    results = preflight.run_checks(make_ctx(chain))
    assert [r.ok for r in results] == [True, True, True, True]
    assert chain.url.startswith("http://127.0.0.1:")
    assert "CABLE Input" in results[3].detail


def test_no_virtual_screen(chain):
    results = preflight.run_checks(make_ctx(chain, screen=None))
    assert results[0].ok is False and results[1].ok is None


def test_window_on_another_screen(chain, monkeypatch):
    monkeypatch.setattr(preflight, "get_window_bounds", lambda b, p: WindowBounds(0, 0, 800, 600, "normal"))
    result = preflight.check_window(make_ctx(chain))
    assert result.ok is False and "not on DISPLAY3" in result.detail


def test_window_not_fullscreen(chain, monkeypatch):
    monkeypatch.setattr(
        preflight, "get_window_bounds", lambda b, p: WindowBounds(2560, 0, 3840, 2160, "maximized")
    )
    result = preflight.check_window(make_ctx(chain))
    assert result.ok is False and "not fullscreen" in result.detail


def test_capture_on_the_wrong_monitor_is_retargeted(chain):
    chain.monitor = "id-main"
    result = preflight.check_capture(make_ctx(chain))
    assert result.ok is True and "now films Virtual" in result.detail
    assert chain.monitor == "id-virtual"  # kept in OBS for next time


def test_capture_never_shows_chrome_restores_the_original_monitor(chain):
    chain.good_monitor = None
    chain.monitor = "id-main"
    result = preflight.check_capture(make_ctx(chain))
    assert result.ok is False
    assert chain.monitor == "id-main"


def test_capture_of_the_users_scene_is_never_retargeted(chain):
    chain.monitor = "id-main"
    result = preflight.check_capture(make_ctx(chain, can_retarget=False, capture="Main"))
    assert result.ok is False
    assert chain.monitor == "id-main"


def test_sound_not_reaching_obs(chain):
    chain.tone_reaches_obs = False
    result = preflight.check_sound(make_ctx(chain))
    assert result.ok is False and "CABLE Output" in result.hint


def test_sound_without_audio_sink_hints_the_mixer(chain):
    chain.tone_reaches_obs = False
    result = preflight.check_sound(make_ctx(chain, audio_output=""))
    assert "volume mixer" in result.hint


def test_sound_unchecked_without_meter(chain):
    assert preflight.check_sound(make_ctx(chain, meter=None)).ok is None


def test_a_crashing_check_fails_instead_of_breaking(chain):
    def boom(ctx: Context) -> preflight.CheckResult:
        raise RuntimeError("page crashed\nmore")

    results = preflight.run_checks(make_ctx(chain), checks=[boom])
    assert results == [preflight.CheckResult(False, "Boom", "page crashed")]


def test_screen_smaller_than_the_canvas_is_mentioned(chain):
    from types import SimpleNamespace

    small = Screen("DISPLAY3", 2560, 0, 1920, 1080, primary=False)
    client = SimpleNamespace(get_video_settings=lambda: SimpleNamespace(base_width=3840, base_height=2160))
    result = preflight.check_screen(make_ctx(chain, screen=small, client=client))
    assert result.ok is True and "upscaled" in result.detail
