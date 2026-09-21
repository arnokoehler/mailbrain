"""Retry-with-exponential-backoff for transient Gmail API errors."""

import json
import time
from collections.abc import Callable
from typing import Any

from google.auth.exceptions import TransportError

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
RATE_LIMIT_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}


def _status_of(exc: Exception) -> int | None:
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    return status if isinstance(status, int) else None


def _contains_rate_limit_reason(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            key == "reason" and item in RATE_LIMIT_REASONS
            or _contains_rate_limit_reason(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_rate_limit_reason(item) for item in value)
    return False


def _is_rate_limit_403(exc: Exception) -> bool:
    if _status_of(exc) != 403:
        return False
    details = getattr(exc, "error_details", None)
    if _contains_rate_limit_reason(details):
        return True
    content = getattr(exc, "content", None)
    if isinstance(content, bytes):
        content = content.decode(errors="replace")
    if isinstance(content, str):
        try:
            return _contains_rate_limit_reason(json.loads(content))
        except json.JSONDecodeError:
            return any(reason in content for reason in RATE_LIMIT_REASONS)
    return False


def default_is_retryable(exc: Exception) -> bool:
    status = _status_of(exc)
    return status in RETRYABLE_STATUS or _is_rate_limit_403(exc)


def is_retryable_read(exc: Exception) -> bool:
    return (
        default_is_retryable(exc)
        or isinstance(exc, (TimeoutError, ConnectionError, OSError, TransportError))
        or exc.__class__.__module__.startswith("httplib2")
    )


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
