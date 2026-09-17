"""Shared resilience helpers for agent.ainvoke calls that cross an external
boundary -- Bedrock model invocation, or an AgentCore Gateway tool call out
to a real external service (Companies House, web search). Retries transient
failures with backoff, and gates each external dependency behind its own
circuit breaker so a sustained outage degrades every concurrent
application's node straight to its fallback instead of each one spending
its own retry budget hammering a dependency that's already down.

Deliberately narrow in what counts as retryable -- see `is_retryable`.
Everything else (AccessDeniedException, ValidationException, a Gateway tool
returning a structured "not found") is left to propagate or to each node's
own domain logic, not retried here.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from botocore.exceptions import ClientError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

logger = logging.getLogger(__name__)

# Bedrock error codes treated as transient/capacity-related -- everything
# else (AccessDeniedException, ValidationException, ResourceNotFoundException)
# is a caller/config problem retrying won't fix.
_RETRYABLE_BEDROCK_CODES = frozenset({
    "ThrottlingException",
    "ModelTimeoutException",
    "ServiceUnavailableException",
    "InternalServerException",
})


def is_retryable(exc: BaseException) -> bool:
    """True for a transient Bedrock/Gateway failure worth retrying: a
    connection/timeout reaching the model or the Gateway itself, or a
    Bedrock error code in _RETRYABLE_BEDROCK_CODES."""
    if isinstance(exc, (TimeoutError, ConnectionError, httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(exc, ClientError):
        return exc.response.get("Error", {}).get("Code") in _RETRYABLE_BEDROCK_CODES
    return False


class CircuitOpenError(Exception):
    """Raised in place of calling a dependency whose circuit is open."""

    def __init__(self, name: str):
        super().__init__(f"circuit '{name}' is open -- dependency treated as unavailable")
        self.name = name


# Every exception type a caller needs to catch around ainvoke_resilient to
# degrade to its own fallback -- transient failures that exhausted their
# retries, plus a tripped circuit. Kept as one tuple so workflow/*.py nodes
# don't have to individually know the retryable-error set.
TRANSIENT_ERRORS = (TimeoutError, ConnectionError, httpx.HTTPError, ClientError, CircuitOpenError)


@dataclass
class CircuitBreaker:
    """Minimal in-process circuit breaker, one instance per external
    dependency (module-level in each workflow/*.py node that calls
    ainvoke_resilient). Not shared across processes or AgentCore Runtime
    workers -- each trips independently, which is fine here: the point is
    to stop one worker's own retries from piling onto a dependency that's
    already down, not to coordinate a fleet-wide decision.

    CLOSED (normal) -> OPEN (after failure_threshold consecutive failures,
    calls short-circuit immediately with CircuitOpenError) -> HALF_OPEN
    (once reset_timeout_seconds has passed, the next call is let through as
    a probe) -> CLOSED on that call's success, or back to OPEN on failure.
    """

    name: str
    failure_threshold: int = 5
    reset_timeout_seconds: float = 60.0
    _consecutive_failures: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)

    def _state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if time.monotonic() - self._opened_at >= self.reset_timeout_seconds:
            return "half_open"
        return "open"

    def before_call(self) -> None:
        if self._state() == "open":
            raise CircuitOpenError(self.name)

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold and self._opened_at is None:
            self._opened_at = time.monotonic()
            logger.warning(
                "circuit '%s' opened after %d consecutive failures",
                self.name, self._consecutive_failures,
            )


class ConcurrencyLimiter:
    """Caps how many concurrent node executions may be calling out to one
    external dependency at once, one instance per dependency (module-level
    in each workflow/*.py node, same as CircuitBreaker). Bounds load at the
    node level -- one check_companies_house/search_web invocation per
    concurrent application run -- rather than per individual Gateway tool
    call inside a node; coarser, but simple and enough to stop a burst of
    dozens of simultaneous submissions from all hitting Companies House/web
    search at the same instant, which is exactly what would trip *their*
    rate limits and cascade into the circuit breaker anyway.

    Per-process only, like CircuitBreaker -- each AgentCore Runtime worker
    enforces its own cap, so the effective fleet-wide ceiling is
    max_concurrent times the worker count, not a single global limit.

    max_concurrent is read from `env_var` at construction time (not
    hardcoded) so it can be tuned per-environment without a code change,
    same convention as FIONAA_GUARDRAIL_ID/FIONAA_GUARDRAIL_VERSION in
    model/load.py.
    """

    def __init__(self, name: str, env_var: str, default: int = 10):
        self.name = name
        value = os.environ.get(env_var)
        max_concurrent = int(value) if value else default
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def __call__(self):
        return self._semaphore


async def ainvoke_resilient(
    agent: Any, input_: Any, *, breaker: CircuitBreaker,
    limiter: ConcurrencyLimiter | None = None, max_attempts: int = 3,
) -> Any:
    """Runs agent.ainvoke(input_), retrying transient failures (see
    is_retryable) with exponential backoff and jitter, gated by `breaker` --
    a dependency with an open circuit fails immediately with
    CircuitOpenError instead of spending another `max_attempts` retries on
    it -- and, if `limiter` is given, bounded by its concurrency cap (the
    call blocks, queued, until a slot frees up; it does not fail fast the
    way an open circuit does). Callers are expected to catch
    TRANSIENT_ERRORS around this call and degrade to their own fallback,
    the same pattern check_companies_house already used for
    TimeoutError/ConnectionError before this existed."""
    breaker.before_call()

    @retry(
        retry=retry_if_exception(is_retryable),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(initial=1, max=10),
        reraise=True,
    )
    async def _call() -> Any:
        return await agent.ainvoke(input_)

    async def _call_with_retry() -> Any:
        try:
            result = await _call()
        except Exception:
            breaker.record_failure()
            raise
        breaker.record_success()
        return result

    if limiter is None:
        return await _call_with_retry()
    async with limiter():
        return await _call_with_retry()
