from __future__ import annotations

import json

from runtime.assessment_orchestration.assessment_runtime import (
    AssessmentRuntimeConfig,
    SearchBackedAssessmentAgent,
)
from runtime.assessment_orchestration.models import AssessedArtifactPackage, CorpusGroundingPackage, PersonReference
from runtime.assessment_orchestration.polling_worker import _render_assessment_comment


class _FakeSearchClient:
    def __init__(self, results: list[dict]) -> None:
        self._results = results
        self.calls: list[dict] = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return list(self._results)


def _config() -> AssessmentRuntimeConfig:
    return AssessmentRuntimeConfig(
        search_endpoint="https://search.example",
        openai_endpoint="https://openai.example",
        controls_top_k=2,
        guidance_top_k=2,
    )


def _artifact() -> AssessedArtifactPackage:
    return AssessedArtifactPackage(
        provider="confluence",
        target_id="123",
        canonical_url="https://example.atlassian.net/wiki/spaces/SEC/pages/123",
        title="Essential Eight MFA Review",
        content="Administrative access requires MFA and emergency access accounts must be monitored.",
        metadata={"version": 7},
        owner=PersonReference(principal_id="owner-1", display_name="Owner One"),
        last_editor=PersonReference(principal_id="editor-1", display_name="Editor One"),
        discussion_context=[
            {"author": "Auditor", "text": "Please review administrative controls and privileged access."}
        ],
    )


def test_search_backed_assessment_agent_retrieves_grounding() -> None:
    controls_client = _FakeSearchClient(
        [
            {
                "requirement_id": "E8-1",
                "framework": "Essential Eight",
                "framework_version": "2023",
                "control_family": "Multi-factor authentication",
                "maturity_level": "ML1",
                "requirement_text": "Require MFA for administrative access.",
                "guidance_text": "Use phishing-resistant MFA where possible.",
                "source_uri": "controls://e8-1",
                "@search.score": 4.2,
            }
        ]
    )
    evidence_client = _FakeSearchClient(
        [
            {
                "content": "Operational guidance for MFA enforcement and break-glass review.",
                "source_name": "Essential Eight Guidance",
                "source_path": "/guidance/e8",
                "corpus": "b",
                "corpus_role": "guidance",
                "@search.score": 3.8,
            }
        ]
    )
    agent = SearchBackedAssessmentAgent(
        config=_config(),
        controls_search_client=controls_client,
        evidence_search_client=evidence_client,
        embed_query=lambda question: [0.1, 0.2, 0.3],
        chat_completion=lambda messages: "{}",
    )

    grounding = agent.retrieve_corpus_grounding(_artifact())

    assert len(grounding.corpus_a_results) == 1
    assert grounding.corpus_a_results[0]["requirement_id"] == "E8-1"
    assert len(grounding.corpus_b_results) == 1
    assert grounding.corpus_b_results[0]["source_name"] == "Essential Eight Guidance"
    assert controls_client.calls[0]["top"] >= 2
    assert evidence_client.calls[0]["filter"] == "corpus eq 'b'"


