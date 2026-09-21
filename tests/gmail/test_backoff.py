import pytest
from httplib2 import HttpLib2Error

from mailbrain.gmail import backoff


class HttpFailure(Exception):
    def __init__(self, status, reason=None):
        self.resp = type("Resp", (), {"status": status})()
        self.content = (
            f'{{"error":{{"errors":[{{"reason":"{reason}"}}]}}}}'.encode()
            if reason
            else b"{}"
        )


def test_returns_value_without_retry():
    calls = []
    result = backoff.with_backoff(lambda: 42, sleep=lambda d: calls.append(d))
    assert result == 42
    assert calls == []


def test_retries_then_succeeds():
    attempts = {"n": 0}
    slept: list[float] = []

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ValueError("transient")
        return "ok"

    result = backoff.with_backoff(
        flaky,
        base_delay=1.0,
        sleep=slept.append,
        is_retryable=lambda exc: isinstance(exc, ValueError),
    )
    assert result == "ok"
    assert attempts["n"] == 3
    assert slept == [1.0, 2.0]


def test_non_retryable_raises_immediately():
    attempts = {"n": 0}

    def boom():
        attempts["n"] += 1
        raise KeyError("fatal")

    with pytest.raises(KeyError):
        backoff.with_backoff(
            boom, sleep=lambda d: None, is_retryable=lambda exc: False
        )
    assert attempts["n"] == 1


def test_gives_up_after_max_attempts():
    attempts = {"n": 0}

    def always_fail():
        attempts["n"] += 1
        raise ValueError("nope")

    with pytest.raises(ValueError):
        backoff.with_backoff(
            always_fail,
            max_attempts=4,
            sleep=lambda d: None,
            is_retryable=lambda exc: True,
        )
    assert attempts["n"] == 4


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_read_retry_classifier_accepts_transient_http_statuses(status):
    assert backoff.is_retryable_read(HttpFailure(status)) is True


@pytest.mark.parametrize("reason", ["rateLimitExceeded", "userRateLimitExceeded"])
def test_read_retry_classifier_accepts_only_rate_limit_403(reason):
    assert backoff.is_retryable_read(HttpFailure(403, reason)) is True


def test_read_retry_classifier_rejects_permanent_auth_403():
    assert backoff.is_retryable_read(HttpFailure(403, "forbidden")) is False


@pytest.mark.parametrize(
    "error", [TimeoutError(), ConnectionError(), OSError(), HttpLib2Error()]
)
def test_read_retry_classifier_accepts_transport_errors(error):
    assert backoff.is_retryable_read(error) is True
