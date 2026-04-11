from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Mapping

import requests  # type: ignore[import-untyped]

from .assessment_runtime import create_search_backed_assessment_agent_from_env
from .interfaces import OrchestratorAdapter
from .mcp.confluence import ConfluenceMCPServer
from .models import AssessedArtifactPackage, CorpusGroundingPackage, DeliveryOutcome
from .skill_catalog import SkillCatalog, load_skill_catalog


class DefaultAssessmentAgent:
    """Minimal default assessment agent used for runtime wiring smoke flows."""

    def retrieve_corpus_grounding(
        self, artifact: AssessedArtifactPackage
    ) -> CorpusGroundingPackage:
        return CorpusGroundingPackage(corpus_a_results=[], corpus_b_results=[])

    def generate_assessment(
        self,
        artifact: AssessedArtifactPackage,
        grounding: CorpusGroundingPackage,
        *,
        validation_mode: str = "hard",
    ) -> dict[str, Any]:
        summary = f"Assessment scaffold generated for {artifact.title}"
        return {
            "schema_version": "v1.1",
            "executive_summary": summary,
            "findings": [],
            "citations": [],
            "metadata": {
                "provider": artifact.provider,
                "target_id": artifact.target_id,
                "validation_mode": validation_mode,
            },
        }

    def generate_per_control_assessment(
        self,
        artifact: AssessedArtifactPackage,
        grounding: CorpusGroundingPackage,
        *,
        progress_cb: Callable[[int, int, str, str], None] | None = None,
    ) -> dict[str, Any]:
        return self.generate_assessment(artifact, grounding)


class DefaultDeliveryPublisher:
    """Default delivery publisher placeholder for orchestrator runtime composition."""

    def post_comment(
        self,
        target_id: str,
        *,
        comment_body: str,
        identity_mode: str,
        idempotency_key: str,
    ) -> DeliveryOutcome:
        return DeliveryOutcome(success=True, attempted_channels=("inline",))

    def send_email(
        self,
        recipients: list[str],
        *,
        subject: str,
        body: str,
        idempotency_key: str,
    ) -> DeliveryOutcome:
        return DeliveryOutcome(success=True, attempted_channels=("email",))


class StdoutAuditSink:
    """Simple audit sink that logs stage transitions to stdout."""

    def record_stage(self, job, stage: str, payload: dict[str, Any]) -> None:
        print(
            {
                "event": "assessment_stage",
                "job_id": getattr(job, "job_id", ""),
                "correlation_id": getattr(job, "correlation_id", ""),
                "stage": stage,
                "payload": payload,
            }
        )


def _required(env: Mapping[str, str], key: str) -> str:
    value = (env.get(key) or "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {key}")
    return value


def _resolve_cloud_id(base_url: str, timeout_s: float = 10.0) -> str:
    resp = requests.get(f"{base_url.rstrip('/')}/_edge/tenant_info", timeout=timeout_s)
    resp.raise_for_status()
    cloud_id = str((resp.json() or {}).get("cloudId") or "").strip()
    if not cloud_id:
        raise ValueError("Could not resolve cloudId from Atlassian tenant_info endpoint")
    return cloud_id


def create_confluence_mcp_server_from_env(
    env: Mapping[str, str] | None = None,
) -> ConfluenceMCPServer:
    values = dict(os.environ) if env is None else dict(env)
    base_url = _required(values, "CONFLUENCE_BASE_URL")
    auth_mode = (values.get("CONFLUENCE_AUTH_MODE") or "basic").strip().lower()
    account_id = (values.get("CONFLUENCE_ACCOUNT_ID") or "").strip() or None

    if auth_mode == "basic":
        api_token = _required(values, "CONFLUENCE_API_TOKEN")
        auth_email = _required(values, "CONFLUENCE_AUTH_EMAIL")
        return ConfluenceMCPServer(
            base_url=base_url,
            auth_email=auth_email,
            api_token=api_token,
            auth_mode="basic",
            account_id=account_id,
        )

    if auth_mode == "bearer":
        api_token = _required(values, "CONFLUENCE_API_TOKEN")
        cloud_id = (values.get("CONFLUENCE_CLOUD_ID") or "").strip() or _resolve_cloud_id(base_url)
        return ConfluenceMCPServer(
            base_url=base_url,
            api_token=api_token,
            auth_mode="bearer",
            cloud_id=cloud_id,
            account_id=account_id,
        )

    if auth_mode == "oauth":
        oauth_access_token = (values.get("CONFLUENCE_OAUTH_ACCESS_TOKEN") or "").strip()
        oauth_client_id = (values.get("CONFLUENCE_OAUTH_CLIENT_ID") or "").strip()
        oauth_client_secret = (values.get("CONFLUENCE_OAUTH_CLIENT_SECRET") or "").strip()
        oauth_token_url = (
            values.get("CONFLUENCE_OAUTH_TOKEN_URL") or ""
        ).strip() or "https://auth.atlassian.com/oauth/token"
        oauth_scope = (values.get("CONFLUENCE_OAUTH_SCOPE") or "").strip() or None
        oauth_audience = (values.get("CONFLUENCE_OAUTH_AUDIENCE") or "").strip() or None
        if not oauth_access_token and not (oauth_client_id and oauth_client_secret):
            raise ValueError(
                "oauth mode requires CONFLUENCE_OAUTH_ACCESS_TOKEN or CONFLUENCE_OAUTH_CLIENT_ID + CONFLUENCE_OAUTH_CLIENT_SECRET"
            )
        cloud_id = (values.get("CONFLUENCE_CLOUD_ID") or "").strip() or _resolve_cloud_id(base_url)
        return ConfluenceMCPServer(
            base_url=base_url,
            oauth_access_token=oauth_access_token or None,
            oauth_client_id=oauth_client_id or None,
            oauth_client_secret=oauth_client_secret or None,
            oauth_token_url=oauth_token_url,
            oauth_scope=oauth_scope,
            oauth_audience=oauth_audience,
            auth_mode="oauth",
            cloud_id=cloud_id,
            account_id=account_id,
        )

    raise ValueError("CONFLUENCE_AUTH_MODE must be either 'basic', 'bearer', or 'oauth'")


def create_orchestrator_adapter_from_env(
    env: Mapping[str, str] | None = None,
) -> OrchestratorAdapter:
    values = dict(os.environ) if env is None else dict(env)
    content_client = create_confluence_mcp_server_from_env(env)
    try:
        assessment_agent = create_search_backed_assessment_agent_from_env(values)
    except ValueError:
        assessment_agent = DefaultAssessmentAgent()  # type: ignore[assignment]

    skills_root_raw = (values.get("ASSESSMENT_SKILLS_ROOT") or "").strip()
    if skills_root_raw:
        skills_root = Path(skills_root_raw)
    else:
        # runtime/assessment_orchestration/runtime_wiring.py -> repo root is parents[2]
        skills_root = Path(__file__).resolve().parents[2] / ".agents" / "skills"

    skill_catalog: SkillCatalog | None = None
    if skills_root.exists():
        skill_catalog = load_skill_catalog(skills_root)

    return OrchestratorAdapter(
        content_client=content_client,
        assessment_agent=assessment_agent,
        delivery_publisher=DefaultDeliveryPublisher(),
        audit_sink=StdoutAuditSink(),
        skill_catalog=skill_catalog,
    )
