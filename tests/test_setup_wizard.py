"""vrec --setup wizard and --features-menu: scripted answers, fake seams, tmp data dirs only."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path

import pytest

from vrec import __main__ as entry
from vrec import setup_wizard as wizard
from vrec.config import Paths, Settings
from vrec.doctor import Check
from vrec.features import FeatureSet, feature_names, load_features


class Script:
    """Scripted answers plus a transcript of everything said and asked."""

    def __init__(self, answers: list[str | type[BaseException]]) -> None:
        self.answers = list(answers)
        self.out: list[str] = []
        self.prompts: list[str] = []

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            return ""  # Enter: the default
        answer = self.answers.pop(0)
        if isinstance(answer, type):
            raise answer
        return answer

    def text(self) -> str:
        return "\n".join(self.out)


def make_io(
    script: Script,
    *,
    checks: list[Check] | None = None,
    interactive: bool = True,
    helper: list[bool | None] | None = None,
    devices: list[str] | None = None,
    admin: bool = True,
    elevate: Callable[[str, str], bool] | None = None,
    install_here: Callable[[], bool] | None = None,
) -> wizard.Seams:
    states = list(helper if helper is not None else [None])
    results = checks if checks is not None else [Check("ok", "Thing")]
    return wizard.Seams(
        ask=script.ask,
        say=script.out.append,
        secret=lambda _prompt: "s3cret",
        interactive=interactive,
        is_admin=lambda: admin,
        elevate=elevate or (lambda program, params: True),
        install_here=install_here or (lambda: True),
        list_devices=lambda: devices if devices is not None else [],
        helper_state=lambda: states.pop(0) if len(states) > 1 else states[0],
        run_checks=lambda settings, paths, features: results,
    )


@pytest.fixture(autouse=True)
def examples(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake download folder holding the example files, and no OBS password from the environment."""
    folder = tmp_path / "download"
    folder.mkdir()
    (folder / "config.example.toml").write_text("[obs]\nport = 4455\n", encoding="utf-8")
    (folder / "videos.example.txt").write_text("https://example.com/a\n", encoding="utf-8")
    monkeypatch.setattr(wizard, "_example_dirs", lambda: [folder])
    monkeypatch.delenv("VREC_OBS_PASSWORD", raising=False)
    return folder


def run(tmp_path: Path, io: wizard.Seams) -> int:
    return wizard.run_setup(tmp_path / "data", None, io)


def test_fresh_setup_creates_files_and_walks_every_step(tmp_path: Path) -> None:
    script = Script([])  # Enter everywhere: save the password, keep the defaults
    checks = [Check("ok", "OBS connection"), Check("fail", "VB-CABLE", hint="Install it.")]
    assert run(tmp_path, make_io(script, checks=checks)) == 0
    data = tmp_path / "data"
    assert (data / "config.toml").read_text(encoding="utf-8").startswith("[obs]")
    assert (data / "videos.txt").exists()
    assert (data / "obs_password.txt").read_text(encoding="utf-8") == "s3cret"
    text = script.text()
    assert "Created:" in text
    assert "still holds the example links" in text
    assert "[FAIL] VB-CABLE" in text and "-> Install it." in text
    assert "Still to do:" in text and "launch_chrome.bat" in text and "start.bat" in text
    assert not (data / "features.toml").exists()  # nothing changed, nothing written


def test_everything_present_changes_nothing(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "config.toml").write_text("# mine\n", encoding="utf-8")
    (data / "videos.txt").write_text("https://example.com/mine\n", encoding="utf-8")
    (data / "obs_password.txt").write_text("keep", encoding="utf-8")
    script = Script([])
    assert run(tmp_path, make_io(script)) == 0
    assert (data / "config.toml").read_text(encoding="utf-8") == "# mine\n"
    assert (data / "videos.txt").read_text(encoding="utf-8") == "https://example.com/mine\n"
    assert (data / "obs_password.txt").read_text(encoding="utf-8") == "keep"
    assert script.text().count("Already there:") == 3
    assert "Nothing left to do." in script.text()
    assert not (data / "features.toml").exists()


def test_empty_password_is_skipped(tmp_path: Path) -> None:
    io = make_io(Script([]))
    io.secret = lambda _prompt: "  "
    run(tmp_path, io)
    assert not (tmp_path / "data" / "obs_password.txt").exists()


def test_declining_the_password_prompt(tmp_path: Path) -> None:
    run(tmp_path, make_io(Script(["n"])))
    assert not (tmp_path / "data" / "obs_password.txt").exists()


def test_missing_example_is_reported(tmp_path: Path, examples: Path) -> None:
    (examples / "videos.example.txt").unlink()
    script = Script([])
    run(tmp_path, make_io(script))
    assert "videos.example.txt not found" in script.text()
    assert not (tmp_path / "data" / "videos.txt").exists()


