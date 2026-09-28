"""Tests for vrec.menu.parse_numbers."""

from __future__ import annotations

import pytest

from vrec.menu import parse_numbers


def test_single_list() -> None:
    assert parse_numbers("3,1,5-8", 10) == [3, 1, 5, 6, 7, 8]


def test_reversed_range() -> None:
    assert parse_numbers("8-5", 10) == [8, 7, 6, 5]


def test_dedup_keeps_first_occurrence() -> None:
    assert parse_numbers("1,2,1,2,3", 10) == [1, 2, 3]


def test_spaces_and_semicolons_as_separators() -> None:
    assert parse_numbers("1 2;3", 10) == [1, 2, 3]


def test_dash_with_spaces_form() -> None:
    assert parse_numbers("3 - 5", 10) == [3, 4, 5]


def test_mixed_separators() -> None:
    assert parse_numbers("1, 2 ;3-4", 10) == [1, 2, 3, 4]


def test_out_of_range_raises() -> None:
    with pytest.raises(ValueError):
        parse_numbers("11", 10)


def test_zero_out_of_range_raises() -> None:
    with pytest.raises(ValueError):
        parse_numbers("0", 10)


def test_range_partially_out_of_range_raises() -> None:
    with pytest.raises(ValueError):
        parse_numbers("8-12", 10)


def test_invalid_token_raises() -> None:
    with pytest.raises(ValueError):
        parse_numbers("abc", 10)


def test_empty_chunks_ignored() -> None:
    assert parse_numbers(" 1 , , 2 ", 10) == [1, 2]
