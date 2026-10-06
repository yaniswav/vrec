"""The library lookup at startup: history helpers, the confirmation prompt, and the app wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from vrec import app, history, library, menu
from vrec.config import Paths, Settings
from vrec.library import scan
from vrec.playlist import url_key

_LISTED: list[tuple[str, str | None]] = [("https://videos.example.test/clip/12345/a", "My Video")]


def _entry(title: str, file: Path | str, status: str = history.STATUS_DONE) -> history.VideoRecord:
    return {
        "url": "https://videos.example.test/clip/1",
        "title": title,
        "status": status,
        "detail": "",
        "file": str(file),
        "quality": "",
        "date": "",
    }


def script(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> list[str]:
    """Feed `answers` to input(); asking more than that fails the test."""
    prompts: list[str] = []
    it = iter(answers)

    def fake_input(prompt: str = "") -> str:
        prompts.append(prompt)
        try:
            return next(it)
        except StopIteration:
            pytest.fail(f"unexpected extra prompt: {prompt!r}")

    monkeypatch.setattr("builtins.input", fake_input)
    return prompts


# ---------- history helpers ----------


def test_find_on_disk_lists_unknown_videos_only(tmp_path: Path) -> None:
    (tmp_path / "Known Video.mp4").touch()
    (tmp_path / "Brand New Video.mp4").touch()
    listed: list[tuple[str, str | None]] = [
        ("https://videos.example.test/clip/11111/a", "Known Video"),
        ("https://videos.example.test/clip/22222/b", "Brand New Video"),
        ("https://videos.example.test/clip/33333/c", "Not Here At All"),
        ("https://videos.example.test/clip/44444/d", None),
    ]
    videos: history.Videos = {url_key(listed[0][0]): _entry("Known Video", "x")}

    found = history.find_on_disk(listed, videos, scan([tmp_path]))

    assert [(url, title, match.path.name) for url, title, match in found] == [
        ("https://videos.example.test/clip/22222/b", "Brand New Video", "Brand New Video.mp4")
    ]


def test_relink_moved_files_updates_only_the_path(tmp_path: Path) -> None:
    moved = tmp_path / "VR" / "Prêt" / "My Video_360.mp4"
    moved.parent.mkdir(parents=True)
    moved.touch()
    here = tmp_path / "Still Here.mp4"
    here.touch()
    videos: history.Videos = {
        "a": _entry("My Video", tmp_path / "gone" / "My Video.mp4"),
        "b": _entry("Still Here", here),
        "c": _entry("Lost Forever", tmp_path / "gone" / "Lost Forever.mp4"),
        "d": _entry("My Video", tmp_path / "gone" / "x.mp4", history.STATUS_FAILED),
        "e": _entry("My Video", "", history.STATUS_MARKED),
    }
    path = tmp_path / "history.json"

    assert history.relink_moved_files(videos, scan([tmp_path]), path) == 1

    assert videos["a"]["file"] == str(moved)
    assert videos["a"]["status"] == history.STATUS_DONE
    assert videos["b"]["file"] == str(here)
    assert videos["c"]["status"] == history.STATUS_DONE  # a missing file never downgrades a done entry
    assert videos["c"]["file"].endswith("Lost Forever.mp4")
    assert videos["d"]["status"] == history.STATUS_FAILED
    assert history.load(path)["a"]["file"] == str(moved)


def test_relink_ignores_a_fuzzy_match(tmp_path: Path) -> None:
    (tmp_path / "The Long Walk Through The Forest Trail.mp4").touch()
    videos: history.Videos = {"a": _entry("The Long Walk Through The Forest Trails", tmp_path / "gone.mp4")}
    assert history.relink_moved_files(videos, scan([tmp_path]), tmp_path / "history.json") == 0
    assert not (tmp_path / "history.json").exists()


# ---------- the confirmation prompt ----------

FOUND = [
    ("https://x.test/videos/1", "Video 1", library.Match(Path("D:/VR/Video 1_360.mp4"), True)),
    ("https://x.test/videos/2", "Video 2", library.Match(Path("D:/VR/Video 2.mp4"), False)),
]


def _offer(tmp_path: Path, videos: history.Videos, interactive: bool = True) -> int:
    return menu.offer_found_files(FOUND, videos, tmp_path / "history.json", interactive)


def test_offer_nothing_found(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert menu.offer_found_files([], {}, tmp_path / "history.json", True) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("answer", ["", "y", "YES"])
def test_offer_yes_marks_them_done(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], answer: str
) -> None:
    prompts = script(monkeypatch, [answer])
    videos: history.Videos = {}

    assert _offer(tmp_path, videos) == 2

    out = capsys.readouterr().out
    assert "2 video(s) look already downloaded:" in out
    assert "  Video 1\n     -> " in out
    assert prompts == ["Mark them as done? [Y/n]: "]
    entry = videos[url_key("https://x.test/videos/1")]
    assert entry["status"] == history.STATUS_DONE
    assert entry["detail"] == "found on disk"
    assert entry["file"] == str(FOUND[0][2].path)
    assert history.load(tmp_path / "history.json") == videos


@pytest.mark.parametrize("answer", ["n", "No", "q"])
def test_offer_no_marks_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, answer: str) -> None:
    script(monkeypatch, [answer])
    videos: history.Videos = {}
    assert _offer(tmp_path, videos) == 0
    assert videos == {}


def test_offer_asks_again_on_a_bad_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script(monkeypatch, ["maybe", "y"])
    assert _offer(tmp_path, {}) == 2
    assert "Please answer y or n" in capsys.readouterr().out


def test_offer_end_of_input_is_no(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError()))
    assert _offer(tmp_path, {}) == 0


def test_offer_never_asks_when_not_interactive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script(monkeypatch, [])  # any prompt fails the test
    videos: history.Videos = {}
    assert _offer(tmp_path, videos, interactive=False) == 0
    out = capsys.readouterr().out
    assert "2 video(s) look already downloaded:" in out
    assert "Not marked as done (no one to ask): they will be recorded." in out
    assert videos == {}


# ---------- app._look_in_library ----------


def _library_setup(tmp_path: Path) -> tuple[Paths, Settings, Path]:
    folder = tmp_path / "lib" / "sorted"
    folder.mkdir(parents=True)
    (folder / "My Video_360.mp4").touch()
    paths = Paths(data_dir=tmp_path, config=tmp_path / "config.toml")
    return paths, Settings(library_folders=(str(tmp_path / "lib"),)), folder


def test_look_in_library_offers_files_from_library_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths, settings, folder = _library_setup(tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.setattr(app.hotkeys, "available", lambda feature_on: True)
    videos_history: history.Videos = {}

    app._look_in_library(_LISTED, videos_history, paths, settings, None)

    assert "1 video(s) look already downloaded:" in capsys.readouterr().out
    entry = videos_history[url_key(_LISTED[0][0])]
    assert entry["status"] == history.STATUS_DONE
    assert entry["file"] == str(folder / "My Video_360.mp4")


def test_look_in_library_does_not_mark_when_nobody_can_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, settings, _ = _library_setup(tmp_path)
    monkeypatch.setattr(app.hotkeys, "available", lambda feature_on: False)
    videos_history: history.Videos = {}
    app._look_in_library(_LISTED, videos_history, paths, settings, None)
    assert videos_history == {}


def test_look_in_library_skips_the_scan_when_there_is_nothing_to_find(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, settings, _ = _library_setup(tmp_path)
    monkeypatch.setattr(app, "scan", lambda folders: pytest.fail("scanned for nothing"))
    videos_history: history.Videos = {url_key(_LISTED[0][0]): _entry("My Video", "")}
    app._look_in_library(_LISTED, videos_history, paths, settings, None)


def test_look_in_library_updates_the_path_of_a_moved_done_video(tmp_path: Path) -> None:
    paths, settings, folder = _library_setup(tmp_path)
    key = url_key(_LISTED[0][0])
    videos_history: history.Videos = {key: _entry("My Video", tmp_path / "obs" / "My Video.mp4")}
    app._look_in_library(_LISTED, videos_history, paths, settings, None)
    assert videos_history[key]["file"] == str(folder / "My Video_360.mp4")
    assert videos_history[key]["status"] == history.STATUS_DONE