def test_feature_walkthrough_keep_on_off_and_single_save(tmp_path: Path) -> None:
    names = [n for n in feature_names() if n != "manage_virtual_display"]
    answers: list[str | type[BaseException]] = ["n", "y"] + [""] * len(names)  # no password, walk
    answers[2 + names.index("quality_filter")] = "off"
    answers[2 + names.index("hotkeys")] = "n"
    answers[2 + names.index("quality_retry")] = "keep"
    script = Script(answers)
    assert run(tmp_path, make_io(script)) == 0
    features, _ = load_features(tmp_path / "data" / "features.toml")
    assert features.enabled("quality_filter") is False
    assert features.enabled("hotkeys") is False
    assert features.enabled("quality_retry") is True
    assert "Saved features.toml" in script.text()
    assert "manage_virtual_display:" not in script.text().split("Step 4")[1]


def test_feature_walkthrough_can_turn_a_feature_on(tmp_path: Path) -> None:
    names = [n for n in feature_names() if n != "manage_virtual_display"]
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "features.toml").write_text("[features]\nhotkeys = false\n", encoding="utf-8")
    answers: list[str | type[BaseException]] = ["n", "y"] + [""] * len(names)
    answers[2 + names.index("hotkeys")] = "on"
    run(tmp_path, make_io(Script(answers)))
    features, _ = load_features(tmp_path / "data" / "features.toml")
    assert features.enabled("hotkeys") is True


def test_invalid_feature_answer_asks_again(tmp_path: Path) -> None:
    script = Script(["n", "y", "maybe", "on"])
    run(tmp_path, make_io(script))
    assert "Type Enter (keep)" in script.text()


def test_invalid_yes_no_answer_asks_again(tmp_path: Path) -> None:
    script = Script(["perhaps", "n"])
    run(tmp_path, make_io(script))
    assert "Please answer y or n" in script.text()


def test_ctrl_c_midway_saves_no_feature_changes(tmp_path: Path) -> None:
    script = Script(["n", "y", "off", "off", KeyboardInterrupt])
    assert run(tmp_path, make_io(script)) == 130
    assert not (tmp_path / "data" / "features.toml").exists()
    assert "No feature changes were saved" in script.text()


def test_end_of_input_stops_cleanly(tmp_path: Path) -> None:
    assert run(tmp_path, make_io(Script(["n", EOFError]))) == 130


def test_non_interactive_prints_checks_only(tmp_path: Path) -> None:
    script = Script([])
    io = make_io(script, interactive=False, checks=[Check("fail", "OBS connection", hint="Open OBS.")])
    assert run(tmp_path, io) == 1
    assert not (tmp_path / "data").exists()
    assert script.prompts == []
    assert "checks only" in script.text()


def test_non_interactive_all_ok_returns_zero(tmp_path: Path) -> None:
    assert run(tmp_path, make_io(Script([]), interactive=False)) == 0


# ---------- virtual display step ----------


def display_answers(*step3: str) -> list[str | type[BaseException]]:
    return ["n", *step3, "n"]  # no password, the display answers, no feature walkthrough


def test_display_helper_already_installed_then_turned_on(tmp_path: Path) -> None:
    script = Script(display_answers(""))
    assert run(tmp_path, make_io(script, helper=[True])) == 0
    features, _ = load_features(tmp_path / "data" / "features.toml")
    assert features.enabled("manage_virtual_display") is True
    assert "already installed" in script.text()


def test_display_no_matching_adapter_skips(tmp_path: Path) -> None:
    script = Script(["n", "n"])
    run(tmp_path, make_io(script, helper=[None], devices=["Intel UHD"]))
    assert "No display adapter matches" in script.text()


def test_display_install_declined(tmp_path: Path) -> None:
    installed: list[bool] = []

    def install() -> bool:
        installed.append(True)
        return True

    script = Script(["n", "", "n"])  # Enter = default no
    run(tmp_path, make_io(script, helper=[None], devices=["Virtual Display"], install_here=install))
    assert installed == []
    assert not (tmp_path / "data" / "features.toml").exists()


def test_display_install_elevated_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(wizard.sys, "executable", "C:/vrec/vrec.exe")
    monkeypatch.setattr(wizard.sys, "frozen", True, raising=False)

    def elevate(program: str, params: str) -> bool:
        calls.append((program, params))
        return True

    script = Script(display_answers("y", "y"))
    io = make_io(script, helper=[None, True], devices=["Virtual Display"], admin=False, elevate=elevate)
    assert run(tmp_path, io) == 0
    assert calls == [("C:/vrec/vrec.exe", "--install-display-helper *Virtual* --pause-on-exit")]
    features, _ = load_features(tmp_path / "data" / "features.toml")
    assert features.enabled("manage_virtual_display") is True
    assert "administrator" in script.text()


