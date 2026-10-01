"""Tests for vrec.history: migration, persistence, and lookups."""

from __future__ import annotations

import json
from pathlib import Path

from vrec import history
from vrec.playlist import legacy_url_key, url_key


def test_legacy_migration_keys_statuses_and_details(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    legacy = {
        "key1": {
            "etat": "fait",
            "detail": "fichier retrouvé",
            "lien": "https://example.com/a",
            "titre": "Video A",
            "fichier": "Video A.mp4",
            "qualite": "1080p",
            "date": "2021-01-01 10:00",
        },
        "key2": {
            "etat": "a_revoir",
            "detail": "chargement bloqué",
            "lien": "https://example.com/b",
            "titre": "Video B",
            "fichier": "",
            "qualite": "",
            "date": "2021-02-02 11:00",
        },
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")

    videos = history.load(path)

    assert videos["key1"]["status"] == history.STATUS_DONE
    assert videos["key1"]["detail"] == "found on disk"
    assert videos["key1"]["url"] == "https://example.com/a"
    assert videos["key1"]["title"] == "Video A"
    assert videos["key1"]["file"] == "Video A.mp4"
    assert videos["key1"]["quality"] == "1080p"
    assert videos["key1"]["date"] == "2021-01-01 10:00"

    assert videos["key2"]["status"] == history.STATUS_REVIEW
    assert videos["key2"]["detail"] == "loading stalled"


def test_legacy_migration_resaves_as_v1(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    legacy = {"key1": {"etat": "nouvelle", "lien": "https://example.com/a", "titre": "A"}}
    path.write_text(json.dumps(legacy), encoding="utf-8")

    history.load(path)

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["version"] == 1
    assert on_disk["videos"]["key1"]["status"] == history.STATUS_NEW


def test_v1_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    videos: history.Videos = {
        "https://example.com/a": {
            "url": "https://example.com/a",
            "title": "A",
            "status": history.STATUS_DONE,
            "detail": "",
            "file": "A.mp4",
            "quality": "1080p",
            "date": "2024-01-01 00:00",
        }
    }
    history.save(path, videos)
    loaded = history.load(path)
    assert loaded == videos


def test_unreadable_file_set_aside(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text("not valid json {{{", encoding="utf-8")

    videos = history.load(path)

    assert videos == {}
    broken = tmp_path / "history.unreadable.json"
    assert broken.exists()
    assert broken.read_text(encoding="utf-8") == "not valid json {{{"
    assert not path.exists()


def test_missing_file_gives_empty_history(tmp_path: Path) -> None:
    assert history.load(tmp_path / "does-not-exist.json") == {}


def test_record_and_status_of(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    videos: history.Videos = {}
    history.record(path, videos, "https://example.com/a", "A", history.STATUS_DONE, "", "A.mp4", "1080p")

    assert history.status_of(videos, "https://example.com/a") == history.STATUS_DONE
    assert history.status_of(videos, "https://EXAMPLE.com/a/") == history.STATUS_DONE  # url_key normalized
    assert history.status_of(videos, "https://example.com/missing") is None

    on_disk = history.load(path)
    assert on_disk == videos


def test_display_title_prefers_given_title() -> None:
    videos: history.Videos = {}
    assert history.display_title(videos, "https://example.com/a", "Explicit Title") == "Explicit Title"


def test_display_title_falls_back_to_known_title() -> None:
    videos: history.Videos = {"https://example.com/a": {"title": "Known Title"}}
    assert history.display_title(videos, "https://example.com/a", None) == "Known Title"


def test_display_title_falls_back_to_derived_from_url() -> None:
    videos: history.Videos = {}
    result = history.display_title(videos, "https://example.com/my-video-name", None)
    assert result == "my video name"


def test_display_title_falls_back_to_raw_url_when_nothing_derivable() -> None:
    videos: history.Videos = {}
    assert history.display_title(videos, "###", None) == "###"


def test_label() -> None:
    assert history.label(history.STATUS_DONE) == "DONE"
    assert history.label(history.STATUS_MARKED) == "DONE"
    assert history.label(history.STATUS_REVIEW) == "REVIEW"
    assert history.label(history.STATUS_FAILED) == "FAILED"
    assert history.label(history.STATUS_NEW) == "NEW"
    assert history.label(None) == "NEW"
    assert history.label("something-unknown") == "NEW"


def test_adopt_existing_files_marks_found_files_done(tmp_path: Path) -> None:
    folder = tmp_path / "recordings"
    folder.mkdir()
    (folder / "My Video.mp4").touch()
    history_path = tmp_path / "history.json"

    videos_list = [("https://example.com/a", "My Video")]
    videos: history.Videos = {}
    history.adopt_existing_files(videos_list, videos, folder, history_path)

    entry = videos["https://example.com/a"]
    assert entry["status"] == history.STATUS_DONE
    assert entry["detail"] == "found on disk"
    assert entry["file"] == str(folder / "My Video.mp4")

    on_disk = history.load(history_path)
    assert on_disk == videos


def test_adopt_existing_files_skips_excluded_prefixes(tmp_path: Path) -> None:
    folder = tmp_path / "recordings"
    folder.mkdir()
    (folder / "TEST - My Video.mp4").touch()
    history_path = tmp_path / "history.json"

    videos_list = [("https://example.com/a", "My Video")]
    videos: history.Videos = {}
    history.adopt_existing_files(videos_list, videos, folder, history_path)

    assert videos == {}


def test_adopt_existing_files_skips_already_known(tmp_path: Path) -> None:
    folder = tmp_path / "recordings"
    folder.mkdir()
    (folder / "My Video.mp4").touch()
    history_path = tmp_path / "history.json"

    videos_list = [("https://example.com/a", "My Video")]
    videos: history.Videos = {"https://example.com/a": {"status": history.STATUS_FAILED}}
    history.adopt_existing_files(videos_list, videos, folder, history_path)

    # Already-known videos are left alone, not overwritten with "found on disk".
    assert videos["https://example.com/a"]["status"] == history.STATUS_FAILED


def _legacy_entry(url: str, status: str = history.STATUS_DONE) -> dict[str, str]:
    return {"url": url, "title": "T", "status": status, "detail": "", "file": "", "quality": "", "date": ""}


def test_load_migrates_old_format_keys_and_saves(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    old_key = "https://example.com/watch"  # legacy key: query dropped, lowercased
    url = "https://example.com/watch?v=A"
    path.write_text(json.dumps({"version": 1, "videos": {old_key: _legacy_entry(url)}}), encoding="utf-8")

    videos = history.load(path)

    assert old_key not in videos
    assert history.status_of(videos, url) == history.STATUS_DONE
    assert history.status_of(videos, "https://example.com/watch?v=B") is None
    on_disk = json.loads(path.read_text(encoding="utf-8"))["videos"]
    assert list(on_disk) == [url_key(url)]


def test_load_migrates_mixed_case_old_key(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    url = "https://Example.com/Video/ABC/"
    path.write_text(
        json.dumps({"version": 1, "videos": {legacy_url_key(url): _legacy_entry(url)}}), encoding="utf-8"
    )
    videos = history.load(path)
    assert history.status_of(videos, url) == history.STATUS_DONE
    assert history.status_of(videos, "https://example.com/Video/ABC") == history.STATUS_DONE


def test_load_keeps_existing_new_key_entry(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    url = "https://example.com/a"
    path.write_text(
        json.dumps({"version": 1, "videos": {url_key(url): _legacy_entry(url, history.STATUS_REVIEW)}}),
        encoding="utf-8",
    )
    assert history.status_of(history.load(path), url) == history.STATUS_REVIEW