def test_search_backed_assessment_agent_generates_validated_report() -> None:
    report_payload = {
        "schema_version": "v1.1",
        "executive_summary": "The page partially aligns with MFA requirements but lacks full break-glass evidence.",
        "scope_and_inputs": ["Confluence page content", "Footer comment discussion"],
        "controls_assessed": ["E8-1", "E8-2"],
        "guidance_applied": ["Essential Eight Guidance"],
        "findings": [
            {
                "finding_id": "finding-1",
                "requirement_id": "E8-1",
                "framework": "Essential Eight",
                "status": "partially_compliant",
                "severity": "high",
                "rationale": "MFA is stated for admins, but no evidence was supplied for emergency access workflow coverage.",
                "evidence_sources": ["Essential Eight MFA Review", "Essential Eight Guidance"],
                "gaps": ["No break-glass review evidence"],
                "recommendations": ["Document and test emergency access account controls."],
            }
        ],
        "overall_risk_rating": "high",
        "missing_evidence": ["Break-glass account review records"],
        "recommended_actions": ["Add evidence of periodic emergency access review."],
        "citations": ["controls://e8-1", "https://example.atlassian.net/wiki/spaces/SEC/pages/123"],
    }
    agent = SearchBackedAssessmentAgent(
        config=_config(),
        controls_search_client=_FakeSearchClient([]),
        evidence_search_client=_FakeSearchClient([]),
        embed_query=lambda question: [0.1],
        chat_completion=lambda messages: json.dumps(report_payload),
    )
    grounding = CorpusGroundingPackage(
        corpus_a_results=[
            {
                "requirement_id": "E8-1",
                "framework": "Essential Eight",
                "framework_version": "2023",
                "control_family": "MFA",
                "requirement_text": "Require MFA for administrative access.",
                "guidance_text": "Apply MFA.",
            }
        ],
        corpus_b_results=[{"source_name": "Essential Eight Guidance", "content": "Apply MFA everywhere."}],
    )

    report = agent.generate_assessment(_artifact(), grounding)

    assert report["schema_version"] == "v1.1"
    assert report["overall_risk_rating"] == "high"
    assert report["findings"][0]["requirement_id"] == "E8-1"
    assert report["metadata"]["target_id"] == "123"
    assert report["metadata"]["grounding_counts"]["corpus_a"] == 1


def test_search_backed_assessment_agent_soft_validation_falls_back() -> None:
    agent = SearchBackedAssessmentAgent(
        config=AssessmentRuntimeConfig(
            search_endpoint="https://search.example",
            openai_endpoint="https://openai.example",
            validation_mode="soft",
        ),
        controls_search_client=_FakeSearchClient([]),
        evidence_search_client=_FakeSearchClient([]),
        embed_query=lambda question: [0.1],
        chat_completion=lambda messages: '{"schema_version": "v1.1", "executive_summary": "broken"}',
    )

    report = agent.generate_assessment(
        _artifact(),
        CorpusGroundingPackage(corpus_a_results=[], corpus_b_results=[]),
        validation_mode="soft",
    )

    assert report["schema_version"] == "v1.1"
    assert report["findings"][0]["status"] == "insufficient_evidence"
    assert "fallback" in report["findings"][0]["finding_id"]


def test_search_backed_assessment_agent_includes_azure_applicability_guidance() -> None:
    captured_messages: list[dict[str, str]] = []

    def _chat(messages):
        captured_messages.extend(messages)
        return json.dumps(
            {
                "schema_version": "v1.1",
                "executive_summary": "Resource configuration evidence was assessed with applicability caution.",
                "scope_and_inputs": ["Azure resource configuration extract"],
                "controls_assessed": ["PR.AC-05"],
                "guidance_applied": [],
                "findings": [
                    {
                        "finding_id": "finding-1",
                        "requirement_id": "PR.AC-05",
                        "framework": "NIST CSF",
                        "status": "insufficient_evidence",
                        "severity": "medium",
                        "rationale": "Process-oriented evidence is not available from resource configuration alone.",
                        "evidence_sources": ["azure-extract"],
                        "gaps": ["procedural evidence"],
                        "recommendations": ["collect operational evidence"],
                    }
                ],
                "overall_risk_rating": "medium",
                "missing_evidence": ["procedural evidence"],
                "recommended_actions": ["collect operational evidence"],
                "citations": ["/subscriptions/sub-1/resourceGroups/rg-1"],
            }
        )

    agent = SearchBackedAssessmentAgent(
        config=_config(),
        controls_search_client=_FakeSearchClient([]),
        evidence_search_client=_FakeSearchClient([]),
        embed_query=lambda question: [0.1],
        chat_completion=_chat,
    )
    artifact = AssessedArtifactPackage(
        provider="azure",
        target_id="azure-target",
        canonical_url="/subscriptions/sub-1/resourceGroups/rg-1",
        title="Azure extract",
        content="{}",
        metadata={
            "framework_filter_override": "NIST CSF",
            "assessment_evidence_scope": "azure_resource_configuration_and_policy_assignments",
            "framework_applicability_model": "azure_technical_control_prefilter_v1",
        },
        owner=None,
        last_editor=None,
        discussion_context=[],
    )

    report = agent.generate_assessment(artifact, CorpusGroundingPackage(corpus_a_results=[], corpus_b_results=[]))

    assert any(
        "do not mark process, governance, training, incident-response" in message.get("content", "").lower()
        for message in captured_messages
        if message.get("role") == "user"
    )
    assert report["metadata"]["assessment_evidence_scope"] == "azure_resource_configuration_and_policy_assignments"
    assert report["metadata"]["framework_applicability_model"] == "azure_technical_control_prefilter_v1"


