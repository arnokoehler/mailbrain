import pytest

from mailbrain.gmail import backoff


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
