"""menu.main_menu and choose_test_video, driven by scripted input and tmp files."""

from __future__ import annotations

from pathlib import Path

import pytest

from vrec import history, menu
from vrec.features import FeatureSet, load_features

URLS = [f"https://x.test/videos/{n}" for n in (1, 2, 3)]
VIDEOS: list[tuple[str, str | None]] = [(u, f"Video {i}") for i, u in enumerate(URLS, 1)]


def script(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> list[str]:
    """Feed `answers` to input(); a menu asking more than that fails the test."""
    prompts: list[str] = []
    it = iter(answers)

    def fake_input(prompt: str = "") -> str:
        prompts.append(prompt)
        try:
            return next(it)
        except StopIteration:
            pytest.fail(f"unexpected extra prompt: {prompt!r}")

    monkeypatch.setattr("builtins.input", fake_input)
    return prompts


@pytest.fixture
def env(tmp_path: Path) -> dict:
    return {
        "videos_history": {},
        "history_path": tmp_path / "history.json",
        "features": FeatureSet(),
        "features_path": tmp_path / "features.toml",
    }


def run_menu(env: dict, videos: list[tuple[str, str | None]] = VIDEOS) -> list[tuple[str, str | None]]:
    return menu.main_menu(
        videos, env["videos_history"], env["history_path"], env["features"], env["features_path"]
    )


def test_quit_returns_nothing(monkeypatch, env) -> None:
    script(monkeypatch, ["q"])
    assert run_menu(env) == []


def test_empty_choice_and_end_of_input_quit(monkeypatch, env) -> None:
    script(monkeypatch, [""])
    assert run_menu(env) == []
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError()))
    assert run_menu(env) == []


def test_unknown_choice_shows_the_menu_again(monkeypatch, env) -> None:
    prompts = script(monkeypatch, ["zzz", "Q"])
    assert run_menu(env) == []
    assert prompts == ["> ", "> "]


def test_option_1_records_everything_to_do(monkeypatch, env, capsys) -> None:
    history.record(env["history_path"], env["videos_history"], URLS[1], "Video 2", history.STATUS_DONE)
    script(monkeypatch, ["1", ""])
    assert run_menu(env) == [VIDEOS[0], VIDEOS[2]]
    out = capsys.readouterr().out
    assert "Record everything (2 video(s)" in out
    assert "Recording order:" in out


def test_option_1_with_nothing_to_do_goes_back(monkeypatch, env, capsys) -> None:
    for url in URLS:
        history.record(env["history_path"], env["videos_history"], url, "t", history.STATUS_DONE)
    script(monkeypatch, ["1", "q"])
    assert run_menu(env) == []
    assert "Everything is already done!" in capsys.readouterr().out


def test_q_at_the_confirmation_goes_back_to_the_menu(monkeypatch, env) -> None:
    script(monkeypatch, ["1", "q", "1", ""])
    assert run_menu(env) == VIDEOS


def test_option_2_keeps_the_chosen_order_and_can_add_the_rest(monkeypatch, env) -> None:
    prompts = script(monkeypatch, ["2", "3,1", "y", ""])
    assert run_menu(env) == [VIDEOS[2], VIDEOS[0], VIDEOS[1]]
    assert any("Record the 1 other not-yet-done" in p for p in prompts)


def test_option_2_declining_the_rest(monkeypatch, env) -> None:
    script(monkeypatch, ["2", "3,1", "n", ""])
    assert run_menu(env) == [VIDEOS[2], VIDEOS[0]]


def test_option_2_warns_about_already_done_videos(monkeypatch, env, capsys) -> None:
    history.record(env["history_path"], env["videos_history"], URLS[0], "Video 1", history.STATUS_DONE)
    script(monkeypatch, ["2", "1", "", ""])  # the 2nd "" answers the "record the others?" question
    assert run_menu(env) == [VIDEOS[0]]
    assert "Already done: 1 -> they will be re-recorded" in capsys.readouterr().out


def test_option_2_no_question_when_nothing_else_is_left(monkeypatch, env) -> None:
    for url in URLS[1:]:
        history.record(env["history_path"], env["videos_history"], url, "t", history.STATUS_DONE)
    prompts = script(monkeypatch, ["2", "1", ""])
    assert run_menu(env) == [VIDEOS[0]]
    assert not any("other not-yet-done" in p for p in prompts)


