"""Configuration and import boundaries must work without deployment credentials."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
from config import RuntimeSettings


def test_imports_do_not_require_aws_configuration_or_create_clients():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("FIONAA_", "AWS_", "AGENTCORE_"))}
    code = '''
import boto3
from unittest.mock import patch
with patch.object(boto3, "client", side_effect=AssertionError("client during import")):
    import schemas
    assert "storage" not in __import__("sys").modules
    assert "workflow.state" not in __import__("sys").modules
    from domain.applications import ApplicationFormSchema
    from domain.documents import BankStatementSchema
    from domain.assessments import FinalDecisionResult
    assert schemas.ApplicationFormSchema is ApplicationFormSchema
    assert schemas.BankStatementSchema is BankStatementSchema
    assert schemas.FinalDecisionResult is FinalDecisionResult
    import model.load
    with patch.object(model.load, "load_model", side_effect=AssertionError("model during import")):
        import graph
        graph.build_graph()
'''
    subprocess.run([sys.executable, "-c", code], env=env,
                   cwd=Path(__file__).resolve().parents[1], check=True,
                   capture_output=True, text=True)


def test_missing_configuration_fails_when_loaded(monkeypatch):
    monkeypatch.delenv("FIONAA_APPLICATIONS_BUCKET", raising=False)
    with pytest.raises(ValueError, match="FIONAA_APPLICATIONS_BUCKET"):
        RuntimeSettings.from_environment()


def test_settings_are_loaded_fresh(monkeypatch):
    first = RuntimeSettings.from_environment()
    monkeypatch.setenv("FIONAA_APPLICATIONS_BUCKET", "new-bucket")
    second = RuntimeSettings.from_environment()
    assert second.applications_bucket == "new-bucket"
    assert first.applications_bucket != second.applications_bucket
