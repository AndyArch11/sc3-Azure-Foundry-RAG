from __future__ import annotations

import pytest

from runtime.assessment_orchestration import azure_assessment


class _FakeAssessmentAgent:
    def retrieve_corpus_grounding(self, artifact):
        return type("Grounding", (), {"corpus_a_results": [], "corpus_b_results": []})()

    def generate_assessment(self, artifact, grounding, *, validation_mode: str = "hard"):
        return {
            "schema_version": "v1.1",
            "executive_summary": "ok",
            "scope_and_inputs": [artifact.canonical_url],
            "controls_assessed": ["ID.AM-1"],
            "guidance_applied": [],
            "findings": [
                {
                    "finding_id": "f-1",
                    "requirement_id": "ID.AM-1",
                    "framework": "NIST CSF",
                    "status": "insufficient_evidence",
                    "severity": "low",
                    "rationale": "test",
                    "evidence_sources": ["azure-extract"],
                    "gaps": ["missing details"],
                    "recommendations": ["collect more evidence"],
                }
            ],
            "overall_risk_rating": "low",
            "missing_evidence": ["details"],
            "recommended_actions": ["action"],
            "citations": [artifact.canonical_url],
            "metadata": {"framework_scope": "NIST CSF"},
        }


class _FakeAzureMcp:
    def __init__(self, *, credential=None):
        self.credential = credential
        self.scope = None

    def resolve_target(self, target_reference: str, *, requester_context=None):
        self.scope = target_reference
        return type(
            "Resolved",
            (),
            {
                "target_id": "azure-target",
                "provider": "azure",
                "target_type": "resource_scope",
                "canonical_url": target_reference,
                "title": "Azure scope",
                "container_id": "sub-1",
                "metadata": {},
            },
        )()

    def get_content_by_id(self, target_id: str, *, identity_mode: str, include_discussion_context: bool = False):
        return type(
            "Artifact",
            (),
            {
                "provider": "azure",
                "target_id": target_id,
                "canonical_url": self.scope,
                "title": "Azure scope",
                "content": "{}",
                "metadata": {},
                "owner": None,
                "last_editor": None,
                "discussion_context": [],
            },
        )()

    def check_user_access(self, target_id: str, delegated_user_context: dict):
        raise AssertionError("not expected in app_only mode")


def test_run_azure_assessment_uses_shared_orchestrator_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(azure_assessment, "AzureMCPServer", _FakeAzureMcp)
    monkeypatch.setattr(
        azure_assessment,
        "create_search_backed_assessment_agent_from_env",
        lambda env: _FakeAssessmentAgent(),
    )

    result = azure_assessment.run_azure_assessment(
        subscription_id="sub-1",
        resource_group="rg-1",
        resource_ids=[],
        controls_framework="NIST CSF",
        env={},
        credential=None,
    )

    assert result["schema_version"] == "v1.1"
    assert result["findings"][0]["framework"] == "NIST CSF"


def test_run_azure_assessment_accepts_non_nist_framework(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(azure_assessment, "AzureMCPServer", _FakeAzureMcp)
    monkeypatch.setattr(
        azure_assessment,
        "create_search_backed_assessment_agent_from_env",
        lambda env: _FakeAssessmentAgent(),
    )

    result = azure_assessment.run_azure_assessment(
        subscription_id="sub-1",
        resource_group="rg-1",
        resource_ids=[],
        controls_framework="ISM",
        env={},
        credential=None,
    )

    assert result["schema_version"] == "v1.1"
