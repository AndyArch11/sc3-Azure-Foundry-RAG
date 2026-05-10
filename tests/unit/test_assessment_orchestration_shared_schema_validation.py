from __future__ import annotations

import importlib
import sys
from typing import Any
from unittest.mock import patch

from runtime.assessment_orchestration.schema_validation import (
    assert_named_schema,
    assert_schema_value,
    load_yaml_contract,
    to_plain_data,
)
from runtime.assessment_orchestration.validators import (
    validate_access_decision,
    validate_assessed_artifact_package,
    validate_assessment_job,
    validate_corpus_grounding_package,
    validate_delivery_outcome,
    validate_delivery_plan,
    validate_person_reference,
    validate_resolved_target,
)


def _load_shared_schemas() -> dict[str, Any]:
    return load_yaml_contract("docs/contracts/shared-schemas.yaml")


def test_runtime_validator_outputs_conform_to_shared_schemas() -> None:
    root = _load_shared_schemas()

    person = to_plain_data(
        validate_person_reference(
            {
                "principal_id": "user-1",
                "display_name": "User One",
                "email": "user.one@example.com",
            }
        )
    )
    assert_named_schema(root, "person_reference", person)

    job = to_plain_data(
        validate_assessment_job(
            {
                "job_id": "job-1",
                "source_type": "manual_request",
                "provider": "sharepoint",
                "target_id": "page-1",
                "target_url": "https://example.sharepoint.com/items/page-1",
                "trigger_type": "user_request",
                "request_identity_mode": "delegated",
                "delivery_policy": "inline_else_email",
                "correlation_id": "corr-1",
                "requester_id": "user-1",
                "requester_email": "user.one@example.com",
                "metadata": {},
            }
        )
    )
    assert_named_schema(root, "assessment_job", job)

    resolved = to_plain_data(
        validate_resolved_target(
            {
                "provider": "sharepoint",
                "target_type": "page",
                "target_id": "page-1",
                "canonical_url": "https://example.sharepoint.com/items/page-1",
                "title": "Page 1",
                "container_id": "site-x",
                "version": "1",
                "metadata": {},
            }
        )
    )
    assert_named_schema(root, "resolved_target", resolved)

    access = to_plain_data(
        validate_access_decision(
            {
                "granted": True,
                "identity_mode": "delegated",
                "reason": "granted",
                "audit_fields": {},
            }
        )
    )
    assert_named_schema(root, "access_decision", access)

    artifact = to_plain_data(
        validate_assessed_artifact_package(
            {
                "provider": "sharepoint",
                "target_id": "page-1",
                "canonical_url": "https://example.sharepoint.com/items/page-1",
                "title": "Page 1",
                "content": "Some content",
                "metadata": {},
                "owner": person,
                "last_editor": person,
                "discussion_context": [{"text": "Comment"}],
            }
        )
    )
    assert_named_schema(root, "assessed_artifact_package", artifact)

    grounding = to_plain_data(
        validate_corpus_grounding_package(
            {
                "corpus_a_results": [{"requirement_id": "REQ-1"}],
                "corpus_b_results": [{"source": "Guide-1"}],
                "precedence_policy_version": "v1",
            }
        )
    )
    assert_named_schema(root, "corpus_grounding_package", grounding)

    plan = to_plain_data(
        validate_delivery_plan(
            {
                "delivery_policy": "inline_else_email",
                "inline_target": {"target_id": "page-1"},
                "email_recipients": ["owner@example.com"],
                "fallback_to_email": True,
            }
        )
    )
    assert_named_schema(root, "delivery_plan", plan)

    outcome = to_plain_data(
        validate_delivery_outcome(
            {
                "success": True,
                "attempted_channels": ["inline"],
                "failures": [],
                "metadata": {},
            }
        )
    )
    assert_named_schema(root, "delivery_outcome", outcome)


def test_sample_structured_assessment_report_conforms_to_shared_schema() -> None:
    root = _load_shared_schemas()
    report = {
        "schema_version": "v1.1",
        "executive_summary": "Assessment completed.",
        "findings": [{"finding_id": "F-1", "status": "compliant"}],
        "citations": ["REQ-1"],
        "report_markdown": "# Report",
    }
    assert_schema_value(root, root["schemas"]["structured_assessment_report"], report)


def test_schema_validation_module_imports_without_yaml_dependency() -> None:
    import runtime.assessment_orchestration.schema_validation as schema_validation_module

    with patch.dict(sys.modules, {"yaml": None}):
        reloaded_module = importlib.reload(schema_validation_module)

    assert hasattr(reloaded_module, "load_yaml_contract")
