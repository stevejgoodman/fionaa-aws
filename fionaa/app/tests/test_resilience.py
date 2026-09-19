import asyncio

import pytest
from botocore.exceptions import ClientError

from fionaa.workflow.resilience import (
    CircuitBreaker, CircuitOpenError, ConcurrencyLimiter, ainvoke_resilient, is_retryable,
)


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, "Converse")


@pytest.mark.parametrize("exc,expected", [
    (TimeoutError("slow"), True),
    (ConnectionError("dropped"), True),
    (_client_error("ThrottlingException"), True),
    (_client_error("ModelTimeoutException"), True),
    (_client_error("ServiceUnavailableException"), True),
    (_client_error("InternalServerException"), True),
    (_client_error("AccessDeniedException"), False),
    (_client_error("ValidationException"), False),
    (ValueError("not transient"), False),
])
def test_is_retryable_classifies_by_error_type(exc, expected):
    assert is_retryable(exc) is expected


class FlakyAgent:
    """Fails `fail_times` times, then succeeds -- for exercising retry."""

    def __init__(self, fail_times: int, exc_factory=lambda: TimeoutError("slow")):
        self.fail_times = fail_times
        self.calls = 0
        self._exc_factory = exc_factory

    async def ainvoke(self, payload):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self._exc_factory()
        return {"ok": True}


class AlwaysFailsAgent:
    def __init__(self, exc_factory=lambda: TimeoutError("slow")):
        self.calls = 0
        self._exc_factory = exc_factory

    async def ainvoke(self, payload):
        self.calls += 1
        raise self._exc_factory()


async def test_ainvoke_resilient_retries_transient_failures_then_succeeds():
    agent = FlakyAgent(fail_times=2)
    breaker = CircuitBreaker(name="test", failure_threshold=5)
    result = await ainvoke_resilient(agent, {}, breaker=breaker, max_attempts=3)
    assert result == {"ok": True}
    assert agent.calls == 3


async def test_ainvoke_resilient_does_not_retry_non_transient_failures():
    agent = AlwaysFailsAgent(exc_factory=lambda: ValueError("not transient"))
    breaker = CircuitBreaker(name="test", failure_threshold=5)
    with pytest.raises(ValueError):
        await ainvoke_resilient(agent, {}, breaker=breaker, max_attempts=3)
    assert agent.calls == 1


async def test_circuit_breaker_opens_after_threshold_and_short_circuits():
    agent = AlwaysFailsAgent()
    breaker = CircuitBreaker(name="test", failure_threshold=2, reset_timeout_seconds=60)

    with pytest.raises(TimeoutError):
        await ainvoke_resilient(agent, {}, breaker=breaker, max_attempts=1)
    with pytest.raises(TimeoutError):
        await ainvoke_resilient(agent, {}, breaker=breaker, max_attempts=1)
    assert agent.calls == 2

    # Circuit is now open -- the next call fails fast without touching the agent.
    with pytest.raises(CircuitOpenError):
        await ainvoke_resilient(agent, {}, breaker=breaker, max_attempts=1)
    assert agent.calls == 2


async def test_circuit_breaker_half_opens_and_recovers_on_success():
    agent = FlakyAgent(fail_times=2)
    # A near-zero (not exactly zero -- see below) reset window so the probe
    # in this test doesn't need a real sleep.
    breaker = CircuitBreaker(name="test", failure_threshold=2, reset_timeout_seconds=0.001)

    with pytest.raises(TimeoutError):
        await ainvoke_resilient(agent, {}, breaker=breaker, max_attempts=1)
    with pytest.raises(TimeoutError):
        await ainvoke_resilient(agent, {}, breaker=breaker, max_attempts=1)
    assert breaker._state() == "open"

    await asyncio.sleep(0.01)
    assert breaker._state() == "half_open"

    # half_open lets the probe call through; it succeeds, closing the circuit.
    result = await ainvoke_resilient(agent, {}, breaker=breaker, max_attempts=1)
    assert result == {"ok": True}
    assert breaker._state() == "closed"


async def test_circuit_breaker_reopens_on_renewed_failures_after_recovery():
    breaker = CircuitBreaker(name="test", failure_threshold=2, reset_timeout_seconds=60)
    breaker.record_failure()
    breaker.record_success()  # as if a probe call succeeded, closing the circuit

    # record_success() reset the consecutive-failure count, so it takes a
    # fresh failure_threshold run of failures to re-open -- one failure
    # alone should not trip it again.
    breaker.record_failure()
    breaker.before_call()  # does not raise: only 1 of 2 needed failures so far
    breaker.record_failure()
    with pytest.raises(CircuitOpenError):
        breaker.before_call()


def test_concurrency_limiter_defaults(monkeypatch):
    monkeypatch.delenv("FIONAA_TEST_MAX_CONCURRENT", raising=False)
    limiter = ConcurrencyLimiter(name="test", env_var="FIONAA_TEST_MAX_CONCURRENT", default=3)
    assert limiter._semaphore._value == 3


def test_concurrency_limiter_reads_env_var(monkeypatch):
    monkeypatch.setenv("FIONAA_TEST_MAX_CONCURRENT", "7")
    limiter = ConcurrencyLimiter(name="test", env_var="FIONAA_TEST_MAX_CONCURRENT", default=3)
    assert limiter._semaphore._value == 7


async def test_concurrency_limiter_bounds_in_flight_calls():
    limiter = ConcurrencyLimiter(name="test", env_var="FIONAA_TEST_MAX_CONCURRENT", default=2)
    in_flight = 0
    max_in_flight = 0

    class TrackingAgent:
        async def ainvoke(self, payload):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return {"ok": True}

    agent = TrackingAgent()
    breakers = [CircuitBreaker(name=f"test-{i}", failure_threshold=99) for i in range(5)]
    await asyncio.gather(*[
        ainvoke_resilient(agent, {}, breaker=b, limiter=limiter, max_attempts=1) for b in breakers
    ])
    assert max_in_flight <= 2
