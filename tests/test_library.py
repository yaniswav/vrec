"""Tests for vrec.library: scanning folder trees and matching titles to file names."""

from __future__ import annotations

from pathlib import Path

import pytest

from vrec import library


def make(tmp_path: Path, *relative: str) -> Path:
    for name in relative:
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.touch()
    return tmp_path


def found(root: Path, title: str) -> library.Match | None:
    return library.scan([root]).find(title)


def test_normalize_accents_case_quotes_and_punctuation() -> None:
    assert library.normalize("Café, l’été !") == "cafe lete"
    assert library.normalize("Don't  Stop") == library.normalize("Don’t stop")
    assert library.normalize("A_B  C") == "a b c"
    assert library.normalize("¿?") == ""


def test_exact_match_in_nested_folders(tmp_path: Path) -> None:
    make(tmp_path, "VR/À faire/My Video.mp4", "other/readme.txt")
    match = found(tmp_path, "My Video")
    assert match == library.Match(tmp_path / "VR" / "À faire" / "My Video.mp4", True)


@pytest.mark.parametrize(
    "name",
    [
        "My Video_360.mp4",
        "My Video_3D_360_TB.mp4",
        "My Video_180.mp4",
        "My Video_3D_180_SBS.mp4",
        "My Video_SBS.mp4",
        "My Video_TB.mkv",
        "My Video_LR.mov",
        "My Video (2).mp4",
        "My Video_360 (2).mp4",
        "12 - My Video.mp4",
        "my  video.MP4",
    ],
)
def test_suffixes_and_prefixes_are_ignored(tmp_path: Path, name: str) -> None:
    make(tmp_path, name)
    match = found(tmp_path, "My Video")
    assert match is not None
    assert match.exact
    assert match.path.name == name


def test_accents_and_curly_quotes_match(tmp_path: Path) -> None:
    make(tmp_path, "Léa's Été Party.mp4")
    match = found(tmp_path, "Lea’s ete party")
    assert match is not None
    assert match.exact


def test_title_ending_with_a_suffix_word_still_matches_itself(tmp_path: Path) -> None:
    make(tmp_path, "Fisheye Clip 180.mp4")
    match = found(tmp_path, "Fisheye Clip 180")
    assert match is not None
    assert match.exact


def test_truncated_title_matches_a_file_named_with_the_cut_name(tmp_path: Path) -> None:
    long_title = "A very long title " * 10
    cut = long_title.strip()[:100]
    make(tmp_path, f"{cut}.mp4")
    match = found(tmp_path, long_title.strip())
    assert match is not None
    assert match.exact


def test_fuzzy_match_is_not_exact(tmp_path: Path) -> None:
    make(tmp_path, "The Long Walk Through The Forest Trail.mp4")
    match = found(tmp_path, "The Long Walk Through The Forest Trails")
    assert match is not None
    assert not match.exact


def test_different_titles_do_not_match(tmp_path: Path) -> None:
    make(tmp_path, "The Long Walk Through The Forest Trail.mp4")
    assert found(tmp_path, "The Long Walk Through The City Streets") is None


def test_short_titles_are_never_fuzzy_matched(tmp_path: Path) -> None:
    make(tmp_path, "Intro Part A.mp4")
    assert found(tmp_path, "Intro Part B") is None


def test_length_difference_blocks_a_fuzzy_match(tmp_path: Path) -> None:
    make(tmp_path, "The Long Walk Through The Forest.mp4")
    assert found(tmp_path, "The Long Walk Through The Forest And Beyond The Hills") is None


@pytest.mark.parametrize(
    "name",
    [
        "TEST - My Video.mp4",
        "INCOMPLETE - My Video.mp4",
        "INTERRUPTED - My Video.mp4",
        "SKIPPED - My Video.mp4",
        "FAILED black image - My Video.mp4",
        "FAILED frozen image - My Video.mp4",
        "ECHEC - My Video.mp4",
        "My Video FAILED.mp4",
    ],
)
def test_vrec_leftovers_are_ignored(tmp_path: Path, name: str) -> None:
    make(tmp_path, name)
    assert found(tmp_path, "My Video") is None


def test_a_title_starting_with_test_is_not_a_leftover(tmp_path: Path) -> None:
    make(tmp_path, "Testing My Patience.mp4")
    assert found(tmp_path, "Testing My Patience") is not None


def test_non_video_files_are_ignored(tmp_path: Path) -> None:
    make(tmp_path, "My Video.txt", "My Video.jpg", "My Video.mp4.part")
    assert found(tmp_path, "My Video") is None


@pytest.mark.parametrize("ext", sorted(library.VIDEO_EXTENSIONS))
def test_video_extensions(tmp_path: Path, ext: str) -> None:
    make(tmp_path, f"My Video{ext}")
    assert found(tmp_path, "My Video") is not None


def test_missing_and_empty_folders_are_skipped(tmp_path: Path) -> None:
    make(tmp_path, "real/My Video.mp4")
    lib = library.scan([None, "", tmp_path / "missing", tmp_path / "real"])
    assert lib.find("My Video") is not None
    assert len(lib) == 1


def test_skipped_system_folders(tmp_path: Path) -> None:
    make(tmp_path, "$RECYCLE.BIN/My Video.mp4")
    assert found(tmp_path, "My Video") is None


def test_the_scan_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library, "MAX_FILES", 2)
    make(tmp_path, "a one.mp4", "b two.mp4", "c three.mp4")
    assert len(library.scan([tmp_path])) == 2


def test_same_file_through_two_folders_is_indexed_once(tmp_path: Path) -> None:
    make(tmp_path, "sub/My Video.mp4")
    assert len(library.scan([tmp_path, tmp_path / "sub"])) == 1


def test_find_without_a_title(tmp_path: Path) -> None:
    assert library.scan([tmp_path]).find(None) is None
    assert library.scan([tmp_path]).find("!!!") is None
