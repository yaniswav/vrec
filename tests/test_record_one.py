"""record_one end to end with a scripted page and a fake OBS (no Chrome, no OBS)."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from vrec import recorder
from vrec.config import Settings
from vrec.features import FeatureSet


class FakePage:
    """Answers each bundled script by name (load_js is patched to return the name)."""

    def __init__(
        self, duration: float = 10.0, forced: str | None = "3840x1920", title: str = "Page | Site"
    ) -> None:
        self.duration, self.forced, self._title = duration, forced, title
        self.url = ""
        self.now = 1000.0  # fake wall clock, advanced by wait_for_timeout
        self.t = 0.0
        self.playing = False
        self.calls: list[tuple[str, Any]] = []

    def goto(self, url: str, **_: Any) -> None:
        self.url = url

    def title(self) -> str:
        return self._title

    def wait_for_timeout(self, ms: float) -> None:
        self.now += ms / 1000
        if self.playing:
            self.t = min(self.duration, self.t + ms / 1000)

    def evaluate(self, script: str, arg: Any = None) -> Any:
        self.calls.append((script, arg))
        if script == recorder.JS_PLAY:
            self.playing = True
            return None
        if script == recorder.JS_PAUSE:
            self.playing = False
            return None
        answers: dict[str, Any] = {
            "pick_video.js": self.duration,
            "fill_window.js": True,
            recorder._JS_GET_FORCED_QUALITY: self.forced,
            "max_quality.js": {"method": "hls.js", "target": 1440},
            "rewind.js": None,
            "wait_can_play.js": True,
            "resolution.js": [3840, 1920, True],
            "player_requests.js": [],
            "state.js": {
                "ended": self.t >= self.duration,
                "t": self.t,
                "d": self.duration,
                "paused": not self.playing,
                "w": 3840,
                "h": 1920,
                "buffer": 30.0,
            },
        }
        return answers[script]


class FakeObs:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.events: list[str] = []

    def start_record(self) -> None:
        self.events.append("start")

    def stop_record(self) -> SimpleNamespace:
        self.events.append("stop")
        self.output.write_bytes(b"video")
        return SimpleNamespace(output_path=str(self.output))

    def pause_record(self) -> None:
        self.events.append("pause")

    def resume_record(self) -> None:
        self.events.append("resume")

    def get_record_status(self) -> SimpleNamespace:
        return SimpleNamespace(output_active=True)


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, str]]:
    routed: list[tuple[Any, str]] = []

    @contextmanager
    def no_script(page: Any, source: str):  # type: ignore[no-untyped-def]
        page.calls.append(("document_script", source))
        yield

    monkeypatch.setattr(recorder, "load_js", lambda name: name)
    real_watch = recorder.watch
    monkeypatch.setattr(
        recorder,
        "watch",
        lambda player, capture, config, output: real_watch(
            player, capture, config, output, clock=lambda: player._page.now
        ),
    )
    monkeypatch.setattr(recorder, "document_script", no_script)
    monkeypatch.setattr(recorder, "is_black_frame", lambda client, scene, settings: False)
    monkeypatch.setattr(
        recorder, "route_audio_to", lambda page, label: routed.append((page, label)) or (True, "CABLE Input")
    )
    return routed


def run(tmp_path: Path, page: FakePage, features: FeatureSet | None = None, **kwargs: Any):
    client = FakeObs(tmp_path / "2026-09-27 10-00-00.mkv")
    result = recorder.record_one(
        page,  # type: ignore[arg-type]
        client,  # type: ignore[arg-type]
        None,
        "Scene",
        Settings(lead_in_s=0, tail_s=0, fullscreen_settle_s=0),
        features or FeatureSet(),
        1,
        1,
        "https://example.com/videos/1",
        kwargs.pop("title", "My Video"),
        kwargs.pop("test_mode", False),
        **kwargs,
    )
    return result, client


def test_normal_recording(tmp_path, patched, capsys):
    page = FakePage()
    result, client = run(tmp_path, page)
    assert result.reason == "ended"
    assert recorder.status_text(result) == "OK"
    assert result.file == tmp_path / "My Video.mkv" and result.file.exists()
    assert result.quality == "3840x1920" and result.target_height == 1920
    assert client.events == ["start", "stop"]
    assert patched and patched[0][1] == "CABLE Input"
    assert ("document_script", "window.__vrecMaxHeight = 0;") in page.calls
    assert "Audio: this video only -> CABLE Input" in capsys.readouterr().out


def test_title_from_the_page_when_the_list_has_none(tmp_path, patched):
    result, _ = run(tmp_path, FakePage(title="Nice Clip | Site"), title=None)
    assert result.title == "Nice Clip"
    assert result.file == tmp_path / "Nice Clip.mkv"


def test_player_level_quality_with_cap(tmp_path, patched):
    page = FakePage(forced=None)
    result, _ = run(tmp_path, page, max_height=2159)
    assert ("max_quality.js", 2159) in page.calls
    assert ("document_script", "window.__vrecMaxHeight = 2159;") in page.calls
    assert result.quality == "1440p" and result.target_height == 1440


def test_quality_filter_off_leaves_quality_to_the_site(tmp_path, patched, capsys):
    page = FakePage()
    result, _ = run(tmp_path, page, FeatureSet({"quality_filter": False}))
    scripts = [name for name, _ in page.calls]
    assert recorder._JS_GET_FORCED_QUALITY not in scripts and "max_quality.js" not in scripts
    assert result.quality == "" and result.target_height == 0
    assert "Quality: left to the site" in capsys.readouterr().out


def test_audio_sink_off_does_not_route(tmp_path, patched):
    run(tmp_path, FakePage(), FeatureSet({"audio_sink": False}))
    assert patched == []


def test_audio_sink_failure_falls_back_with_a_warning(tmp_path, patched, monkeypatch, capsys):
    monkeypatch.setattr(recorder, "route_audio_to", lambda page, label: (False, "no permission"))
    result, _ = run(tmp_path, FakePage())
    assert result.reason == "ended"
    assert "using the Windows audio setup" in capsys.readouterr().out


def test_test_mode_names_the_file_test(tmp_path, patched):
    result, _ = run(tmp_path, FakePage(duration=600), test_mode=True)
    assert result.reason == "test finished"
    assert result.file == tmp_path / "TEST - My Video.mkv"


def test_black_check_off_reports_not_checked(tmp_path, patched):
    result, _ = run(tmp_path, FakePage(), FeatureSet({"black_check": False}))
    assert result.image_ok is None
    assert recorder.status_text(result) == "OK"


# ---------- failure naming, tail wait and rename failure (outcome injected through watch) ----------


class TimedPage(FakePage):
    """Remembers every wait, to tell the tail wait from the other pauses."""

    def __init__(self) -> None:
        super().__init__()
        self.waits: list[float] = []

    def wait_for_timeout(self, ms: float) -> None:
        self.waits.append(ms)
        super().wait_for_timeout(ms)


def run_with_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: Any, tail_s: float = 0
) -> tuple[Any, FakeObs, TimedPage]:
    from vrec.monitor import WatchOutcome

    monkeypatch.setattr(
        recorder, "watch", lambda player, capture, config, output: WatchOutcome(reason=reason)
    )
    page = TimedPage()
    client = FakeObs(tmp_path / "2026-09-27 10-00-00.mkv")
    result = recorder.record_one(
        page,  # type: ignore[arg-type]
        client,  # type: ignore[arg-type]
        None,
        "Scene",
        Settings(lead_in_s=0, tail_s=tail_s, fullscreen_settle_s=0),
        FeatureSet(),
        1,
        1,
        "https://example.com/videos/1",
        "My Video",
        False,
    )
    return result, client, page


def test_black_image_is_named_failed_black(tmp_path, patched, monkeypatch):
    from vrec.monitor import StopReason

    result, _, _ = run_with_outcome(tmp_path, monkeypatch, StopReason.BLACK)
    assert result.file == tmp_path / "FAILED black image - My Video.mkv"
    assert result.file.exists()
    assert result.reason == "black image"


@pytest.mark.parametrize("name", ["STALLED", "VIDEO_GONE", "TOO_LONG", "OBS_LOST"])
def test_incomplete_reasons_are_named_incomplete(tmp_path, patched, monkeypatch, name):
    from vrec.monitor import StopReason

    result, _, _ = run_with_outcome(tmp_path, monkeypatch, StopReason[name])
    assert result.file == tmp_path / "INCOMPLETE - My Video.mkv"
    assert result.file.exists()


@pytest.mark.parametrize("name", ["ENDED", "TIME_LIMIT"])
def test_other_reasons_keep_the_plain_title(tmp_path, patched, monkeypatch, name):
    from vrec.monitor import StopReason

    result, _, _ = run_with_outcome(tmp_path, monkeypatch, StopReason[name])
    assert result.file == tmp_path / "My Video.mkv"


@pytest.mark.parametrize("name", ["BLACK", "STALLED", "OBS_LOST"])
def test_no_tail_wait_when_the_recording_is_already_lost(tmp_path, patched, monkeypatch, name):
    from vrec.monitor import StopReason

    _, _, page = run_with_outcome(tmp_path, monkeypatch, StopReason[name], tail_s=7)
    assert 7000 not in page.waits


@pytest.mark.parametrize("name", ["ENDED", "VIDEO_GONE", "TOO_LONG", "TIME_LIMIT"])
def test_tail_wait_for_the_other_reasons(tmp_path, patched, monkeypatch, name):
    from vrec.monitor import StopReason

    _, _, page = run_with_outcome(tmp_path, monkeypatch, StopReason[name], tail_s=7)
    assert page.waits[-1] == 7000


def test_rename_failure_warns_and_keeps_the_obs_name(tmp_path, patched, monkeypatch, capsys):
    from vrec.monitor import StopReason

    monkeypatch.setattr(recorder, "rename_recording", lambda path, stem: Path(path))
    result, _, _ = run_with_outcome(tmp_path, monkeypatch, StopReason.ENDED)
    assert result.file == tmp_path / "2026-09-27 10-00-00.mkv"
    assert "Couldn't rename the recording, it keeps its OBS name: 2026-09-27 10-00-00.mkv" in (
        capsys.readouterr().out
    )


def test_obs_lost_when_stop_fails_raises_a_clear_error(tmp_path, patched, monkeypatch):
    from vrec.errors import VrecError
    from vrec.monitor import StopReason

    def boom(self: FakeObs) -> None:
        raise RuntimeError("socket closed")

    monkeypatch.setattr(FakeObs, "stop_record", boom)
    with pytest.raises(VrecError, match="OBS stopped recording during the video"):
        run_with_outcome(tmp_path, monkeypatch, StopReason.OBS_LOST)


def test_stop_failure_for_another_reason_is_not_masked(tmp_path, patched, monkeypatch):
    from vrec.monitor import StopReason

    def boom(self: FakeObs) -> None:
        raise RuntimeError("socket closed")

    monkeypatch.setattr(FakeObs, "stop_record", boom)
    with pytest.raises(RuntimeError, match="socket closed"):
        run_with_outcome(tmp_path, monkeypatch, StopReason.ENDED)


def test_window_capture_follows_the_page_title(tmp_path, patched, monkeypatch, capsys):
    targeted: list[str] = []
    monkeypatch.setattr(
        recorder.obs_scene, "target_window", lambda client, capture, title: targeted.append(title) or True
    )
    result, client = run(tmp_path, FakePage(duration=70, title="Clip | Site"), window_capture="vrec window")
    assert result.reason == "ended"
    assert targeted[0] == "Clip | Site"  # pointed before the recording starts
    assert len(targeted) > 1  # and again with the liveness checks
    assert "can't find the Chrome window" not in capsys.readouterr().out


def test_window_capture_warns_when_obs_cant_see_the_window(tmp_path, patched, monkeypatch, capsys):
    monkeypatch.setattr(recorder.obs_scene, "target_window", lambda client, capture, title: False)
    run(tmp_path, FakePage(title="Clip | Site"), window_capture="vrec window")
    assert "OBS can't find the Chrome window 'Clip | Site'" in capsys.readouterr().out


def test_screen_capture_never_touches_windows(tmp_path, patched, monkeypatch):
    monkeypatch.setattr(recorder.obs_scene, "target_window", lambda *a: pytest.fail("screen mode"))
    result, _ = run(tmp_path, FakePage())
    assert result.reason == "ended"
