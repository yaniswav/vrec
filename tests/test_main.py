"""Tests for vrec.__main__ argument parsing: --all/--only (lot E)."""

from __future__ import annotations

import pytest

from vrec.__main__ import _build_parser


def test_all_and_only_default_to_off() -> None:
    args = _build_parser().parse_args([])
    assert args.all is False
    assert args.only is None


def test_all_flag_parses() -> None:
    args = _build_parser().parse_args(["--all"])
    assert args.all is True
    assert args.only is None


def test_only_flag_parses() -> None:
    args = _build_parser().parse_args(["--only", "3,1,5-8"])
    assert args.only == "3,1,5-8"
    assert args.all is False


def test_all_and_only_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["--all", "--only", "1"])


def test_test_and_only_can_combine() -> None:
    args = _build_parser().parse_args(["--test", "--only", "4"])
    assert args.test is True
    assert args.only == "4"
