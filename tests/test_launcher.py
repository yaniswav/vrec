"""Tests for vrec.launcher: finding/starting OBS and Chrome (no real registry, process, or network)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vrec import launcher, obs_control
from vrec.config import Paths, Settings
from vrec.errors import VrecError
from vrec.features import FeatureSet

# ---------- find_obs ----------


def test_find_obs_uses_the_setting_if_it_exists(tmp_path: Path) -> None:
    exe = tmp_path / "obs64.exe"
    exe.write_bytes(b"")
    settings = Settings(obs_path=str(exe))
    found = launcher.find_obs(settings, read_registry=lambda key: pytest.fail("should not read registry"))
    assert found == exe


def test_find_obs_ignores_a_setting_that_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ProgramFiles", str(tmp_path))  # empty: the default install path won't exist
    settings = Settings(obs_path=str(tmp_path / "missing.exe"))
    assert launcher.find_obs(settings, read_registry=lambda key: None) is None


def test_find_obs_falls_back_to_the_registry(tmp_path: Path) -> None:
    install_dir = tmp_path / "OBS Studio"
    exe = install_dir / "bin" / "64bit" / "obs64.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")

    def read_registry(key_path: str) -> str | None:
        return str(install_dir) if key_path == launcher._OBS_REGISTRY_KEYS[0] else None

    assert launcher.find_obs(Settings(), read_registry=read_registry) == exe


def test_find_obs_tries_the_wow6432node_view_too(tmp_path: Path) -> None:
    install_dir = tmp_path / "OBS Studio"
    exe = install_dir / "bin" / "64bit" / "obs64.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")

    def read_registry(key_path: str) -> str | None:
        return str(install_dir) if key_path == launcher._OBS_REGISTRY_KEYS[1] else None

    assert launcher.find_obs(Settings(), read_registry=read_registry) == exe


def test_find_obs_falls_back_to_the_default_install_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    exe = tmp_path / "obs-studio" / "bin" / "64bit" / "obs64.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert launcher.find_obs(Settings(), read_registry=lambda key: None) == exe


def test_find_obs_returns_none_when_nothing_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ProgramFiles", str(tmp_path))  # nothing installed there
    assert launcher.find_obs(Settings(), read_registry=lambda key: None) is None


# ---------- find_chrome ----------


def test_find_chrome_uses_the_setting_if_it_exists(tmp_path: Path) -> None:
    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"")
    settings = Settings(chrome_path=str(exe))
    found = launcher.find_chrome(settings, read_registry=lambda key: pytest.fail("should not read registry"))
    assert found == exe


def test_find_chrome_falls_back_to_a_standard_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    program_files = tmp_path / "pf"
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "pfx86"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    exe = program_files / "Google" / "Chrome" / "Application" / "chrome.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")

    found = launcher.find_chrome(Settings(), read_registry=lambda key: pytest.fail("should not be reached"))
    assert found == exe


def test_find_chrome_falls_back_to_the_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "pfx86"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    exe = tmp_path / "elsewhere" / "chrome.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")

    found = launcher.find_chrome(Settings(), read_registry=lambda key: str(exe))
    assert found == exe


def test_find_chrome_returns_none_when_nothing_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "pfx86"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert launcher.find_chrome(Settings(), read_registry=lambda key: None) is None


# ---------- chrome_profile_dir (profile resolution order) ----------


def test_chrome_profile_dir_uses_the_setting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VREC_CHROME_PROFILE", str(tmp_path / "env"))
    settings = Settings(chrome_profile=str(tmp_path / "setting"))
    assert launcher.chrome_profile_dir(settings) == tmp_path / "setting"


def test_chrome_profile_dir_falls_back_to_the_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VREC_CHROME_PROFILE", str(tmp_path / "env"))
    assert launcher.chrome_profile_dir(Settings()) == tmp_path / "env"


def test_chrome_profile_dir_falls_back_to_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VREC_CHROME_PROFILE", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert launcher.chrome_profile_dir(Settings()) == tmp_path / "vrec" / "chrome-profile"


# ---------- obs_running / chrome_port_open ----------


def test_obs_running_true_when_tasklist_finds_it() -> None:
    def run(args: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(args), 0, "obs64.exe  1234 Console  1  50,000 K\r\n", "")

    assert launcher.obs_running(run=run) is True


def test_obs_running_false_when_tasklist_finds_nothing() -> None:
    def run(args: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            list(args), 1, "INFO: No tasks are running which match the specified criteria.\r\n", ""
        )

    assert launcher.obs_running(run=run) is False


class _FakeResponse:
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_chrome_port_open_true_when_urlopen_succeeds() -> None:
    assert launcher.chrome_port_open(9222, urlopen=lambda url, timeout=2: _FakeResponse()) is True


def test_chrome_port_open_false_when_urlopen_fails() -> None:
    def boom(url: str, timeout: float = 2) -> None:
        raise OSError("connection refused")

    assert launcher.chrome_port_open(9222, urlopen=boom) is False


# ---------- start_obs / start_chrome ----------


def test_start_obs_uses_the_right_argv_cwd_and_flags(tmp_path: Path) -> None:
    exe = tmp_path / "bin" / "64bit" / "obs64.exe"
    exe.parent.mkdir(parents=True)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(args: list[str], **kwargs: object) -> object:
        calls.append((args, kwargs))
        return object()

    launcher.start_obs(exe, popen=fake_popen)

    args, kwargs = calls[0]
    assert args == [str(exe), "--disable-shutdown-check"]
    assert kwargs["cwd"] == exe.parent
    assert kwargs["creationflags"] == subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL


def test_start_chrome_uses_the_same_flags_as_the_bat_file(tmp_path: Path) -> None:
    exe = tmp_path / "chrome.exe"
    profile = tmp_path / "chrome-profile"
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(args: list[str], **kwargs: object) -> object:
        calls.append((args, kwargs))
        return object()

    launcher.start_chrome(exe, 9222, profile, popen=fake_popen)

    args, kwargs = calls[0]
    assert args == [
        str(exe),
        "--remote-debugging-port=9222",
        f"--user-data-dir={profile}",
        "--autoplay-policy=no-user-gesture-required",
        "--disable-features=CalculateNativeWinOcclusion",
        "--disable-backgrounding-occluded-windows",
    ]
    assert kwargs["creationflags"] == subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    assert kwargs["stdin"] == subprocess.DEVNULL


# ---------- ensure_obs ----------


def _paths(tmp_path: Path) -> Paths:
    return Paths(data_dir=tmp_path, config=tmp_path / "config.toml")


def test_ensure_obs_returns_immediately_when_already_reachable(tmp_path: Path) -> None:
    client = object()
    readied: list[object] = []

    def connect(settings: Settings, paths: Paths) -> tuple[object, str]:
        return client, "pw"

    result = launcher.ensure_obs(
        Settings(),
        _paths(tmp_path),
        FeatureSet(),
        connect=connect,
        find=lambda s: pytest.fail("should not look for obs"),
        running=lambda: pytest.fail("should not check tasklist"),
        start=lambda exe: pytest.fail("should not start obs"),
        ready=lambda c, timeout: readied.append(c),
    )
    assert result == (client, "pw")
    assert readied == [client]  # an OBS opened by hand may still be loading too


def test_ensure_obs_starts_and_connects_after_retries(tmp_path: Path) -> None:
    client = object()
    attempts = {"n": 0}

    def connect(settings: Settings, paths: Paths) -> tuple[object, str]:
        attempts["n"] += 1
        if attempts["n"] <= 3:
            raise obs_control.ObsUnreachable("nope")
        return client, "pw"

    started: list[Path] = []
    sleeps: list[float] = []
    readied: list[tuple[object, float]] = []
    clock_values = iter([0.0, 2.0, 4.0, 6.0, 8.0, 10.0])
    exe = tmp_path / "obs64.exe"

    result = launcher.ensure_obs(
        Settings(obs_start_timeout_s=60),
        _paths(tmp_path),
        FeatureSet(),
        connect=connect,
        find=lambda s: exe,
        running=lambda: False,
        start=lambda e: started.append(e),
        sleep=sleeps.append,
        clock=lambda: next(clock_values),
        ready=lambda c, timeout: readied.append((c, timeout)),
    )

    assert result == (client, "pw")
    assert readied == [(client, 54.0)]  # waits for OBS to finish loading, within what's left
    assert started == [exe]
    assert attempts["n"] == 4  # the initial attempt, then 3 retries before success
    assert sleeps == [2.0, 2.0, 2.0]


def test_ensure_obs_running_but_not_answering_errors_without_starting(tmp_path: Path) -> None:
    def connect(settings: Settings, paths: Paths) -> tuple[object, str]:
        raise obs_control.ObsUnreachable("nope")

    with pytest.raises(VrecError, match="doesn't answer"):
        launcher.ensure_obs(
            Settings(),
            _paths(tmp_path),
            FeatureSet(),
            connect=connect,
            running=lambda: True,
            find=lambda s: pytest.fail("should not look for obs"),
            start=lambda e: pytest.fail("should not start obs"),
        )


def test_ensure_obs_not_found_errors(tmp_path: Path) -> None:
    def connect(settings: Settings, paths: Paths) -> tuple[object, str]:
        raise obs_control.ObsUnreachable("nope")

    with pytest.raises(VrecError, match="wasn't found"):
        launcher.ensure_obs(
            Settings(),
            _paths(tmp_path),
            FeatureSet(),
            connect=connect,
            running=lambda: False,
            find=lambda s: None,
            start=lambda e: pytest.fail("should not start obs"),
        )


def test_ensure_obs_timeout_errors(tmp_path: Path) -> None:
    def connect(settings: Settings, paths: Paths) -> tuple[object, str]:
        raise obs_control.ObsUnreachable("nope")

    clock_values = iter([0.0, 100.0])

    with pytest.raises(VrecError, match="doesn't answer"):
        launcher.ensure_obs(
            Settings(obs_start_timeout_s=10),
            _paths(tmp_path),
            FeatureSet(),
            connect=connect,
            running=lambda: False,
            find=lambda s: tmp_path / "obs64.exe",
            start=lambda e: None,
            sleep=lambda s: None,
            clock=lambda: next(clock_values),
        )


def test_ensure_obs_feature_off_reraises_the_original_error(tmp_path: Path) -> None:
    def connect(settings: Settings, paths: Paths) -> tuple[object, str]:
        raise obs_control.ObsUnreachable("Can't reach OBS: open OBS and enable the WebSocket server.")

    with pytest.raises(obs_control.ObsUnreachable, match="Can't reach OBS"):
        launcher.ensure_obs(
            Settings(),
            _paths(tmp_path),
            FeatureSet({"auto_start_obs": False}),
            connect=connect,
            running=lambda: pytest.fail("should not check tasklist"),
            find=lambda s: pytest.fail("should not look for obs"),
            start=lambda e: pytest.fail("should not start obs"),
        )


def test_ensure_obs_wrong_password_is_not_treated_as_unreachable(tmp_path: Path) -> None:
    def connect(settings: Settings, paths: Paths) -> tuple[object, str]:
        raise VrecError("OBS refused the connection: the password is probably wrong.")

    with pytest.raises(VrecError, match="password"):
        launcher.ensure_obs(
            Settings(),
            _paths(tmp_path),
            FeatureSet(),
            connect=connect,
            running=lambda: pytest.fail("should not check tasklist"),
            find=lambda s: pytest.fail("should not look for obs"),
            start=lambda e: pytest.fail("should not start obs"),
        )


# ---------- ensure_chrome ----------


def test_ensure_chrome_does_nothing_when_the_port_is_already_open() -> None:
    launcher.ensure_chrome(
        Settings(),
        FeatureSet(),
        port_open=lambda port: True,
        find=lambda s: pytest.fail("should not look for chrome"),
        start=lambda e, port, profile: pytest.fail("should not start chrome"),
    )


def test_ensure_chrome_starts_and_waits_for_the_port(tmp_path: Path) -> None:
    exe = tmp_path / "chrome.exe"
    attempts = {"n": 0}

    def port_open(port: int) -> bool:
        attempts["n"] += 1
        return attempts["n"] > 3

    started: list[tuple[Path, int, Path]] = []
    sleeps: list[float] = []
    clock_values = iter([0.0, 2.0, 4.0, 6.0, 8.0])

    launcher.ensure_chrome(
        Settings(chrome_start_timeout_s=30),
        FeatureSet(),
        port_open=port_open,
        find=lambda s: exe,
        start=lambda e, port, profile: started.append((e, port, profile)),
        sleep=sleeps.append,
        clock=lambda: next(clock_values),
    )

    assert started == [(exe, 9222, launcher.chrome_profile_dir(Settings()))]
    assert sleeps == [2.0, 2.0]


def test_ensure_chrome_not_found_errors() -> None:
    with pytest.raises(VrecError, match="wasn't found"):
        launcher.ensure_chrome(
            Settings(),
            FeatureSet(),
            port_open=lambda port: False,
            find=lambda s: None,
            start=lambda e, port, profile: pytest.fail("should not start chrome"),
        )


def test_ensure_chrome_timeout_errors(tmp_path: Path) -> None:
    exe = tmp_path / "chrome.exe"
    clock_values = iter([0.0, 100.0])

    with pytest.raises(VrecError, match="doesn't answer"):
        launcher.ensure_chrome(
            Settings(chrome_start_timeout_s=10),
            FeatureSet(),
            port_open=lambda port: False,
            find=lambda s: exe,
            start=lambda e, port, profile: None,
            sleep=lambda s: None,
            clock=lambda: next(clock_values),
        )


def test_ensure_chrome_feature_off_does_nothing() -> None:
    launcher.ensure_chrome(
        Settings(),
        FeatureSet({"auto_start_chrome": False}),
        port_open=lambda port: False,
        find=lambda s: pytest.fail("should not look for chrome"),
        start=lambda e, port, profile: pytest.fail("should not start chrome"),
    )
