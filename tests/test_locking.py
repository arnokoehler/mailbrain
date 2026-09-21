import multiprocessing
import os

import pytest

from mailbrain.locking import LockUnavailableError, process_lock
from mailbrain.paths import lock_path


def hold_lock(home, ready, release):
    os.environ["MAILBRAIN_HOME"] = home
    with process_lock():
        ready.set()
        release.wait(10)


def test_process_contention_fails_immediately(monkeypatch, tmp_path):
    monkeypatch.setenv("MAILBRAIN_HOME", str(tmp_path))
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(target=hold_lock, args=(str(tmp_path), ready, release))
    process.start()
    assert ready.wait(10)
    try:
        with pytest.raises(LockUnavailableError, match="state is locked"), process_lock():
            pass
    finally:
        release.set()
        process.join(10)
    assert process.exitcode == 0
    assert lock_path().exists()


def test_process_crash_releases_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("MAILBRAIN_HOME", str(tmp_path))
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(target=hold_lock, args=(str(tmp_path), ready, release))
    process.start()
    assert ready.wait(10)
    process.kill()
    process.join(10)
    with process_lock() as handle:
        assert not handle.closed
    assert handle.closed
    assert lock_path().exists()
