# ---------- no_quick_edit ----------


def test_no_quick_edit_off_windows(monkeypatch):
    from vrec import console

    monkeypatch.setattr(console.sys, "platform", "linux")
    with console.no_quick_edit() as changed:
        assert changed is False


def test_no_quick_edit_turns_quick_edit_off_and_restores_it(monkeypatch):
    import ctypes
    import sys

    import pytest

    if sys.platform != "win32":
        pytest.skip("Windows console API")
    from vrec import console

    calls: list[int] = []

    class Kernel32:
        def GetStdHandle(self, n: int) -> int:  # noqa: N802 - Windows API name
            return 5

        def GetConsoleMode(self, handle: int, mode_ref) -> int:  # noqa: ANN001, N802
            ctypes.cast(mode_ref, ctypes.POINTER(ctypes.c_ulong)).contents.value = 0x01F7  # QuickEdit on
            return 1

        def SetConsoleMode(self, handle: int, mode: int) -> int:  # noqa: N802
            calls.append(mode)
            return 1

    monkeypatch.setattr(ctypes.windll, "kernel32", Kernel32())
    with console.no_quick_edit() as changed:
        assert changed is True
        assert calls[0] & 0x0040 == 0  # QuickEdit off
        assert calls[0] & 0x0080  # extended flags, required for the change to apply
    assert calls[-1] == 0x01F7  # restored


def test_no_quick_edit_when_input_is_not_a_console(monkeypatch):
    import ctypes
    import sys

    import pytest

    if sys.platform != "win32":
        pytest.skip("Windows console API")
    from vrec import console

    class Kernel32:
        def GetStdHandle(self, n: int) -> int:  # noqa: N802
            return 5

        def GetConsoleMode(self, handle: int, mode_ref) -> int:  # noqa: ANN001, N802
            return 0  # redirected input

        def SetConsoleMode(self, handle: int, mode: int) -> int:  # noqa: N802
            raise AssertionError("must not change anything")

    monkeypatch.setattr(ctypes.windll, "kernel32", Kernel32())
    with console.no_quick_edit() as changed:
        assert changed is False


# ---------- opened_by_double_click ----------


def test_double_click_needs_a_count_of_one() -> None:
    from vrec.console import opened_by_double_click

    assert opened_by_double_click(frozen=True, count=1, interactive=True) is True
    assert opened_by_double_click(frozen=True, count=2, interactive=True) is False
    assert opened_by_double_click(frozen=True, count=None, interactive=True) is False


def test_double_click_ignored_when_not_packaged_or_not_interactive() -> None:
    from vrec.console import opened_by_double_click

    assert opened_by_double_click(frozen=False, count=1, interactive=True) is False
    assert opened_by_double_click(frozen=True, count=1, interactive=False) is False


def test_double_click_is_off_in_a_plain_python_run() -> None:
    from vrec.console import opened_by_double_click

    assert opened_by_double_click() is False


def test_console_process_count_off_windows(monkeypatch) -> None:
    from vrec import console

    monkeypatch.setattr(console.sys, "platform", "linux")
    assert console.console_process_count() is None
