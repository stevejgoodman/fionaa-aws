"""Shared pytest setup for deepeval_evals/ -- loaded before any test module,
so these env vars are in place before metrics.py's module-level
AmazonBedrockModel(...) reads deepeval's settings singleton (get_settings()
recomputes off an env fingerprint, so this ordering matters).

Raises DeepEval's own Bedrock-call retry budget. AmazonBedrockModel's
a_generate is already wrapped in a @retry_bedrock/tenacity retry, but its
defaults (DEEPEVAL_RETRY_MAX_ATTEMPTS=2, DEEPEVAL_RETRY_CAP_SECONDS=5.0) are
too thin for real Bedrock throttling -- that's what was producing the
tenacity.RetryError the README's "Known gaps" section describes, not an
absence of retry logic. Widening the budget here absorbs the transient
ThrottlingException/ReadTimeoutError bursts instead of surfacing them as
scenario failures.

This doesn't remove the need to run one deepeval_evals/*.py file at a time
(see README.md) -- concurrent *processes* hitting the same Bedrock quota is
a separate problem from a single process's retry budget being too thin. See
also assert_test(..., run_async=False) in each test file, which serializes
a scenario's own metrics (up to 4 judge calls) instead of firing them
concurrently.
"""

from __future__ import annotations

import os

os.environ.setdefault("DEEPEVAL_RETRY_MAX_ATTEMPTS", "6")
os.environ.setdefault("DEEPEVAL_RETRY_INITIAL_SECONDS", "2.0")
os.environ.setdefault("DEEPEVAL_RETRY_EXP_BASE", "2.0")
os.environ.setdefault("DEEPEVAL_RETRY_CAP_SECONDS", "30.0")
