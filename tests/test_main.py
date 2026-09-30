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


def _stub_run(monkeypatch: pytest.MonkeyPatch, double_click: bool) -> list[str]:
    from vrec import __main__ as entry

    prompts: list[str] = []
    monkeypatch.setattr(entry, "_run", lambda args, argv: 0)
    monkeypatch.setattr(entry, "_opened_by_double_click", lambda: double_click)
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "")
    return prompts


def test_main_pauses_after_a_double_click(monkeypatch: pytest.MonkeyPatch) -> None:
    from vrec.__main__ import main

    prompts = _stub_run(monkeypatch, double_click=True)
    assert main([]) == 0
    assert len(prompts) == 1


def test_main_does_not_pause_from_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    from vrec.__main__ import main

    prompts = _stub_run(monkeypatch, double_click=False)
    assert main([]) == 0
    assert prompts == []
    assert main(["--pause-on-exit"]) == 0
    assert len(prompts) == 1
