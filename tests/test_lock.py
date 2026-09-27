"""Tests for vrec.lock.InstanceLock."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from vrec.errors import VrecError
from vrec.lock import InstanceLock


def test_second_lock_same_process_raises(tmp_path: Path) -> None:
    path = tmp_path / "vrec.lock"
    with InstanceLock(path), pytest.raises(VrecError), InstanceLock(path):
        pass


def test_lock_released_after_exit(tmp_path: Path) -> None:
    path = tmp_path / "vrec.lock"
    with InstanceLock(path):
        pass
    # Should succeed: the first lock was released on exit.
    with InstanceLock(path):
        pass


def test_lock_writes_pid(tmp_path: Path) -> None:
    # Read through the lock's own file descriptor: a second handle on the locked
    # region (even path.read_text, which opens a new one) is denied on Windows.
    path = tmp_path / "vrec.lock"
    lock = InstanceLock(path)
    lock.__enter__()
    try:
        os.lseek(lock._fd, 0, os.SEEK_SET)
        content = os.read(lock._fd, 64)
        assert content == str(os.getpid()).encode("utf-8")
    finally:
        lock.__exit__(None, None, None)


def test_lock_reentrant_across_sequential_runs(tmp_path: Path) -> None:
    path = tmp_path / "vrec.lock"
    for _ in range(3):
        with InstanceLock(path):
            assert path.exists()
    # File is removed as a nicety once nothing holds the lock.
    assert not path.exists()


def test_lock_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "vrec.lock"
    with InstanceLock(path):
        assert path.exists()


def test_second_lock_raises_even_while_still_inside_first(tmp_path: Path) -> None:
    path = tmp_path / "vrec.lock"
    lock1 = InstanceLock(path)
    lock1.__enter__()
    try:
        lock2 = InstanceLock(path)
        with pytest.raises(VrecError, match="already running"):
            lock2.__enter__()
    finally:
        lock1.__exit__(None, None, None)
