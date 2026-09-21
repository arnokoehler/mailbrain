"""Process-level locking for MailBrain state."""

from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

from mailbrain.paths import ensure_app_dir, lock_path


class LockUnavailableError(RuntimeError):
    pass


@contextmanager
def process_lock(path: Path | None = None) -> Iterator[IO[str]]:
    target = path or lock_path()
    if path is None:
        ensure_app_dir()
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
    handle = target.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockUnavailableError(f"MailBrain state is locked: {target}") from exc
        yield handle
    finally:
        handle.close()
