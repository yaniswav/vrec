"""Interactive menu: picking which videos to record, marking or resetting status."""

from __future__ import annotations

import re
from pathlib import Path

from vrec import history
from vrec.features import LEGEND, FeatureSet, render_lines, save_features
from vrec.playlist import url_key


def ask(question: str) -> str:
    try:
        return input(question).strip()
    except EOFError:
        return "q"


def parse_numbers(text: str, maximum: int) -> list[int]:
    """Parse "3,1,5-8"-style input into a list of unique numbers within [1, maximum]."""
    numbers: list[int] = []
    for chunk in re.split(r"[,;\s]+", text.replace(" - ", "-").strip()):
        if not chunk:
            continue
        rng = re.fullmatch(r"(\d+)-(\d+)", chunk)
        if rng:
            a, b = int(rng[1]), int(rng[2])
            values: list[int] = list(range(a, b + 1)) if a <= b else list(range(a, b - 1, -1))
        elif chunk.isdigit():
            values = [int(chunk)]
        else:
            raise ValueError(chunk)
        for n in values:
            if not 1 <= n <= maximum:
                raise ValueError(n)
            if n not in numbers:
                numbers.append(n)
    return numbers


def choose_numbers(question: str, maximum: int) -> list[int]:
    while True:
        text = ask(question)
        if not text or text.lower() == "q":
            return []
        try:
            return parse_numbers(text, maximum)
        except ValueError as e:
            print(f"   '{e}' is not valid. Example: 3,1,5-8   (Enter to cancel)")


def show_list(videos: list[tuple[str, str | None]], videos_history: history.Videos) -> None:
    print("\n===== YOUR VIDEOS (videos.txt) =====")
    for i, (url, title) in enumerate(videos, 1):
        known = videos_history.get(url_key(url))
        if not isinstance(known, dict):
            known = None
        tag = history.label(history.status_of(videos_history, url))
        info = []
        date = known.get("date") if known else None
        if isinstance(date, str):
            parts = date[:10].split("-")
            if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
                info.append(f"{parts[2]}/{parts[1]}")
        if known and known.get("detail"):
            info.append(str(known["detail"]))
        suffix = f"   ({', '.join(info)})" if info else ""
        print(f"{i:3d}. [{tag:<8}] {history.display_title(videos_history, url, title)[:60]}{suffix}")


def main_menu(
    videos: list[tuple[str, str | None]],
    videos_history: history.Videos,
    history_path: Path,
    features: FeatureSet,
    features_path: Path,
) -> list[tuple[str, str | None]]:
    while True:
        show_list(videos, videos_history)
        todo = [
            i
            for i, (url, _) in enumerate(videos, 1)
            if history.status_of(videos_history, url) in history.TODO
        ]
        print(f"\n{len(todo)} to record, {len(videos) - len(todo)} already done or to review.\n")
        print("What do you want to do?")
        print(f"  1 - Record everything ({len(todo)} video(s): the new ones and the failures)")
        print("  2 - Choose which ones, in the order you want (even already-done ones)")
        print("  3 - Mark videos as already done (without recording them)")
        print("  4 - Reset videos back to NEW")
        print("  5 - Features on/off")
        print("  Q - Quit")
        choice = ask("> ").lower()

        if choice == "1":
            if not todo:
                print("Everything is already done!")
                continue
            selection = todo
        elif choice == "2":
            selection = choose_numbers("Numbers, in the order you want (e.g. 3,1,5-8): ", len(videos))
            if not selection:
                continue
            already_done = [n for n in selection if n not in todo]
            if already_done:
                print(
                    f"   Already done: {', '.join(map(str, already_done))} -> they will be re-recorded "
                    "(the old file is kept)."
                )
            rest = [n for n in todo if n not in selection]
            if rest and ask(
                f"Record the {len(rest)} other not-yet-done video(s) afterwards? (y/N): "
            ).lower().startswith("y"):
                selection = selection + rest
        elif choice in ("3", "4"):
            question = "Numbers to mark as done: " if choice == "3" else "Numbers to reset to NEW: "
            for n in choose_numbers(question, len(videos)):
                url, title = videos[n - 1]
                display = history.display_title(videos_history, url, title)
                if choice == "3":
                    history.record(
                        history_path, videos_history, url, display, history.STATUS_MARKED, "marked by hand"
                    )
                else:
                    history.record(history_path, videos_history, url, display, history.STATUS_NEW, "reset")
            history.save(history_path, videos_history)
            continue
        elif choice == "5":
            _features_menu(features, features_path)
            continue
        elif choice in ("q", ""):
            return []
        else:
            continue

        print("\nRecording order:")
        for k, n in enumerate(selection, 1):
            url, title = videos[n - 1]
            print(f"  {k:2d}. {history.display_title(videos_history, url, title)[:70]}")
        if ask("\nEnter to start, Q to go back to the menu: ").lower() == "q":
            continue
        return [videos[n - 1] for n in selection]


def _features_menu(features: FeatureSet, features_path: Path) -> None:
    """Show the numbered feature list, toggle by number, save immediately. Loops until Enter."""
    while True:
        print("\n===== FEATURES ON/OFF =====")
        items = features.items()
        for line in render_lines(features, numbered=True):
            print(line)
        print(LEGEND)
        text = ask("\nNumbers to switch (e.g. 2,5), Enter to go back: ")
        if not text:
            return
        try:
            numbers = parse_numbers(text, len(items))
        except ValueError as e:
            print(f"   '{e}' is not valid. Example: 2,5   (Enter to go back)")
            continue
        for n in numbers:
            feature, value = items[n - 1]
            features.set(feature.name, not value)
        save_features(features_path, features)


def choose_test_video(
    videos: list[tuple[str, str | None]], videos_history: history.Videos
) -> list[tuple[str, str | None]]:
    show_list(videos, videos_history)
    todo = [
        i for i, (url, _) in enumerate(videos, 1) if history.status_of(videos_history, url) in history.TODO
    ]
    default = todo[0] if todo else 1
    text = ask(f"\nWhich video to test? (Enter = {default}): ")
    n = int(text) if text.isdigit() and 1 <= int(text) <= len(videos) else default
    return [videos[n - 1]]
