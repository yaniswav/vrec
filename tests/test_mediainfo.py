"""Duration check: the Windows reader (faked), and what vrec does with the length of a file."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_app import make_batch, make_record, make_result

from vrec import app, history, mediainfo, menu
from vrec.library import Match
from vrec.playlist import url_key
from vrec.recorder import status_text

URL = "https://videos.example.test/clip/12345/some-title"


# ---------- the reader ----------


def test_file_duration_converts_100ns_ticks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mediainfo, "_read_duration_ticks", lambda path: 11_930_000_000)
    assert mediainfo.file_duration_s("x.mp4") == pytest.approx(1193.0)


@pytest.mark.parametrize("ticks", [None, 0])
def test_file_duration_unknown(monkeypatch: pytest.MonkeyPatch, ticks: int | None) -> None:
    monkeypatch.setattr(mediainfo, "_read_duration_ticks", lambda path: ticks)
    assert mediainfo.file_duration_s(Path("x.mp4")) is None


def test_file_duration_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(path: str) -> int:
        raise OSError("no shell")

    monkeypatch.setattr(mediainfo, "_read_duration_ticks", broken)
    assert mediainfo.file_duration_s("x.mp4") is None


# ---------- is_cut_short ----------


@pytest.mark.parametrize(
    ("file_s", "video_s", "short"),
    [
        (723, 1193, True),  # 12:03 of 19:53
        (1193, 1193, False),
        (1200, 1193, False),  # the recording has a lead-in and a tail
        (1500, 1193, False),
        (1150, 1193, False),  # 96%, only 43 s short
        (1100, 1193, True),  # 92% and 93 s short
        (None, 1193, False),
        (0, 1193, False),
        (100, 0, False),
        (50, 70, False),  # 71% but only 20 s short: a short clip is never flagged on the ratio alone
    ],
)
def test_is_cut_short(file_s: float | None, video_s: float, short: bool) -> None:
    assert mediainfo.is_cut_short(file_s, video_s) is short


# ---------- after a recording ----------


def _batch_with_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_s: float | None, **extra: object):
    monkeypatch.setattr(mediainfo, "file_duration_s", lambda path: file_s)
    batch = make_batch(tmp_path, **extra)
    recording = tmp_path / "Video.mkv"
    recording.touch()
    record = make_record([make_result(file=recording, duration_s=1193.0)])
    return batch, record


def test_short_file_is_marked_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    batch, record = _batch_with_file(tmp_path, monkeypatch, 723)

    app._record_batch(batch, [(URL, "Video")], record=record)

    entry = batch.videos_history[url_key(URL)]
    assert entry["status"] == history.STATUS_REVIEW
    assert entry["detail"] == "file shorter than the video (12:03 of 19:53)"
    assert entry["duration"] == 1193
    assert status_text(batch.results[0]).startswith("CHECK: file shorter")
    assert "file shorter than the video (12:03 of 19:53)" in capsys.readouterr().out


@pytest.mark.parametrize("file_s", [1195, 1400, None])
def test_normal_longer_or_unknown_file_stays_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_s: float | None
) -> None:
    batch, record = _batch_with_file(tmp_path, monkeypatch, file_s)

    app._record_batch(batch, [(URL, "Video")], record=record)

    entry = batch.videos_history[url_key(URL)]
    assert entry["status"] == history.STATUS_DONE
    assert entry["detail"] == ""
    assert entry["duration"] == 1193


def test_unknown_video_length_is_not_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mediainfo, "file_duration_s", lambda path: pytest.fail("read for nothing"))
    batch = make_batch(tmp_path)
    recording = tmp_path / "Video.mkv"
    recording.touch()
    record = make_record([make_result(file=recording, duration_s=0.0)])

    app._record_batch(batch, [(URL, "Video")], record=record)

    entry = batch.videos_history[url_key(URL)]
    assert entry["status"] == history.STATUS_DONE
    assert "duration" not in entry  # old-style entry: still valid


def test_a_recording_already_marked_for_review_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mediainfo, "file_duration_s", lambda path: pytest.fail("read for nothing"))
    batch = make_batch(tmp_path)
    recording = tmp_path / "Video.mkv"
    recording.touch()
    record = make_record([make_result(file=recording, duration_s=1193.0, image_ok=False)])

    app._record_batch(batch, [(URL, "Video")], record=record)

    assert batch.videos_history[url_key(URL)]["status"] == history.STATUS_REVIEW


def test_test_mode_skips_the_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mediainfo, "file_duration_s", lambda path: pytest.fail("read for nothing"))
    batch = make_batch(tmp_path, test_mode=True)
    recording = tmp_path / "Video.mkv"
    recording.touch()
    record = make_record([make_result(file=recording, duration_s=1193.0)])

    app._record_batch(batch, [(URL, "Video")], record=record)

    assert batch.results[0].file_problem == ""


# ---------- history ----------


def test_old_entries_without_a_duration_still_load(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    videos: history.Videos = {}
    history.record(path, videos, URL, "A", history.STATUS_DONE)
    assert "duration" not in videos[url_key(URL)]
    history.record(path, videos, URL, "A", history.STATUS_DONE, duration=1193.4)
    assert history.load(path)[url_key(URL)]["duration"] == 1193


# ---------- the list of found files ----------


def test_found_files_show_their_length(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lengths = {"A.mp4": 1191.0, "B.mp4": None}
    calls: list[Path] = []

    def fake(path: str | Path) -> float | None:
        calls.append(Path(path))
        return lengths[Path(path).name]

    monkeypatch.setattr(mediainfo, "file_duration_s", fake)
    found = [
        (f"{URL}1", "A", Match(tmp_path / "A.mp4", True)),
        (f"{URL}2", "B", Match(tmp_path / "B.mp4", True)),
    ]

    menu.offer_found_files(found, {}, tmp_path / "history.json", False)

    out = capsys.readouterr().out
    assert f"-> {tmp_path / 'A.mp4'}  (19:51)" in out
    assert f"-> {tmp_path / 'B.mp4'}\n" in out
    assert len(calls) == 2  # one read per file shown
