"""Unit tests for model/load.py -- no real Bedrock call, just checking the
ChatBedrockConverse kwargs load_model() builds under each env-var state."""

from model.load import MODEL_ID, load_model


def test_load_model_without_guardrail_env_vars(monkeypatch):
    """FIONAA_GUARDRAIL_ID/FIONAA_GUARDRAIL_VERSION unset (the case for every
    existing test, local run, and the deepeval_evals/ harness) -- load_model
    must behave exactly as before this guardrail was added."""
    monkeypatch.delenv("FIONAA_GUARDRAIL_ID", raising=False)
    monkeypatch.delenv("FIONAA_GUARDRAIL_VERSION", raising=False)

    model = load_model()

    assert model.model_id == MODEL_ID
    assert model.guardrail_config is None


def test_load_model_with_guardrail_env_vars(monkeypatch):
    monkeypatch.setenv("FIONAA_GUARDRAIL_ID", "abc123")
    monkeypatch.setenv("FIONAA_GUARDRAIL_VERSION", "1")

    model = load_model()

    assert model.model_id == MODEL_ID
    assert model.guardrail_config == {
        "guardrail_identifier": "abc123",
        "guardrail_version": "1",
        # MUST be disabled in production -- see load_model's docstring.
        "trace": "disabled",
    }


def test_load_model_requires_both_guardrail_env_vars(monkeypatch):
    """Only one of the two set -- e.g. a half-configured environment --
    must not attach a guardrail_config with a missing field, since AWS
    silently skips guardrail application if either is absent (see
    cdk-stack.ts's FionaaGuardrail comment)."""
    monkeypatch.setenv("FIONAA_GUARDRAIL_ID", "abc123")
    monkeypatch.delenv("FIONAA_GUARDRAIL_VERSION", raising=False)

    model = load_model()

    assert model.guardrail_config is None
