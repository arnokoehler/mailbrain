"""Retry-with-exponential-backoff for transient Gmail API errors."""

import time
from collections.abc import Callable

# Gmail returns these for rate limiting / transient server issues.
RETRYABLE_STATUS = {403, 429, 500, 503}


def _status_of(exc: Exception) -> int | None:
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    return status if isinstance(status, int) else None


def default_is_retryable(exc: Exception) -> bool:
    return _status_of(exc) in RETRYABLE_STATUS


def with_backoff[T](
    fn: Callable[[], T],
    *,
    max_attempts: int = 5,
    base_delay: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    is_retryable: Callable[[Exception], bool] = default_is_retryable,
) -> T:
    """Call `fn`, retrying on retryable exceptions with exponential backoff."""
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            attempt += 1
            if attempt >= max_attempts or not is_retryable(exc):
                raise
            sleep(base_delay * 2 ** (attempt - 1))