def test_option_2_invalid_then_cancel(monkeypatch, env, capsys) -> None:
    script(monkeypatch, ["2", "9", "", "q"])
    assert run_menu(env) == []
    assert "'9' is not valid" in capsys.readouterr().out


def test_option_3_marks_videos_as_done(monkeypatch, env) -> None:
    script(monkeypatch, ["3", "1,3", "q"])
    assert run_menu(env) == []
    saved = history.load(env["history_path"])
    assert history.status_of(saved, URLS[0]) == history.STATUS_MARKED
    assert history.status_of(saved, URLS[2]) == history.STATUS_MARKED
    assert history.status_of(saved, URLS[1]) is None
    assert saved[next(iter(saved))]["detail"] == "marked by hand"
    assert history.status_of(env["videos_history"], URLS[0]) == history.STATUS_MARKED


def test_option_3_then_option_1_skips_the_marked_ones(monkeypatch, env) -> None:
    script(monkeypatch, ["3", "1,3", "1", ""])
    assert run_menu(env) == [VIDEOS[1]]


def test_option_4_resets_to_new(monkeypatch, env) -> None:
    history.record(env["history_path"], env["videos_history"], URLS[0], "Video 1", history.STATUS_DONE)
    script(monkeypatch, ["4", "1", "q"])
    assert run_menu(env) == []
    saved = history.load(env["history_path"])
    assert history.status_of(saved, URLS[0]) == history.STATUS_NEW
    assert saved[next(iter(saved))]["detail"] == "reset"


def test_option_5_toggles_a_feature_and_saves_it(monkeypatch, env) -> None:
    names = [feature.name for feature, _ in env["features"].items()]
    index = names.index("black_check") + 1
    script(monkeypatch, ["5", str(index), "", "q"])
    assert run_menu(env) == []
    assert not env["features"].enabled("black_check")
    saved, _ = load_features(env["features_path"])
    assert not saved.enabled("black_check")


def test_option_5_toggle_twice_restores_and_bad_input_is_reported(monkeypatch, env, capsys) -> None:
    script(monkeypatch, ["5", "abc", "1", "1", "", "q"])
    assert run_menu(env) == []
    assert "'abc' is not valid" in capsys.readouterr().out
    first = env["features"].items()[0][0].name
    assert env["features"].enabled(first)


def test_ask_strips_and_treats_eof_as_quit(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt="": "  hi  ")
    assert menu.ask("? ") == "hi"
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError()))
    assert menu.ask("? ") == "q"


def test_show_list_prints_status_date_and_detail(env, capsys) -> None:
    history.record(
        env["history_path"], env["videos_history"], URLS[0], "Video 1", history.STATUS_FAILED, "black image"
    )
    menu.show_list(VIDEOS, env["videos_history"])
    out = capsys.readouterr().out
    assert "YOUR VIDEOS" in out
    assert "Video 1" in out and "black image" in out
    assert "Video 2" in out


# ---------- choose_test_video ----------


def test_test_video_defaults_to_the_first_one_to_do(monkeypatch, env) -> None:
    history.record(env["history_path"], env["videos_history"], URLS[0], "Video 1", history.STATUS_DONE)
    prompts = script(monkeypatch, [""])
    assert menu.choose_test_video(VIDEOS, env["videos_history"]) == [VIDEOS[1]]
    assert "Enter = 2" in prompts[0]


def test_test_video_defaults_to_1_when_all_are_done(monkeypatch, env) -> None:
    for url in URLS:
        history.record(env["history_path"], env["videos_history"], url, "t", history.STATUS_DONE)
    script(monkeypatch, [""])
    assert menu.choose_test_video(VIDEOS, env["videos_history"]) == [VIDEOS[0]]


def test_test_video_explicit_choice(monkeypatch, env) -> None:
    script(monkeypatch, ["3"])
    assert menu.choose_test_video(VIDEOS, env["videos_history"]) == [VIDEOS[2]]


@pytest.mark.parametrize("answer", ["0", "4", "abc", "-1"])
def test_test_video_invalid_choice_falls_back_to_the_default(monkeypatch, env, answer) -> None:
    script(monkeypatch, [answer])
    assert menu.choose_test_video(VIDEOS, env["videos_history"]) == [VIDEOS[0]]
