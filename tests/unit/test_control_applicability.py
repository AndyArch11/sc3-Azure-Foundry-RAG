"""Tests for control applicability classification logic."""

from __future__ import annotations

import pytest

from runtime.assessment_orchestration.control_applicability import (
    classify_control_applicability, enrich_control_with_applicability)
from runtime.assessment_orchestration.validate_control_applicability import (
    review_ambiguous_controls_with_llm, validate_controls_applicability)


def test_classify_technical_control() -> None:
    control = {
        "requirement_id": "PR.AC-1",
        "framework": "NIST CSF",
        "control_family": "Access Control",
        "requirement_text": "Enforce multifactor authentication for privileged administrative access.",
        "guidance_text": "Configure MFA and least-privilege access restrictions.",
    }

    metadata = classify_control_applicability(control)

    assert metadata.scope == "technical"
    assert metadata.confidence >= 0.90
    assert metadata.technical_matches > 0
    assert metadata.process_matches == 0
    assert not metadata.uncertain


def test_classify_process_control() -> None:
    control = {
        "requirement_id": "GV-1",
        "framework": "NIST CSF",
        "control_family": "Governance",
        "requirement_text": "Establish, communicate, and review cybersecurity policy and roles and responsibilities.",
        "guidance_text": "Document governance oversight and approval workflows.",
    }

    metadata = classify_control_applicability(control)

    assert metadata.scope == "governance"
    assert metadata.confidence >= 0.90
    assert metadata.governance_id_match


def test_classify_process_heavy_control() -> None:
    control = {
        "requirement_id": "E8-1",
        "framework": "Essential Eight",
        "control_family": "MFA",
        "requirement_text": "Implement a procedure and policy for personnel management.",
        "guidance_text": "Document governance oversight of training and personnel approval processes.",
    }

    metadata = classify_control_applicability(control)

    assert metadata.scope == "process"
    assert metadata.confidence >= 0.60  # May be mixed (balanced) with lower confidence
    assert metadata.process_matches > 0


def test_classify_mixed_control() -> None:
    control = {
        "requirement_id": "OP-5",
        "framework": "Test",
        "control_family": "Operations",
        "requirement_text": "Implement encryption policies and procedures with approval workflow.",
        "guidance_text": "Configure TLS for communications and review governance for key management.",
    }

    metadata = classify_control_applicability(control)

    assert metadata.scope == "mixed"
    assert metadata.confidence < 0.75
    assert metadata.technical_matches > 0
    assert metadata.process_matches > 0
    assert metadata.uncertain


def test_classify_no_signal_control() -> None:
    control = {
        "requirement_id": "AC-2",
        "framework": "Test",
        "control_family": "Access",
        "requirement_text": "Review and verify access requirements.",
        "guidance_text": "Ensure access is appropriate.",
    }

    metadata = classify_control_applicability(control)

    assert metadata.scope == "mixed"
    assert metadata.confidence == 0.50
    assert metadata.technical_matches == 0
    assert metadata.process_matches == 0
    assert metadata.uncertain


def test_enrich_control_with_applicability() -> None:
    control = {
        "requirement_id": "PR.AC-1",
        "framework": "NIST CSF",
        "control_family": "Access Control",
        "requirement_text": "Enforce multifactor authentication.",
        "guidance_text": "Configure MFA.",
    }

    enriched = enrich_control_with_applicability(control)

    assert enriched["control_applicability_scope"] == "technical"
    assert 0.0 <= enriched["applicability_confidence"] <= 1.0
    assert isinstance(enriched["applicability_uncertain"], bool)
    assert enriched is control  # Mutates in place


def test_runtime_filtering_prefers_high_confidence_technical() -> None:
    """Verify that runtime filtering uses pre-computed scores when available."""
    from runtime.assessment_orchestration.assessment_runtime import \
        _azure_control_is_likely_applicable

    control_with_metadata = {
        "requirement_id": "PR.AC-1",
        "control_applicability_scope": "technical",
        "applicability_confidence": 0.92,
        "applicability_uncertain": False,
    }

    assert _azure_control_is_likely_applicable(control_with_metadata)


def test_runtime_filtering_excludes_high_confidence_process() -> None:
    """Verify that runtime filtering excludes high-confidence process controls."""
    from runtime.assessment_orchestration.assessment_runtime import \
        _azure_control_is_likely_applicable

    control_with_metadata = {
        "requirement_id": "OP-2",
        "control_applicability_scope": "process",
        "applicability_confidence": 0.91,
        "applicability_uncertain": False,
    }

    assert not _azure_control_is_likely_applicable(control_with_metadata)


def test_runtime_filtering_includes_mixed_control() -> None:
    """Verify that runtime filtering includes mixed-signal controls."""
    from runtime.assessment_orchestration.assessment_runtime import \
        _azure_control_is_likely_applicable

    control_with_metadata = {
        "requirement_id": "AC-5",
        "control_applicability_scope": "mixed",
        "applicability_confidence": 0.55,
        "applicability_uncertain": True,
    }

    assert _azure_control_is_likely_applicable(control_with_metadata)


def test_review_ambiguous_controls_with_llm_reports_agreement() -> None:
    controls = [
        {
            "requirement_id": "OP-5",
            "framework": "Test",
            "control_family": "Operations",
            "requirement_text": "Implement encryption policies and procedures with approval workflow.",
            "guidance_text": "Configure TLS for communications and review governance for key management.",
        }
    ]

    def chat_completion(_messages: list[dict[str, str]]) -> str:
        return '{"scope":"mixed","confidence":0.78,"rationale":"Balanced technical configuration and process workflow language."}'

    result = review_ambiguous_controls_with_llm(
        controls,
        confidence_threshold=0.75,
        max_controls=5,
        chat_completion=chat_completion,
    )

    assert result["reviewed_controls"] == 1
    assert result["agreements"] == 1
    assert result["disagreements"] == 0
    assert result["results"][0]["llm_scope"] == "mixed"


def test_validate_controls_applicability_includes_llm_review_summary(tmp_path) -> None:
    controls_file = tmp_path / "controls.jsonl"
    controls_file.write_text(
        "\n".join(
            [
                '{"requirement_id":"OP-5","framework":"Test","control_family":"Operations","requirement_text":"Implement encryption policies and procedures with approval workflow.","guidance_text":"Configure TLS for communications and review governance for key management."}',
                '{"requirement_id":"GV-1","framework":"Test","control_family":"Governance","requirement_text":"Establish and review cybersecurity policy and roles and responsibilities.","guidance_text":"Document governance oversight and approval workflows."}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def chat_completion(_messages: list[dict[str, str]]) -> str:
        return '{"scope":"mixed","confidence":0.81,"rationale":"Contains both technical implementation and procedure language."}'

    result = validate_controls_applicability(
        controls_source=str(controls_file),
        confidence_threshold=0.75,
        review_with_llm=True,
        llm_max_controls=5,
        chat_completion=chat_completion,
    )

    assert "llm_review" in result
    assert result["llm_review"]["reviewed_controls"] == 1
    assert result["llm_review"]["agreements"] == 1
