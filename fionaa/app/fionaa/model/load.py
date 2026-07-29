from langchain_aws import ChatBedrock

# Uses global inference profile for Claude Sonnet 4.5
# https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def load_model() -> ChatBedrock:
    """Get Bedrock model client using IAM credentials."""
    return ChatBedrock(model_id=MODEL_ID)
