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
    """
    return ChatBedrockConverse(model_id=MODEL_ID)
