import os

from langchain_aws import ChatBedrockConverse

# Uses global inference profile for Claude Sonnet 4.5
# https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html
MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"


def load_model() -> ChatBedrockConverse:
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
    return ChatBedrockConverse(**kwargs)
