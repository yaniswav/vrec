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


def test_selftest_passes_with_the_real_environment(capsys: pytest.CaptureFixture[str]) -> None:
    from vrec.__main__ import main

    assert main(["--selftest"]) == 0
    assert capsys.readouterr().out.strip() == "selftest ok"


def test_selftest_is_hidden_from_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["--help"])
    assert "--selftest" not in capsys.readouterr().out


def test_selftest_reports_the_failing_import(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import builtins

    from vrec.__main__ import main

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "obsws_python":
            raise ImportError("No module named 'obsws_python'")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("importlib.import_module", lambda name: fake_import(name))
    assert main(["--selftest"]) == 1
    assert "obsws_python" in capsys.readouterr().out


def _dispatch_with_missing_import(monkeypatch: pytest.MonkeyPatch, frozen: bool) -> str:
    import builtins
    import sys
    from argparse import Namespace
    from pathlib import Path

    from vrec.__main__ import _dispatch
    from vrec.config import Paths
    from vrec.features import FeatureSet

    real_import = builtins.__import__

    def fake_import(
        name: str, globals_: object = None, locals_: object = None, fromlist: object = (), level: int = 0
    ) -> object:
        if name == "vrec" and fromlist and "app" in fromlist:  # type: ignore[operator]
            raise ImportError("No module named 'playwright'")
        return real_import(name, globals_, locals_, fromlist, level)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    paths = Paths(data_dir=Path("data"), config=Path("data/config.toml"))
    assert _dispatch(Namespace(), paths, FeatureSet()) == 1
    return ""


def test_missing_dependency_message_when_frozen(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _dispatch_with_missing_import(monkeypatch, frozen=True)
    out = capsys.readouterr().out
    assert "No module named 'playwright'" in out
    assert "corrupted" in out
    assert "install.bat" not in out


def test_missing_dependency_message_from_source(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _dispatch_with_missing_import(monkeypatch, frozen=False)
    out = capsys.readouterr().out
    assert "No module named 'playwright'" in out
    assert "install.bat" in out
