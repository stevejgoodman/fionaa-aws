import os

from langchain_aws import ChatBedrockConverse
from langchain_core.runnables import Runnable

# Uses cross-region inference profile for Claude Sonnet 4.5. Sonnet 5 is
# cheaper per-token and was tried here, but real Converse calls returned
# AccessDeniedException ("not available for this account") in every
# constituent region even after accepting the model-access agreement and
# get-foundation-model-availability reporting AVAILABLE -- looks like a
# gated release needing AWS Sales-mediated access, not something self-service
# agreement acceptance unlocks. Revisit once that's confirmed granted.
# https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html
MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"


def load_model() -> Runnable:
    """Get Bedrock model client using IAM credentials.

    Uses the Converse API wrapper, not the legacy ChatBedrock (InvokeModel)
    one -- create_agent binds response_format for structured output
    (check_companies_house), and ChatBedrock passes that kwarg straight
    through into the native Anthropic request body, which Bedrock rejects
    with "response_format: Extra inputs are not permitted". Converse
    supports response_format/structured output natively.

    Attaches the guardrail created in cdk-stack.ts (FionaaGuardrail) when
    FIONAA_GUARDRAIL_ID/FIONAA_GUARDRAIL_VERSION are set -- read lazily here
    (not at module import) so local runs/tests without those env vars still
    work unchanged, the same convention gateway.py uses for its own env
    reads. trace is "disabled": "enabled" would return the original PII/
    harmful text that triggered a filter in the API response -- see
    cdk-stack.ts's FionaaGuardrail comment for what this guardrail covers
    (and deliberately doesn't).

    Bound with cache_control so Bedrock adds a cachePoint after the system
    prompt, tool definitions, and the last message's content (see
    langchain_aws's ChatBedrockConverse._apply_cache_points). Each
    workflow/*.py node's system_prompt (prompts.py) and tool set are static
    per node, so they get reused across every application; the 1h TTL suits
    the lower, steadier request volume of a loan-review workload rather than
    the 5m default. Returns a RunnableBinding, not a bare ChatBedrockConverse
    -- .bind() forwards attribute access (model_id, guardrail_config) to the
    wrapped model via __getattr__, so existing callers/tests are unaffected.
    """
    kwargs: dict = {"model_id": MODEL_ID}
    guardrail_id = os.environ.get("FIONAA_GUARDRAIL_ID")
    guardrail_version = os.environ.get("FIONAA_GUARDRAIL_VERSION")
    if guardrail_id and guardrail_version:
        kwargs["guardrail_config"] = {
            "guardrail_identifier": guardrail_id,
            "guardrail_version": guardrail_version,
            "trace": "disabled",
        }
    return ChatBedrockConverse(**kwargs).bind(cache_control={"ttl": "1h"})