def test_elevation_command_for_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wizard.sys, "frozen", False, raising=False)
    monkeypatch.setattr(wizard.sys, "executable", "py.exe")
    expected = ("py.exe", "-m vrec --install-display-helper *Virtual* --pause-on-exit")
    assert wizard._elevated_command() == expected


def test_display_elevation_refused(tmp_path: Path) -> None:
    script = Script(display_answers("y"))
    io = make_io(script, helper=[None], devices=["Virtual Display"], admin=False, elevate=lambda p, a: False)
    run(tmp_path, io)
    assert "didn't run" in script.text()
    assert "Virtual display helper:" in script.text()


def test_display_install_when_already_admin(tmp_path: Path) -> None:
    installed: list[bool] = []

    def install() -> bool:
        installed.append(True)
        return True

    script = Script(display_answers("y", "n"))
    io = make_io(script, helper=[None, True], devices=["Virtual Display"], admin=True, install_here=install)
    run(tmp_path, io)
    assert installed == [True]
    assert not (tmp_path / "data" / "features.toml").exists()  # display feature answered n: unchanged


def test_install_in_process_uses_display_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake(args: object) -> int:
        seen.append(args.install_display_helper)
        return 0

    monkeypatch.setattr(entry, "_run_display_helper", fake)
    assert wizard._install_in_process() is True
    assert seen == ["*Virtual*"]


# ---------- real checks, with fakes ----------


def test_run_checks_reports_chrome_and_survives_a_crashing_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(ctx: object) -> Check:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(wizard.doctor, "check_obs_connection", boom)
    monkeypatch.setattr(wizard.doctor, "check_vb_cable", lambda ctx: Check("ok", "VB-CABLE"))
    monkeypatch.setattr(wizard.doctor, "check_virtual_screen", lambda ctx: Check("ok", "Virtual screen"))
    monkeypatch.setattr(wizard.launcher, "find_chrome", lambda settings: None)
    paths = Paths(data_dir=tmp_path, config=tmp_path / "config.toml")
    results = wizard.run_checks(Settings(), paths, FeatureSet())
    assert {c.title: c for c in results}["Chrome"].status == "fail"
    assert any(c.status == "fail" and "kaboom" in c.detail for c in results)
    monkeypatch.setattr(wizard.launcher, "find_chrome", lambda settings: Path("chrome.exe"))
    assert {c.title: c for c in wizard.run_checks(Settings(), paths, FeatureSet())}["Chrome"].status == "ok"


def test_invalid_features_file_skips_steps_3_and_4(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "features.toml").write_text("not [valid", encoding="utf-8")
    script = Script([])
    assert run(tmp_path, make_io(script)) == 0
    assert "Steps 3 and 4 skipped" in script.text()
    assert (data / "features.toml").read_text(encoding="utf-8") == "not [valid"


# ---------- CLI ----------


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entry, "_opened_by_double_click", lambda: False)
    monkeypatch.setattr("vrec.console.no_quick_edit", contextlib.nullcontext)
    monkeypatch.delenv("VREC_DATA_DIR", raising=False)


class Tty:
    def isatty(self) -> bool:
        return True


def test_cli_setup_dispatches_to_the_wizard(
    cli: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[Path, Path | None]] = []

    def fake(data_dir: Path, config: Path | None, io: object = None) -> int:
        seen.append((data_dir, config))
        return 0

    monkeypatch.setattr(wizard, "run_setup", fake)
    assert entry.main(["--setup", "--data-dir", str(tmp_path)]) == 0
    assert seen == [(tmp_path, None)]


def test_cli_features_menu_without_keyboard_lists(
    cli: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert entry.main(["--features-menu", "--data-dir", str(tmp_path)]) == 0  # pytest stdin isn't a tty
    out = capsys.readouterr().out
    assert all(name in out for name in feature_names())


def test_cli_features_menu_on_a_keyboard_toggles_and_saves(
    cli: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(entry.sys, "stdin", Tty())
    answers = iter(["2", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    assert entry.main(["--features-menu", "--data-dir", str(tmp_path)]) == 0
    features, _ = load_features(tmp_path / "features.toml")
    assert features.enabled(feature_names()[1]) is False


def test_cli_features_menu_invalid_file(
    cli: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(entry.sys, "stdin", Tty())
    (tmp_path / "features.toml").write_text("not [valid", encoding="utf-8")
    assert entry.main(["--features-menu", "--data-dir", str(tmp_path)]) == 1
    assert "Invalid features file" in capsys.readouterr().out
