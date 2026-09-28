"""Tests for vrec.naming: title cleanup, existing-recording lookup, and renaming."""

from __future__ import annotations

from pathlib import Path

from vrec.naming import clean_title, find_existing_recording, rename_recording


def test_clean_title_strips_site_suffix() -> None:
    assert clean_title("My Great Video | SiteName") == "My Great Video"


def test_clean_title_long_site_suffix_keeps_text_but_loses_pipe() -> None:
    # The suffix regex only swallows a trailing " | <=20 chars" as a whole; past that
    # length the text survives, but the "|" itself is still stripped as a forbidden char.
    long_suffix = "x" * 25
    text = f"My Video | {long_suffix}"
    assert clean_title(text) == f"My Video {long_suffix}"


def test_clean_title_strips_forbidden_chars() -> None:
    assert clean_title('a\\b/c:d*e?f"g<h>i') == "abcdefghi"


def test_clean_title_collapses_whitespace() -> None:
    assert clean_title("a   b\t\tc\n\nd") == "a b c d"


def test_clean_title_strips_trailing_dot_and_surrounding_space() -> None:
    assert clean_title("  My Title.  ") == "My Title"


def test_clean_title_caps_at_100_chars() -> None:
    text = "a" * 150
    result = clean_title(text)
    assert len(result) == 100
    assert result == "a" * 100


def test_clean_title_empty_gives_video() -> None:
    assert clean_title("") == "video"
    assert clean_title(None) == "video"
    assert clean_title("   ") == "video"
    assert clean_title('***"""') == "video"


def test_clean_title_strips_invisible_chars() -> None:
    assert clean_title("My​ Title") == "My Title"


def test_find_existing_recording_exact_stem(tmp_path: Path) -> None:
    (tmp_path / "My Video.mp4").touch()
    found = find_existing_recording(tmp_path, "My Video")
    assert found == tmp_path / "My Video.mp4"


def test_find_existing_recording_legacy_numbered_prefix(tmp_path: Path) -> None:
    (tmp_path / "01 - My Video.mkv").touch()
    found = find_existing_recording(tmp_path, "My Video")
    assert found == tmp_path / "01 - My Video.mkv"


def test_find_existing_recording_excludes_test_prefix(tmp_path: Path) -> None:
    (tmp_path / "TEST - My Video.mp4").touch()
    assert find_existing_recording(tmp_path, "My Video") is None


def test_find_existing_recording_excludes_incomplete_prefix(tmp_path: Path) -> None:
    (tmp_path / "INCOMPLETE - My Video.mp4").touch()
    assert find_existing_recording(tmp_path, "My Video") is None


def test_find_existing_recording_excludes_interrupted_prefix(tmp_path: Path) -> None:
    (tmp_path / "INTERRUPTED - My Video.mp4").touch()
    assert find_existing_recording(tmp_path, "My Video") is None


def test_find_existing_recording_excludes_failed(tmp_path: Path) -> None:
    (tmp_path / "FAILED black image - My Video.mp4").touch()
    assert find_existing_recording(tmp_path, "My Video") is None


def test_find_existing_recording_excludes_legacy_echec(tmp_path: Path) -> None:
    (tmp_path / "My Video ECHEC.mp4").touch()
    assert find_existing_recording(tmp_path, "My Video ECHEC") is None


def test_find_existing_recording_missing_folder(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    assert find_existing_recording(missing, "My Video") is None


def test_find_existing_recording_none_folder() -> None:
    assert find_existing_recording(None, "My Video") is None


def test_find_existing_recording_none_title(tmp_path: Path) -> None:
    (tmp_path / "My Video.mp4").touch()
    assert find_existing_recording(tmp_path, None) is None


def test_find_existing_recording_empty_folder() -> None:
    assert find_existing_recording("", "My Video") is None


def test_rename_recording_no_collision(tmp_path: Path) -> None:
    src = tmp_path / "raw.mkv"
    src.write_text("data", encoding="utf-8")
    result = rename_recording(src, "My Video")
    assert result == tmp_path / "My Video.mkv"
    assert result.exists()


def test_rename_recording_collision_appends_number(tmp_path: Path) -> None:
    (tmp_path / "My Video.mkv").write_text("existing", encoding="utf-8")
    src = tmp_path / "raw.mkv"
    src.write_text("data", encoding="utf-8")
    result = rename_recording(src, "My Video")
    assert result == tmp_path / "My Video (2).mkv"
    assert result.exists()


def test_rename_recording_double_collision_appends_next_number(tmp_path: Path) -> None:
    (tmp_path / "My Video.mkv").write_text("existing", encoding="utf-8")
    (tmp_path / "My Video (2).mkv").write_text("existing", encoding="utf-8")
    src = tmp_path / "raw.mkv"
    src.write_text("data", encoding="utf-8")
    result = rename_recording(src, "My Video")
    assert result == tmp_path / "My Video (3).mkv"
    assert result.exists()