def test_search_backed_assessment_agent_filters_non_technical_azure_controls() -> None:
    controls_client = _FakeSearchClient(
        [
            {
                "requirement_id": "GV-1",
                "framework": "NIST CSF",
                "framework_version": "2.0",
                "control_family": "Governance",
                "maturity_level": None,
                "requirement_text": "Establish, communicate, and review cybersecurity policy and roles and responsibilities.",
                "guidance_text": "Document governance oversight and approval workflows.",
                "source_uri": "controls://gv-1",
                "@search.score": 5.0,
            },
            {
                "requirement_id": "PR.AC-1",
                "framework": "NIST CSF",
                "framework_version": "2.0",
                "control_family": "Access Control",
                "maturity_level": None,
                "requirement_text": "Enforce multifactor authentication for privileged administrative access.",
                "guidance_text": "Configure MFA and least-privilege access restrictions.",
                "source_uri": "controls://pr.ac-1",
                "@search.score": 4.5,
            },
        ]
    )
    agent = SearchBackedAssessmentAgent(
        config=_config(),
        controls_search_client=controls_client,
        evidence_search_client=_FakeSearchClient([]),
        embed_query=lambda question: [0.1],
        chat_completion=lambda messages: "{}",
    )
    artifact = AssessedArtifactPackage(
        provider="azure",
        target_id="azure-target",
        canonical_url="/subscriptions/sub-1/resourceGroups/rg-1",
        title="Azure extract",
        content="{}",
        metadata={
            "framework_filter_override": "NIST CSF",
            "assessment_evidence_scope": "azure_resource_configuration_and_policy_assignments",
        },
        owner=None,
        last_editor=None,
        discussion_context=[],
    )

    grounding = agent.retrieve_corpus_grounding(artifact)

    assert [item["requirement_id"] for item in grounding.corpus_a_results] == ["PR.AC-1"]
    assert artifact.metadata["controls_retrieved_before_applicability_filter"] == 2
    assert artifact.metadata["controls_filtered_for_applicability"] == 1
    assert artifact.metadata["controls_retained_after_applicability_filter"] == 1


def test_render_assessment_comment_includes_structured_sections() -> None:
    html = _render_assessment_comment(
        {
            "executive_summary": "The page is partially compliant.",
            "overall_risk_rating": "high",
            "findings": [
                {
                    "requirement_id": "E8-1",
                    "framework": "Essential Eight",
                    "status": "partially_compliant",
                    "severity": "high",
                    "rationale": "Break-glass evidence is missing.",
                    "evidence_sources": ["Essential Eight MFA Review"],
                    "gaps": ["No periodic review record"],
                    "recommendations": ["Add quarterly review evidence"],
                }
            ],
            "recommended_actions": ["Add evidence of quarterly review."],
            "missing_evidence": ["Break-glass account review records"],
            "citations": ["controls://e8-1"],
        }
    )

    assert "Overall risk" in html
    assert "Key findings" in html
    assert "Break-glass evidence is missing." in html
    assert "Recommended actions" in html
    assert "Citations" in html