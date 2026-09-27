"""Tests for vrec.playlist: reading videos.txt and normalizing URLs."""

from __future__ import annotations

from pathlib import Path

from vrec.playlist import read_playlist, url_key


def test_bare_links(tmp_path: Path) -> None:
    path = tmp_path / "videos.txt"
    path.write_text("https://example.com/a\nhttps://example.com/b\n", encoding="utf-8")
    videos = read_playlist(path)
    assert videos == [
        ("https://example.com/a", None),
        ("https://example.com/b", None),
    ]


def test_title_line_then_link(tmp_path: Path) -> None:
    path = tmp_path / "videos.txt"
    path.write_text("My Video Title\nhttps://example.com/a\n", encoding="utf-8")
    videos = read_playlist(path)
    assert videos == [("https://example.com/a", "My Video Title")]


def test_title_on_same_line_before_link(tmp_path: Path) -> None:
    path = tmp_path / "videos.txt"
    path.write_text("My Video Title https://example.com/a\n", encoding="utf-8")
    videos = read_playlist(path)
    assert videos == [("https://example.com/a", "My Video Title")]


def test_comments_and_blank_lines_ignored(tmp_path: Path) -> None:
    path = tmp_path / "videos.txt"
    path.write_text(
        "# a comment\n\nhttps://example.com/a\n\n# another comment\nhttps://example.com/b\n",
        encoding="utf-8",
    )
    videos = read_playlist(path)
    assert videos == [
        ("https://example.com/a", None),
        ("https://example.com/b", None),
    ]


def test_dedup_by_query_string(tmp_path: Path) -> None:
    path = tmp_path / "videos.txt"
    path.write_text(
        "https://example.com/a?x=1\nhttps://example.com/a?x=2\n",
        encoding="utf-8",
    )
    videos = read_playlist(path)
    assert len(videos) == 1
    assert videos[0][0] == "https://example.com/a?x=1"


def test_dedup_by_case(tmp_path: Path) -> None:
    path = tmp_path / "videos.txt"
    path.write_text(
        "https://Example.com/A\nhttps://example.com/a\n",
        encoding="utf-8",
    )
    videos = read_playlist(path)
    assert len(videos) == 1


def test_dedup_by_trailing_slash(tmp_path: Path) -> None:
    path = tmp_path / "videos.txt"
    path.write_text(
        "https://example.com/a/\nhttps://example.com/a\n",
        encoding="utf-8",
    )
    videos = read_playlist(path)
    assert len(videos) == 1


def test_invisible_characters_stripped(tmp_path: Path) -> None:
    path = tmp_path / "videos.txt"
    # Zero-width space (U+200B) inside the title and right after the URL.
    zwsp = "​"
    path.write_text(
        f"My{zwsp} Title\nhttps://example.com/a{zwsp}\n",
        encoding="utf-8",
    )
    videos = read_playlist(path)
    assert videos == [("https://example.com/a", "My Title")]


def test_cp1252_fallback(tmp_path: Path) -> None:
    path = tmp_path / "videos.txt"
    # "Café vidéo" encoded as cp1252 is not valid utf-8 (0xE9 is a lone continuation byte).
    text = "Café vidéo\nhttps://example.com/a\n"
    path.write_bytes(text.encode("cp1252"))
    videos = read_playlist(path)
    assert videos == [("https://example.com/a", "Café vidéo")]


def test_utf8_sig_bom_handled(tmp_path: Path) -> None:
    path = tmp_path / "videos.txt"
    path.write_bytes("https://example.com/a\n".encode("utf-8-sig"))
    videos = read_playlist(path)
    assert videos == [("https://example.com/a", None)]


def test_title_carried_from_previous_non_url_line_only_once(tmp_path: Path) -> None:
    path = tmp_path / "videos.txt"
    path.write_text(
        "Title One\nhttps://example.com/a\nhttps://example.com/b\n",
        encoding="utf-8",
    )
    videos = read_playlist(path)
    assert videos == [
        ("https://example.com/a", "Title One"),
        ("https://example.com/b", None),
    ]


def test_url_key_strips_fragment_and_query() -> None:
    assert url_key("https://example.com/a?x=1#y") == "https://example.com/a"


def test_url_key_lowercases() -> None:
    assert url_key("https://Example.COM/A") == "https://example.com/a"


def test_url_key_strips_trailing_slash() -> None:
    assert url_key("https://example.com/a/") == "https://example.com/a"


def test_url_key_stable_for_equivalent_urls() -> None:
    a = url_key("https://Example.com/A/?ref=share#top")
    b = url_key("https://example.com/a")
    assert a == b
