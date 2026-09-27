"""Tests for vrec.logs: the \\r-collapsing tee, header/rotation, input logging, and the
`vrec.__main__` unhandled-exception path."""

from __future__ import annotations

import builtins
import io
from pathlib import Path

import pytest

from vrec import __main__ as vrec_main
from vrec import logs as logs_module
from vrec.features import FeatureSet
from vrec.logs import MAX_LOG_FILES, _Tee, capture

# ---------------------------------------------------------------------------
# _Tee: collapsing in-place progress lines
# ---------------------------------------------------------------------------


def test_tee_collapses_carriage_returns_to_the_final_state() -> None:
    lines: list[str] = []
    tee = _Tee(io.StringIO(), lines.append)

    tee.write("\rProgress 10%")
    tee.write("\rProgress 50%")
    tee.write("\rProgress 100%")
    tee.write("\n")

    assert lines == ["Progress 100%\n"]


def test_tee_writes_the_console_stream_unchanged() -> None:
    console = io.StringIO()
    tee = _Tee(console, lambda line: None)

    tee.write("\rA\rB\n")

    assert console.getvalue() == "\rA\rB\n"


def test_tee_only_flushes_a_line_once_it_ends_with_newline() -> None:
    lines: list[str] = []
    tee = _Tee(io.StringIO(), lines.append)

    tee.write("partial, no newline yet")
    assert lines == []

    tee.write(" now it's done\n")
    assert lines == ["partial, no newline yet now it's done\n"]


def test_tee_handles_several_complete_lines_in_one_write() -> None:
    lines: list[str] = []
    tee = _Tee(io.StringIO(), lines.append)

    tee.write("one\ntwo\nthree")

    assert lines == ["one\n", "two\n"]


def test_tee_note_appends_to_log_only_not_the_console() -> None:
    console = io.StringIO()
    lines: list[str] = []
    tee = _Tee(console, lines.append)

    tee.write("Prompt: ")
    tee.note("secret-answer")

    assert console.getvalue() == "Prompt: "  # the answer never touches the console stream
    assert lines == ["Prompt: secret-answer\n"]


# ---------------------------------------------------------------------------
# capture(): file creation, header, and being a no-op when the feature is off
# ---------------------------------------------------------------------------


def test_capture_creates_a_log_file_with_a_header(tmp_path: Path) -> None:
    with capture(tmp_path, ["--test"], FeatureSet({"manage_virtual_display": True})) as log:
        assert log.path is not None
        assert log.path.parent == tmp_path / "logs"
        print("hello from the run")

    text = log.path.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0].startswith("vrec ")
    assert lines[1].startswith("Python ")
    assert "Args: --test" in lines[3]
    assert "Features off: (none)" in lines[4]
    assert "hello from the run" in text


def test_header_lists_disabled_features() -> None:
    class FakeFeatures:
        def disabled(self) -> list[str]:
            return ["run_logs", "beta_thing"]

    header = logs_module._header(["--test"], FakeFeatures())

    assert header[3] == "Args: --test"
    assert header[4] == "Features off: run_logs, beta_thing"


def test_capture_is_a_noop_when_run_logs_is_disabled(tmp_path: Path) -> None:
    features = FeatureSet()
    features.set("run_logs", False)

    with capture(tmp_path, [], features) as log:
        assert log.path is None
        print("not logged anywhere but the console")

    assert not (tmp_path / "logs").exists()


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


def test_rotation_keeps_only_the_20_most_recent_logs(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    old_names = [f"vrec-20240101-{i:06d}.log" for i in range(25)]
    for name in old_names:
        (log_dir / name).write_text("old", encoding="utf-8")

    with capture(tmp_path, [], FeatureSet()):
        pass

    remaining = sorted(p.name for p in log_dir.glob("vrec-*.log"))
    assert len(remaining) == MAX_LOG_FILES
    # The 6 oldest dummy files must have been pruned to make room for the new one.
    for name in old_names[:6]:
        assert name not in remaining
    for name in old_names[6:]:
        assert name in remaining


# ---------------------------------------------------------------------------
# input() logging, with password masking
# ---------------------------------------------------------------------------


def test_input_prompts_and_answers_are_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(["Bob"])
    monkeypatch.setattr(builtins, "input", lambda: next(answers))

    with capture(tmp_path, [], FeatureSet()) as log:
        answer = input("Name: ")

    assert answer == "Bob"
    text = log.path.read_text(encoding="utf-8")
    assert "Name: Bob" in text


def test_password_prompt_answer_is_masked_in_the_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(["s3cret-value"])
    monkeypatch.setattr(builtins, "input", lambda: next(answers))

    with capture(tmp_path, [], FeatureSet()) as log:
        answer = input("Paste the WebSocket server password here: ")

    assert answer == "s3cret-value"
    text = log.path.read_text(encoding="utf-8")
    assert "s3cret-value" not in text
    assert "Paste the WebSocket server password here: ***" in text


# ---------------------------------------------------------------------------
# vrec.__main__: unhandled exceptions
# ---------------------------------------------------------------------------


def test_unhandled_exception_is_logged_with_a_short_console_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from vrec import app as app_module

    def boom(*args: object, **kwargs: object) -> int:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(app_module, "run", boom)

    code = vrec_main.main(["--data-dir", str(tmp_path)])

    assert code == 1
    out = capsys.readouterr().out
    assert "Unexpected error: kaboom. Details in" in out
    assert "Traceback" not in out  # the full traceback goes to the log file, not the console

    log_files = list((tmp_path / "logs").glob("vrec-*.log"))
    assert len(log_files) == 1
    text = log_files[0].read_text(encoding="utf-8")
    assert "Traceback (most recent call last)" in text
    assert "RuntimeError: kaboom" in text


def test_unhandled_exception_prints_traceback_when_logging_is_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from vrec import app as app_module
    from vrec.features import save_features

    features = FeatureSet()
    features.set("run_logs", False)
    save_features(tmp_path / "features.toml", features)

    def boom(*args: object, **kwargs: object) -> int:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(app_module, "run", boom)

    code = vrec_main.main(["--data-dir", str(tmp_path)])

    assert code == 1
    err = capsys.readouterr().err
    assert "RuntimeError: kaboom" in err
    assert not (tmp_path / "logs").exists()
