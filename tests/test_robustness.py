"""History robustness (save retry, unknown versions, malformed entries) and file name safety."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from vrec import history, menu
from vrec.naming import clean_title


def test_save_retries_the_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "history.json"
    real = Path.replace
    failures = {"left": 3}

    def flaky(self: Path, target: Any) -> Path:
        if failures["left"]:
            failures["left"] -= 1
            raise PermissionError("held")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", flaky)
    sleeps: list[float] = []
    history.save(path, {}, sleep=sleeps.append)
    assert sleeps == [0.2, 0.2, 0.2]
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_save_gives_up_after_five_attempts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def always(self: Path, target: Any) -> Path:
        raise PermissionError("held")

    monkeypatch.setattr(Path, "replace", always)
    sleeps: list[float] = []
    with pytest.raises(PermissionError):
        history.save(tmp_path / "history.json", {}, sleep=sleeps.append)
    assert len(sleeps) == 4


@pytest.mark.parametrize("content", ['{"version": 2, "videos": {}}', "[1, 2]", '{"version": 1, "videos": 5}'])
def test_load_sets_aside_unknown_formats(tmp_path: Path, content: str) -> None:
    path = tmp_path / "history.json"
    path.write_text(content, encoding="utf-8")
    assert history.load(path) == {}
    assert not path.exists()
    assert (tmp_path / "history.unreadable.json").read_text(encoding="utf-8") == content


def test_load_still_migrates_legacy_without_version(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text(json.dumps({"k": {"etat": "fait", "lien": "u", "titre": "T"}}), encoding="utf-8")
    assert history.load(path)["k"]["status"] == history.STATUS_DONE
    assert not (tmp_path / "history.unreadable.json").exists()


def test_malformed_entries_do_not_crash(capsys: pytest.CaptureFixture[str]) -> None:
    url = "https://x.test/some-video"
    key = history.url_key(url)
    for entry in ({}, {"title": None, "date": "garbage"}, "oops", None):
        videos: Any = {key: entry}
        assert history.status_of(videos, url) is None
        assert history.display_title(videos, url, None) == "some video"
        menu.show_list([(url, None)], videos)
    assert "[NEW" in capsys.readouterr().out


def test_show_list_malformed_date_is_skipped(capsys: pytest.CaptureFixture[str]) -> None:
    url = "https://x.test/v"
    videos: Any = {history.url_key(url): {"status": "done", "date": "2025-xx", "detail": "ok"}}
    menu.show_list([(url, "V")], videos)
    out = capsys.readouterr().out
    assert "[DONE" in out and "(ok)" in out


@pytest.mark.parametrize("name", ["CON", "con", "NUL.txt", "com1", "LPT9", "aux.mkv"])
def test_clean_title_reserved_names(name: str) -> None:
    cleaned = clean_title(name)
    assert cleaned.split(".")[0].endswith("_")
    assert cleaned.split(".")[0].upper() not in {"CON", "NUL", "COM1", "LPT9", "AUX"}


def test_clean_title_keeps_ordinary_names() -> None:
    assert clean_title("Console") == "Console"
    assert clean_title("COM10") == "COM10"


def test_clean_title_strips_control_characters() -> None:
    assert clean_title("a\x00b\x1fc\x7fd\te") == "a b c d e"
