
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from urllib.parse import quote
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Literal, cast


import requests  # type: ignore[import-untyped]
from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient, SearchIndexerClient
from azure.search.documents.models import VectorizedQuery
from azure.storage.blob import BlobServiceClient, ContentSettings
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from prompt_injection_guard import (
    BLOCKED_PROMPT_INJECTION_MESSAGE,
    PROMPT_INJECTION_SYSTEM_PROMPT,
    VALIDATOR_SYSTEM_PROMPT,
    assess_prompt_injection,
    evaluate_prompt_risk,
    sanitise_conversation_turn,
    sanitise_untrusted_text,
)
from pydantic import BaseModel, Field
from runtime.assessment_orchestration.state_store import CosmosPollingStateStore
from runtime.assessment_orchestration._framework_patterns import (
    infer_single_framework as _infer_framework_filter,
)

from runtime.assessment_orchestration.azure_assessment import (
    collect_azure_grounding,
    run_azure_assessment,
)

from compliance import register_compliance_endpoints
from corpus import register_corpus_endpoints
from diagnostics import register_diagnostics_endpoints
from status import register_status_endpoints
from ask import register_ask_endpoints
from home import register_home_endpoints
import controls
import llm_chat
import rag_pipeline
from controls import _CONTROLS_FRAMEWORK_FILTERS
from conversations import (
    ConversationMessage,
    ConversationSession,
    ResponseRating,
    _build_feedback_context as _conversations_build_feedback_context,
    _get_user_id as _conversations_get_user_id,
    _load_conversation as _conversations_load_conversation,
    _save_conversation as _conversations_save_conversation,
)
from constants import (
    ALLOWED_EXTENSIONS,
    COMPLIANCE_REPORT_SCHEMA_VERSION,
    MIME_TYPE_BY_EXTENSION,
    QUERY_WEB_VERSION_SIGNATURE,
)
from utils import (
    _compute_normalised_text_hash,
    _dedupe_blob_prefix,
    _extract_dedupe_hashes,
    _sanitise_blob_name_component,
    _utc_now_iso,
)
from config import (
    QueryConfig,
    PrecedencePolicy,
    load_config,
    _canonical_framework_name,
    _env_bool,
    _form_bool,
    _require_env,
    _FRAMEWORK_ALIASES,
    _CANONICAL_FRAMEWORKS,
    _parse_framework_authority_order,
    _load_precedence_policy,
)

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam

try:
    from azure.cosmos.exceptions import CosmosResourceNotFoundError as _CosmosResourceNotFoundError
except Exception:
    _CosmosResourceNotFoundError = Exception  # type: ignore[misc,assignment]

CosmosResourceNotFoundError: type[Exception] = _CosmosResourceNotFoundError


# Helper to count blobs with a given prefix (for dry_run in clear endpoints)
def _count_blob_prefix(prefix: str) -> dict[str, int]:
    if not _is_corpus_upload_enabled():
        return {"would_delete": 0}

    account_url = f"https://{config.storage_account_name}.blob.core.windows.net"
    client = BlobServiceClient(account_url=account_url, credential=credential)
    container = client.get_container_client(config.storage_container_name)
    count = 0
    try:
        blobs = container.list_blobs(name_starts_with=prefix)
        for blob in blobs:
            # Count every blob under the prefix so legacy extensionless
            # dedupe blobs are visible in dry-run and diagnostics.
            if blob.name:
                count += 1
    except Exception as exc:
        logger.warning(f"Failed to count blobs with prefix {prefix}: {exc}")
    return {"would_delete": count}

def _is_allowed_filetype(filename: str) -> bool:
    ext = Path(filename).suffix.lower()
    return ext in ALLOWED_EXTENSIONS

def _extension_matches_mime(filename: str, mime_type: str) -> bool:
    ext = Path(filename).suffix.lower()
    expected_mime = MIME_TYPE_BY_EXTENSION.get(ext)
    if not expected_mime:
        return False
    # Some browsers may send additional parameters (e.g., charset) in content_type
    return mime_type.split(";")[0].strip() == expected_mime

def _risk_label(value: str) -> str:
    normalised = str(value or "unknown").strip().replace("_", " ").lower()
    if normalised == "low":
        return "Low"
    if normalised == "medium":
        return "Medium"
    if normalised == "high":
        return "High"
    if normalised == "critical":
        return "Critical"
    return "Unknown"


logger = logging.getLogger(__name__)

_INTERNAL_ERROR_MESSAGE = "An internal error occurred."


# Conversation models and helpers moved to conversations.py module

CYBER_PERSONA_PROMPT = (
    "You are a Cyber Security Assistant. Answer questions related to cyber safety, "
    "secure-by-design controls, and operational risk using only retrieved context. "
    "Do not fabricate controls, standards, or facts not present in the context. "
    "If evidence is insufficient, state what is missing. Be concise and actionable."
)

EVALUATOR_PROMPT = (
    "You are a strict evaluator for a cyber-security RAG assistant. Evaluate if the answer is grounded and useful. "
    "Return JSON only with keys: acceptable (bool), score (0..1), reason (string). "
    "Accept only when factual claims are supported by context and response addresses the question."
)


def _prompt_injection_response(reason: str) -> dict[str, Any]:
    return {
        "answer": BLOCKED_PROMPT_INJECTION_MESSAGE,
        "results": [],
        "controls_results": [],
        "evaluation": {"acceptable": False, "score": 0.0, "reason": reason},
        "iterations": 1,
        "metrics": {
            "guardrail_blocked": 1.0,
            "rag_retrieval_s": 0.0,
            "embedding_s": 0.0,
            "search_s": 0.0,
            "llm_reply_s": 0.0,
            "evaluator_s": 0.0,
            "llm_retry_s": 0.0,
            "llm_total_s": 0.0,
            "total_s": 0.0,
        },
    }


def _json_fallback_eval() -> dict[str, Any]:
    return {"acceptable": False, "score": 0.0, "reason": "Evaluator did not return valid JSON."}


def _parse_eval(text: str) -> dict[str, Any]:
    """Extract and validate the evaluation JSON from the model response.

    Handles models that wrap JSON in markdown code fences or prefix it with prose
    by scanning for the first {...} block that contains the required keys.
    """
    candidates: list[str] = []

    # 1. Try the full response as-is (ideal case).
    candidates.append(text.strip())

    # 2. Strip ```json ... ``` or ``` ... ``` fences.
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        candidates.append(fence_match.group(1))

    # 3. Extract every {...} block in the response (handles leading/trailing prose).
    for m in re.finditer(r"\{[^{}]*\}", text, re.DOTALL):
        candidates.append(m.group(0))

    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if not isinstance(data, dict):
                continue
            if "acceptable" not in data and "score" not in data:
                continue
            acceptable = bool(data.get("acceptable", False))
            score = max(0.0, min(1.0, float(data.get("score", 0.0))))
            reason = str(data.get("reason", "No reason provided.")).strip()
            return {"acceptable": acceptable, "score": score, "reason": reason}
        except Exception:
            continue

    return _json_fallback_eval()


def _parse_validator_response(text: str) -> dict[str, Any]:
    """Extract and validate validator JSON from the model response.

    Handles raw JSON, fenced JSON, or JSON surrounded by prose, mirroring the
    evaluator parser's tolerance for common model formatting quirks.
    """
    candidates: list[str] = []

    candidates.append(text.strip())

    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        candidates.append(fence_match.group(1))

    for match in re.finditer(r"\{[^{}]*\}", text, re.DOTALL):
        candidates.append(match.group(0))

    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if not isinstance(data, dict):
                continue
            if "malicious" not in data and "confidence" not in data:
                continue
            categories = data.get("categories", [])
            if not isinstance(categories, list):
                categories = []
            return {
                "malicious": bool(data.get("malicious", False)),
                "confidence": float(max(0.0, min(1.0, data.get("confidence", 0.0)))),
                "categories": [str(category) for category in categories],
                "reason": str(data.get("reason", ""))[:200],
            }
        except Exception:
            continue

    return {}


def _unwrap_answer(text: str) -> str:
    """Extract plain answer text from responses that are mistakenly wrapped in JSON.

    Handles patterns like:
      {"answer": "..."}
      ```json\n{"answer": "..."}\n```
    Returns the original text unchanged when no known wrapping is detected.
    """
    stripped = text.strip()

    # Strip markdown code fences first.
    fence_match = re.search(r"```(?:json)?\s*(.+?)\s*```", stripped, re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1).strip()

    # Try to parse as JSON and pull an "answer" key.
    try:
        data = json.loads(stripped)
        if isinstance(data, dict) and "answer" in data:
            return str(data["answer"]).strip()
    except Exception:
        pass

    return text.strip()


def _clean_markdown_whitespace(text: str) -> str:
    """Normalise markdown spacing while preserving paragraph separation."""
    if not text:
        return ""

    # Normalise line endings and trim trailing whitespace per line.
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    normalised = "\n".join(line.rstrip() for line in normalised.split("\n"))

    # Collapse runs of 3+ blank lines down to a single paragraph break.
    normalised = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", normalised)

    return normalised.strip()


def _ensure_visible_answer(answer: str) -> str:
    """Prevent silent blank answers from reaching the UI."""
    if answer and answer.strip():
        return answer.strip()
    return (
        "## Decision\n"
        "No answer text was generated for this request, even though retrieval completed.\n\n"
        "## Next Step\n"
        "Review the retrieved controls and chunks shown below, then retry the question or narrow the scope."
    )


def _chunk_reference_label(chunk: dict[str, Any], *, fallback: str = "(unknown source)") -> str:
    """Return a reader-friendly source label, preferring original filename metadata."""
    original_filename = str(chunk.get("original_filename") or "").strip()
    if original_filename:
        return original_filename

    source_name = str(chunk.get("source_name") or "").strip()
    if source_name:
        return source_name

    source_path = str(chunk.get("source_path") or "").strip()
    if source_path:
        path_name = Path(source_path).name.strip()
        if path_name:
            return path_name
        return source_path

    source_uri = str(chunk.get("source_uri") or "").strip()
    if source_uri:
        return source_uri

    return fallback


def _build_retrieval_based_fallback_answer(
    *,
    question: str,
    controls: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    corpus_b_chunks: list[dict[str, Any]] | None = None,
    corpus_c_chunks: list[dict[str, Any]] | None = None,
) -> str:
    # Prefer explicit corpus groupings passed from retrieval flow.
    resolved_corpus_b_chunks = list(corpus_b_chunks or [])
    resolved_corpus_c_chunks = list(corpus_c_chunks or [])
    if not resolved_corpus_b_chunks and not resolved_corpus_c_chunks and chunks:
        resolved_corpus_b_chunks = [
            c
            for c in chunks
            if c.get("corpus") == "b" or c.get("corpus_role") == "narrative_guidance"
        ]
        resolved_corpus_c_chunks = [c for c in chunks if c not in resolved_corpus_b_chunks]

    frameworks = sorted({str(c.get("framework") or "").strip() for c in controls if c.get("framework")})
    framework_text = ", ".join(frameworks) if frameworks else "none"

    def _guidance_snippet(control: dict[str, Any], limit: int = 220) -> str:
        text = sanitise_untrusted_text(str(control.get("requirement_text") or "").strip())
        if not text:
            return "Requirement text unavailable in retrieved control metadata."
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."

    control_examples = [
        (
            f"- {str(c.get('requirement_id') or '(no id)')} | "
            f"{str(c.get('framework') or '(unknown framework)')}: "
            f"{_guidance_snippet(c)}"
        )
        for c in controls[:5]
    ]
    if not control_examples:
        control_examples = ["- No Corpus A controls were retrieved."]

    focus_terms = _question_focus_terms(question)

    def _normalise_excerpt_text(text: str) -> str:
        # Clean OCR/tabular artifacts so fallback output reads as narrative text.
        cleaned = sanitise_untrusted_text(text or "")
        cleaned = cleaned.replace("\t", " ").replace("\r", " ").replace("\n", " ")
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
        return cleaned.strip()

    def _sentence_candidates(text: str) -> list[str]:
        cleaned = _normalise_excerpt_text(text)
        if not cleaned:
            return []
        parts = re.split(r"(?<=[.!?])\s+|\s*;\s+", cleaned)
        return [part.strip() for part in parts if part and len(part.strip()) >= 40]

    def _sentence_score(sentence: str) -> tuple[int, int, int]:
        low = sentence.lower()
        focus_hits = sum(1 for term in focus_terms if term in low)
        signal_hits = sum(
            1
            for term in (
                "backup",
                "restore",
                "recovery",
                "availability",
                "immutable",
                "encryption",
                "retention",
                "test",
                "continuity",
                "access",
            )
            if term in low
        )
        # Penalise URL-heavy or obviously fragmentary lines.
        noise_penalty = 1 if "http" in low else 0
        return (focus_hits, signal_hits, -noise_penalty)

    def _chunk_snippet(chunk: dict[str, Any], *, limit: int = 220) -> str:
        content = _normalise_excerpt_text(str(chunk.get("content") or "").strip())
        if not content:
            return "Narrative guidance retrieved; excerpt unavailable in this chunk."
        candidates = _sentence_candidates(content)
        if focus_terms and candidates:
            ranked = sorted(candidates, key=_sentence_score, reverse=True)
            if ranked:
                best = ranked[0]
                if len(best) <= limit:
                    return best
                return best[:limit].rstrip() + "..."
        return content[:limit].rstrip() + ("..." if len(content) > limit else "")

    def _corpus_b_narrative(items: list[dict[str, Any]], *, limit: int = 3) -> list[str]:
        statements: list[tuple[tuple[int, int, int], str, str]] = []
        seen_sentences: set[str] = set()

        for item in items:
            label = _chunk_reference_label(item).strip() or "(unknown source)"
            for sentence in _sentence_candidates(str(item.get("content") or "")):
                key = sentence.lower()
                if key in seen_sentences:
                    continue
                seen_sentences.add(key)
                statements.append((_sentence_score(sentence), label, sentence))

        if not statements:
            return []

        statements.sort(key=lambda row: row[0], reverse=True)
        lines: list[str] = []
        for _, _, sentence in statements[:limit]:
            lines.append(f"- {sentence}")
        return lines

    def _unique_source_labels(
        items: list[dict[str, Any]], *, limit: int = 5, include_excerpt: bool = False
    ) -> list[str]:
        labels: list[str] = []
        seen: set[str] = set()
        for item in items:
            label = _chunk_reference_label(item).strip()
            if not label or label in seen:
                continue
            seen.add(label)
            if include_excerpt:
                labels.append(f"- {label}: {_chunk_snippet(item)}")
            else:
                labels.append(f"- {label}")
            if len(labels) >= limit:
                break
        return labels

    corpus_b_examples = _unique_source_labels(resolved_corpus_b_chunks, include_excerpt=True)
    if not corpus_b_examples:
        corpus_b_examples = ["- No Corpus B chunks were retrieved."]
    corpus_b_narrative = _corpus_b_narrative(resolved_corpus_b_chunks)

    corpus_c_examples = _unique_source_labels(resolved_corpus_c_chunks, include_excerpt=True)
    if not corpus_c_examples:
        corpus_c_examples = ["- No Corpus C chunks were retrieved."]
    corpus_c_narrative = _corpus_b_narrative(resolved_corpus_c_chunks)

    comparison_intent = _is_cross_framework_comparison_intent(question)
    comparison_note = ""
    if comparison_intent and len(frameworks) <= 1:
        comparison_note = (
            "The question appears to request cross-framework comparison, but retrieval returned "
            f"controls from only one framework ({framework_text}).\n"
        )

    return _clean_markdown_whitespace(
        "\n".join(
            [
                "## Decision",
                "A full model narrative could not be generated for this request; returning a retrieval-grounded summary instead.",
                comparison_note,
                "## Corpus A Basis (Normative Requirements)",
                f"Retrieved frameworks: {framework_text}.",
                *control_examples,
                "",
                "## Corpus B Basis (Narrative Guidance)",
                "Corpus B guidance below is synthesised directly from retrieved text snippets (fallback mode; no additional model completion was available).",
                *corpus_b_narrative,
                "",
                "Retrieved excerpts:",
                *corpus_b_examples,
                "",
                "## Corpus C Basis (Assessed Artifacts/Evidence)",
                "Corpus C evidence below is synthesised directly from retrieved artifact text snippets (fallback mode; no additional model completion was available).",
                *corpus_c_narrative,
                "",
                "Retrieved excerpts:",
                *corpus_c_examples,
                "",
                "## Discrepancies and Precedence Resolution",
                "Potential contradictions cannot be fully resolved in this fallback mode; apply configured framework precedence to conflicting controls.",
                "",
                "## Gaps and Recommended Actions",
                "Retrieve additional controls across the target frameworks and retry the question for a complete comparative answer.",
                "",
                "## Confidence and Citations",
                "Confidence: Low (fallback response generated from retrieval metadata due empty model output).",
            ]
        )
    )


app = FastAPI(title="RAG Query Console")
_APP_DIR = Path(__file__).resolve().parent
_STATIC_VERSION = str(
    max(
        int((_APP_DIR / "templates" / "index.html").stat().st_mtime),
        int((_APP_DIR / "static" / "index.css").stat().st_mtime),
        int((_APP_DIR / "static" / "index.js").stat().st_mtime),
    )
)
templates = Jinja2Templates(directory=str(_APP_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(_APP_DIR / "static")), name="static")
credential = DefaultAzureCredential()
config = load_config()

# Mount branding directory: prefer BRANDING_STATIC_PATH override, fall back to bundled assets.
_bundled_branding_dir = _APP_DIR / "static" / "branding"
_branding_dir: Path = (
    Path(config.branding_static_path)
    if config.branding_static_path and Path(config.branding_static_path).is_dir()
    else _bundled_branding_dir
)
app.mount(
    "/static/branding",
    StaticFiles(directory=str(_branding_dir)),
    name="branding",
)


def _branding_ctx() -> dict[str, Any]:
    """Return template context variables shared by every page response."""
    return {
        "app_title": config.app_title,
        "static_version": _STATIC_VERSION,
    }


precedence_policy = _load_precedence_policy(
    config.precedence_policy_path,
    config.controls_framework_authority_order,
)
search_client = SearchClient(
    endpoint=config.search_endpoint,
    index_name=config.search_index_name,
    credential=credential,
)

controls_search_client = SearchClient(
    endpoint=config.search_endpoint,
    index_name=config.controls_index_name,
    credential=credential,
)

# Initialise CosmosDB client
cosmos_db = None
try:
    from azure.cosmos import CosmosClient

    cosmos_client = CosmosClient(url=config.cosmos_endpoint, credential=credential)
    cosmos_db = cosmos_client.get_database_client(config.cosmos_database_name)
    conversations_container = cosmos_db.get_container_client(config.cosmos_container_name)
except (ImportError, Exception) as exc:
    # If CosmosDB is unavailable, continue with in-memory conversation tracking
    cosmos_client = None  # type: ignore[assignment]
    conversations_container = None  # type: ignore[assignment]
    import logging

    logging.warning(f"CosmosDB unavailable: {exc}. Conversations will not be persisted.")

orchestration_state_container = None
confluence_poll_state_store = None
if cosmos_client is not None and cosmos_db is not None:
    try:
        orchestration_state_container = cosmos_db.get_container_client(
            config.cosmos_orchestration_container_name
        )
        confluence_poll_state_store = CosmosPollingStateStore(orchestration_state_container)
    except Exception as exc:
        logger.warning(
            "Confluence orchestration state unavailable from Cosmos container %s: %s",
            config.cosmos_orchestration_container_name,
            exc,
        )


class AskRequest(BaseModel):
    question: str
    retrieve_k: int = Field(default=5, ge=1, le=20)
    temperature: float = Field(default=1.0, ge=0.0, le=1.0)
    auth_token: str = ""
    controls_semantic: bool | None = None
    controls_framework: str | None = None
    controls_comparison_mode: str = "auto-detect"
    evidence_corpora_include: list[str] | None = None
    evidence_corpora_exclude: list[str] | None = None
    advanced_mode: bool = False


class CorpusAIngestRequest(BaseModel):
    frameworks: list[str] | None = None
    replace_existing: bool = False
    dry_run: bool = False
    no_guidance: bool = False
    auth_token: str = ""


class ComplianceReportRequest(BaseModel):
    question: str = ""
    retrieve_k: int = Field(default=5, ge=1, le=20)
    controls_top_k: int = Field(default=4, ge=1, le=2000)
    temperature: float = Field(default=1.0, ge=0.0, le=1.0)
    controls_framework: str | None = None
    controls_comparison_mode: str = "auto-detect"
    corpus_b_upload_batch: str | None = None
    corpus_c_upload_batch: str | None = None
    evidence_corpora_include: list[str] | None = None
    evidence_corpora_exclude: list[str] | None = None
    assessment_strategy: Literal["single_pass", "per_control"] = "single_pass"
    validation_mode: Literal["hard", "soft"] = "hard"
    auth_token: str = ""


class AzureComplianceReportRequest(BaseModel):
    subscription_id: str
    resource_group: str
    resource_ids: list[str] = Field(default_factory=list)
    controls_framework: str = "NIST CSF"
    controls_top_k: int = Field(default=4, ge=1, le=2000)
    temperature: float = Field(default=1.0, ge=0.0, le=1.0)
    assessment_strategy: Literal["single_pass", "per_control"] = "single_pass"
    validation_mode: Literal["hard", "soft"] = "hard"
    auth_token: str = ""


@dataclass
class _ReportJob:
    job_id: str
    kind: Literal["compliance", "azure"]
    created_at: str
    updated_at: str
    state: Literal["queued", "running", "completed", "failed"] = "queued"
    message: str = "Queued"
    total_controls: int = 0
    completed_controls: int = 0
    current_requirement_id: str = ""
    result: dict[str, Any] | None = None
    error: str = ""


_REPORT_JOBS: dict[str, _ReportJob] = {}
_REPORT_JOBS_LOCK = threading.Lock()


def _new_report_job(kind: Literal["compliance", "azure"]) -> _ReportJob:
    now = _utc_now_iso()
    job = _ReportJob(
        job_id=str(uuid.uuid4()),
        kind=kind,
        created_at=now,
        updated_at=now,
    )
    with _REPORT_JOBS_LOCK:
        _REPORT_JOBS[job.job_id] = job
    return job


def _get_report_job(job_id: str) -> _ReportJob | None:
    with _REPORT_JOBS_LOCK:
        return _REPORT_JOBS.get(job_id)


def _update_report_job(job_id: str, **updates: Any) -> None:
    with _REPORT_JOBS_LOCK:
        job = _REPORT_JOBS.get(job_id)
        if not job:
            return
        for key, value in updates.items():
            setattr(job, key, value)
        job.updated_at = _utc_now_iso()


class CorpusClearRequest(BaseModel):
    clear_blobs: bool = False
    dry_run: bool = False
    auth_token: str = ""


class CorpusAClearRequest(BaseModel):
    frameworks: list[str] | None = None
    dry_run: bool = False
    auth_token: str = ""


class ComplianceFinding(BaseModel):
    finding_id: str = Field(min_length=1, max_length=64)
    requirement_id: str = Field(min_length=1, max_length=128)
    framework: str = Field(min_length=1, max_length=64)
    status: Literal[
        "compliant",
        "partially_compliant",
        "non_compliant",
        "not_applicable",
        "insufficient_evidence",
    ]
    severity: Literal["low", "medium", "high", "critical"]
    rationale: str = Field(min_length=1, max_length=3000)
    evidence_sources: list[str] = Field(default_factory=list, min_length=1, max_length=20)
    gaps: list[str] = Field(default_factory=list, max_length=20)
    recommendations: list[str] = Field(default_factory=list, max_length=20)


class ComplianceReportStructured(BaseModel):
    schema_version: str = Field(min_length=1, max_length=32)
    executive_summary: str = Field(min_length=1, max_length=3000)
    scope_and_inputs: list[str] = Field(default_factory=list, min_length=1, max_length=40)
    controls_assessed: list[str] = Field(default_factory=list, min_length=1, max_length=200)
    guidance_applied: list[str] = Field(default_factory=list, max_length=80)
    findings: list[ComplianceFinding] = Field(default_factory=list, min_length=1, max_length=300)
    overall_risk_rating: Literal["low", "medium", "high", "critical"]
    missing_evidence: list[str] = Field(default_factory=list, max_length=80)
    recommended_actions: list[str] = Field(default_factory=list, min_length=1, max_length=80)
    citations: list[str] = Field(default_factory=list, min_length=1, max_length=200)


COMPLIANCE_REPORT_PROMPT = (
    "You are a compliance assessment assistant. Build a strict JSON compliance report "
    "using Corpus A and Corpus B as grounding data, and Corpus C as assessed artifacts. "
    "Do not invent requirements or evidence. If evidence is missing, state it explicitly. "
    "Return JSON only. No markdown, no prose outside JSON, and no code fences."
)

COMPLIANCE_REPORT_JSON_SCHEMA_HINT = (
    "Required JSON shape:\n"
    "{\n"
    "  \"schema_version\": string (must be 'v1.1'),\n"
    '  "executive_summary": string,\n'
    '  "scope_and_inputs": string[],\n'
    '  "controls_assessed": string[],\n'
    '  "guidance_applied": string[],\n'
    '  "findings": [\n'
    "    {\n"
    '      "finding_id": string,\n'
    '      "requirement_id": string,\n'
    '      "framework": string,\n'
    '      "status": "compliant"|"partially_compliant"|"non_compliant"|"not_applicable"|"insufficient_evidence",\n'
    '      "severity": "low"|"medium"|"high"|"critical",\n'
    '      "rationale": string,\n'
    '      "evidence_sources": string[],\n'
    '      "gaps": string[],\n'
    '      "recommendations": string[]\n'
    "    }\n"
    "  ],\n"
    '  "overall_risk_rating": "low"|"medium"|"high"|"critical",\n'
    '  "missing_evidence": string[],\n'
    '  "recommended_actions": string[],\n'
    '  "citations": string[]\n'
    "}"
)


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = _unwrap_answer(text).strip()
    if not cleaned:
        raise ValueError("Model returned empty response")

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first == -1 or last == -1 or last <= first:
        raise ValueError("Model response did not contain a JSON object")

    parsed = json.loads(cleaned[first : last + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Model JSON payload is not an object")
    return parsed


def _validate_compliance_report_payload(payload: dict[str, Any]) -> ComplianceReportStructured:
    report = ComplianceReportStructured.model_validate(payload)
    if report.schema_version != COMPLIANCE_REPORT_SCHEMA_VERSION:
        raise ValueError(
            f"schema_version must be {COMPLIANCE_REPORT_SCHEMA_VERSION}, got {report.schema_version}"
        )
    return report


def _clean_non_empty_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for raw in value:
        text = str(raw or "").strip()
        if text:
            items.append(text)
    return items


def _normalise_compliance_report_payload(
    payload: dict[str, Any],
    *,
    question: str,
    controls: list[dict[str, Any]],
    corpus_b_chunks: list[dict[str, Any]],
    corpus_c_chunks: list[dict[str, Any]],
    corpus_b_indexed_total: int = 0,
    corpus_c_indexed_total: int = 0,
    corpus_b_upload_batch: str | None = None,
    corpus_c_upload_batch: str | None = None,
    corpus_b_filtered_total: int | None = None,
    corpus_c_filtered_total: int | None = None,
    assessment_strategy: str | None = None,
) -> dict[str, Any]:
    report = dict(payload or {})

    controls_count = len(controls)
    corpus_b_count = len(corpus_b_chunks)
    corpus_c_count = len(corpus_c_chunks)

    control_ids = [
        str(item.get("requirement_id") or "").strip()
        for item in controls
        if str(item.get("requirement_id") or "").strip()
    ]
    control_frameworks = [
        str(item.get("framework") or "").strip()
        for item in controls
        if str(item.get("framework") or "").strip()
    ]
    source_names = [
        _chunk_reference_label(item, fallback="")
        for item in [*corpus_c_chunks, *corpus_b_chunks]
        if _chunk_reference_label(item, fallback="")
    ]

    default_requirement_id = control_ids[0] if control_ids else "UNMAPPED"
    default_framework = control_frameworks[0] if control_frameworks else "Unknown"
    default_sources = source_names or ["No direct evidence source available"]

    report["schema_version"] = str(
        report.get("schema_version") or COMPLIANCE_REPORT_SCHEMA_VERSION
    ).strip()

    executive_summary = str(report.get("executive_summary") or "").strip()
    if not executive_summary:
        executive_summary = (
            "Automated compliance assessment generated with available grounded evidence. "
            "Some required fields were normalised due to incomplete model output."
        )
    if (
        controls_count > 0 or corpus_c_count > 0
    ) and "no normative requirements" in executive_summary.lower():
        executive_summary = (
            "Automated compliance assessment generated from retrieved corpus evidence. "
            "Some model statements were corrected to match retrieved control and artifact counts."
        )
    report["executive_summary"] = executive_summary

    # Keep scope summary aligned with actual retrieval to avoid contradictory model prose.
    scope_and_inputs = _build_compliance_scope_inputs(
        controls_count=controls_count,
        corpus_b_chunk_count=corpus_b_count,
        corpus_c_chunk_count=corpus_c_count,
        corpus_b_indexed_total=corpus_b_indexed_total,
        corpus_c_indexed_total=corpus_c_indexed_total,
        corpus_b_upload_batch=corpus_b_upload_batch,
        corpus_c_upload_batch=corpus_c_upload_batch,
        corpus_b_filtered_total=corpus_b_filtered_total,
        corpus_c_filtered_total=corpus_c_filtered_total,
        assessment_strategy=assessment_strategy,
    )
    report["scope_and_inputs"] = scope_and_inputs

    controls_assessed = _clean_non_empty_string_list(report.get("controls_assessed"))
    if control_ids:
        controls_assessed = control_ids
    elif not controls_assessed:
        controls_assessed = ["UNMAPPED"]
    report["controls_assessed"] = controls_assessed

    report["guidance_applied"] = _clean_non_empty_string_list(report.get("guidance_applied"))

    findings_raw = report.get("findings")
    findings: list[dict[str, Any]] = []
    if isinstance(findings_raw, list):
        findings = [item for item in findings_raw if isinstance(item, dict)]

    if not findings:
        findings = [
            {
                "finding_id": "finding-1",
                "requirement_id": default_requirement_id,
                "framework": default_framework,
                "status": "insufficient_evidence",
                "severity": "medium",
                "rationale": "Insufficient grounded evidence was available to produce a complete structured finding.",
                "evidence_sources": default_sources,
                "gaps": ["Insufficient structured evidence"],
                "recommendations": ["Collect additional evidence and re-run assessment."],
            }
        ]

    normalised_findings: list[dict[str, Any]] = []
    valid_status = {
        "compliant",
        "partially_compliant",
        "non_compliant",
        "not_applicable",
        "insufficient_evidence",
    }
    valid_severity = {"low", "medium", "high", "critical"}
    for idx, finding in enumerate(findings):
        finding_id = str(finding.get("finding_id") or "").strip() or f"finding-{idx + 1}"
        requirement_id = str(finding.get("requirement_id") or "").strip() or default_requirement_id
        framework = str(finding.get("framework") or "").strip() or default_framework
        status = str(finding.get("status") or "").strip().lower()
        severity = str(finding.get("severity") or "").strip().lower()
        rationale = str(finding.get("rationale") or "").strip()
        if not rationale:
            rationale = "Model output omitted rationale; marked as insufficient evidence."
            status = "insufficient_evidence"
        if status not in valid_status:
            status = "insufficient_evidence"
        if severity not in valid_severity:
            severity = "medium"

        evidence_sources = _clean_non_empty_string_list(finding.get("evidence_sources"))
        if not evidence_sources:
            evidence_sources = default_sources

        gaps = _clean_non_empty_string_list(finding.get("gaps"))
        recommendations = _clean_non_empty_string_list(finding.get("recommendations"))

        normalised_findings.append(
            {
                "finding_id": finding_id,
                "requirement_id": requirement_id,
                "framework": framework,
                "status": status,
                "severity": severity,
                "rationale": rationale,
                "evidence_sources": evidence_sources,
                "gaps": gaps,
                "recommendations": recommendations,
            }
        )
    report["findings"] = normalised_findings

    risk = str(report.get("overall_risk_rating") or "").strip().lower()
    if risk not in {"low", "medium", "high", "critical"}:
        risk = "medium"
    report["overall_risk_rating"] = risk

    missing_evidence = _clean_non_empty_string_list(report.get("missing_evidence"))
    if not missing_evidence and (not controls or not source_names):
        missing_evidence = [
            "Insufficient grounded evidence for full control mapping and source attribution.",
        ]
    report["missing_evidence"] = missing_evidence

    recommended_actions = _clean_non_empty_string_list(report.get("recommended_actions"))
    if not recommended_actions:
        recommended_actions = [
            "Collect additional evidence and re-run compliance assessment.",
        ]
    report["recommended_actions"] = recommended_actions

    citations = _clean_non_empty_string_list(report.get("citations"))
    if not citations:
        citations = default_sources
    report["citations"] = citations

    return report


def _report_findings_to_csv(report: ComplianceReportStructured) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "finding_id",
            "requirement_id",
            "framework",
            "status",
            "severity",
            "rationale",
            "evidence_sources",
            "gaps",
            "recommendations",
        ]
    )
    for finding in report.findings:
        writer.writerow(
            [
                finding.finding_id,
                finding.requirement_id,
                finding.framework,
                finding.status,
                finding.severity,
                finding.rationale,
                " | ".join(finding.evidence_sources),
                " | ".join(finding.gaps),
                " | ".join(finding.recommendations),
            ]
        )
    return buffer.getvalue()


def _build_fallback_compliance_report_payload(
    *,
    question: str,
    controls: list[dict[str, Any]],
    corpus_b_chunks: list[dict[str, Any]],
    corpus_c_chunks: list[dict[str, Any]],
    validation_error: str,
    corpus_b_indexed_total: int = 0,
    corpus_c_indexed_total: int = 0,
    corpus_b_upload_batch: str | None = None,
    corpus_c_upload_batch: str | None = None,
    corpus_b_filtered_total: int | None = None,
    corpus_c_filtered_total: int | None = None,
    assessment_strategy: str | None = None,
) -> dict[str, Any]:
    control_ids = [
        str(item.get("requirement_id") or "").strip()
        for item in controls
        if str(item.get("requirement_id") or "").strip()
    ]
    framework_names = [
        str(item.get("framework") or "").strip()
        for item in controls
        if str(item.get("framework") or "").strip()
    ]
    evidence_sources = [
        _chunk_reference_label(item, fallback="")
        for item in [*corpus_c_chunks, *corpus_b_chunks]
        if _chunk_reference_label(item, fallback="")
    ]

    return {
        "schema_version": COMPLIANCE_REPORT_SCHEMA_VERSION,
        "executive_summary": (
            "Fallback compliance report generated because the model returned an unusable or invalid response. "
            "Retrieved evidence counts are preserved below."
        ),
        "scope_and_inputs": _build_compliance_scope_inputs(
            question=question,
            controls_count=len(controls),
            corpus_b_chunk_count=len(corpus_b_chunks),
            corpus_c_chunk_count=len(corpus_c_chunks),
            corpus_b_indexed_total=corpus_b_indexed_total,
            corpus_c_indexed_total=corpus_c_indexed_total,
            corpus_b_upload_batch=corpus_b_upload_batch,
            corpus_c_upload_batch=corpus_c_upload_batch,
            corpus_b_filtered_total=corpus_b_filtered_total,
            corpus_c_filtered_total=corpus_c_filtered_total,
            assessment_strategy=assessment_strategy,
        ),
        "controls_assessed": control_ids or ["UNMAPPED"],
        "guidance_applied": evidence_sources[:10],
        "findings": [
            {
                "finding_id": "fallback-1",
                "requirement_id": control_ids[0] if control_ids else "UNMAPPED",
                "framework": framework_names[0] if framework_names else "Unknown",
                "status": "insufficient_evidence",
                "severity": "medium",
                "rationale": (
                    "The model response could not be validated; this fallback finding preserves the retrieved evidence "
                    f"state instead. Validation error: {validation_error}"
                )[:3000],
                "evidence_sources": evidence_sources or ["No evidence sources retrieved"],
                "gaps": ["Model output failed schema validation"],
                "recommendations": ["Review retrieved sources and re-run the report."],
            }
        ],
        "overall_risk_rating": "medium",
        "missing_evidence": [
            "Additional corroborating evidence may be required for a final compliance conclusion."
        ],
        "recommended_actions": [
            "Retry the report or review source grounding manually.",
        ],
        "citations": evidence_sources or ["No evidence sources retrieved"],
    }


def _control_terms(control: dict[str, Any]) -> set[str]:
    text = " ".join(
        [
            str(control.get("requirement_id") or ""),
            str(control.get("control_family") or ""),
            str(control.get("requirement_text") or ""),
            str(control.get("guidance_text") or ""),
        ]
    ).lower()
    return {token for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text) if len(token) >= 4}


def _select_chunks_for_control(
    control: dict[str, Any],
    chunks: list[dict[str, Any]],
    *,
    max_chunks: int,
) -> list[dict[str, Any]]:
    if not chunks:
        return []

    terms = _control_terms(control)
    scored: list[tuple[int, float, dict[str, Any]]] = []
    for chunk in chunks:
        content = str(chunk.get("content") or "")
        lower_content = content.lower()
        overlap = sum(1 for term in terms if term in lower_content)
        score = float(chunk.get("score") or 0.0)
        scored.append((overlap, score, chunk))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in scored[:max_chunks]]


def _assess_control_finding_with_llm(
    *,
    question: str,
    control: dict[str, Any],
    corpus_b_chunks: list[dict[str, Any]],
    corpus_c_chunks: list[dict[str, Any]],
    temperature: float,
) -> dict[str, Any]:
    requirement_id = str(control.get("requirement_id") or "").strip() or "UNMAPPED"
    framework = str(control.get("framework") or "").strip() or "Unknown"

    b_context = "\n\n".join(
        f"Source: {_chunk_reference_label(c, fallback='guidance')}\nExcerpt: {sanitise_untrusted_text(str(c.get('content') or '')[:900])}"
        for c in corpus_b_chunks
    )
    c_context = "\n\n".join(
        f"Source: {_chunk_reference_label(c, fallback='artifact')}\nExcerpt: {sanitise_untrusted_text(str(c.get('content') or '')[:1200])}"
        for c in corpus_c_chunks
    )

    messages = [
        {"role": "system", "content": PROMPT_INJECTION_SYSTEM_PROMPT},
        {
            "role": "system",
            "content": (
                "Assess one compliance control and return exactly one JSON finding object with fields: "
                "finding_id, requirement_id, framework, status, severity, rationale, evidence_sources, gaps, recommendations."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Assessment question:\n{sanitise_untrusted_text(question)}\n\n"
                "Control under assessment:\n"
                f"Requirement ID: {requirement_id}\n"
                f"Framework: {framework}\n"
                f"Control Family: {control.get('control_family', '')}\n"
                f"Requirement: {sanitise_untrusted_text(str(control.get('requirement_text') or '')[:1600])}\n"
                f"Guidance: {sanitise_untrusted_text(str(control.get('guidance_text') or '')[:1000])}\n\n"
                f"Corpus B guidance (optional):\n{b_context or 'No relevant Corpus B guidance.'}\n\n"
                f"Corpus C evidence:\n{c_context or 'No relevant Corpus C evidence.'}\n\n"
                "Constraints:\n"
                "- status must be one of compliant|partially_compliant|non_compliant|not_applicable|insufficient_evidence\n"
                "- severity must be one of low|medium|high|critical\n"
                "- include at least one evidence source when possible\n"
                "- return JSON object only"
            ),
        },
    ]

    try:
        raw = _chat_completion_with_empty_retry(
            messages,
            deployment=config.query_deployment,
            temperature=temperature,
        )
        parsed = _extract_json_object(raw)
    except Exception:
        parsed = {}

    fallback = {
        "finding_id": f"finding-{requirement_id}",
        "requirement_id": requirement_id,
        "framework": framework,
        "status": "insufficient_evidence",
        "severity": "medium",
        "rationale": "Insufficient evidence for deterministic assessment in per-control mode.",
        "evidence_sources": [
            _chunk_reference_label(item, fallback="evidence")
            for item in (corpus_c_chunks or corpus_b_chunks)[:3]
        ]
        or ["No evidence sources retrieved"],
        "gaps": ["Additional artifact evidence needed for this control."],
        "recommendations": ["Provide corroborating evidence and reassess this control."],
    }
    fallback.update(parsed)

    fallback["evidence_sources"] = _clean_non_empty_string_list(
        fallback.get("evidence_sources")
    ) or ["No evidence sources retrieved"]
    fallback["gaps"] = _clean_non_empty_string_list(fallback.get("gaps"))
    fallback["recommendations"] = _clean_non_empty_string_list(fallback.get("recommendations"))
    return fallback


def _build_per_control_report_payload(
    *,
    question: str,
    controls: list[dict[str, Any]],
    corpus_b_chunks: list[dict[str, Any]],
    corpus_c_chunks: list[dict[str, Any]],
    temperature: float,
    progress_cb: Callable[[int, int, str, str], None] | None = None,
    corpus_b_indexed_total: int = 0,
    corpus_c_indexed_total: int = 0,
    corpus_b_upload_batch: str | None = None,
    corpus_c_upload_batch: str | None = None,
    corpus_b_filtered_total: int | None = None,
    corpus_c_filtered_total: int | None = None,
    corpus_c_scope_label: str = "Corpus C artifacts retrieved",
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    total = len(controls)

    for index, control in enumerate(controls, start=1):
        requirement_id = str(control.get("requirement_id") or "").strip() or f"CTRL-{index}"
        if progress_cb:
            progress_cb(index - 1, total, requirement_id, f"Assessing control {index}/{total}")

        relevant_b = _select_chunks_for_control(control, corpus_b_chunks, max_chunks=2)
        relevant_c = _select_chunks_for_control(control, corpus_c_chunks, max_chunks=3)
        finding = _assess_control_finding_with_llm(
            question=question,
            control=control,
            corpus_b_chunks=relevant_b,
            corpus_c_chunks=relevant_c,
            temperature=temperature,
        )
        findings.append(finding)
        if progress_cb:
            progress_cb(index, total, requirement_id, f"Completed control {index}/{total}")

    scope_inputs = _build_compliance_scope_inputs(
        question=question,
        controls_count=len(controls),
        corpus_b_chunk_count=len(corpus_b_chunks),
        corpus_c_chunk_count=len(corpus_c_chunks),
        corpus_b_indexed_total=corpus_b_indexed_total,
        corpus_c_indexed_total=corpus_c_indexed_total,
        corpus_b_upload_batch=corpus_b_upload_batch,
        corpus_c_upload_batch=corpus_c_upload_batch,
        corpus_b_filtered_total=corpus_b_filtered_total,
        corpus_c_filtered_total=corpus_c_filtered_total,
        corpus_c_scope_label=corpus_c_scope_label,
        assessment_strategy="per_control",
    )
    control_ids = [
        str(c.get("requirement_id") or "").strip()
        for c in controls
        if str(c.get("requirement_id") or "").strip()
    ]
    source_names = [
        _chunk_reference_label(item, fallback="")
        for item in [*corpus_b_chunks, *corpus_c_chunks]
        if _chunk_reference_label(item, fallback="")
    ]

    statuses = [str(item.get("status") or "").strip().lower() for item in findings]
    if any(s in {"non_compliant", "critical"} for s in statuses):
        risk = "high"
    elif any(s in {"partially_compliant", "insufficient_evidence"} for s in statuses):
        risk = "medium"
    else:
        risk = "low"

    missing_evidence = [
        f"Control {item.get('requirement_id', '')}: additional corroborating evidence required"
        for item in findings
        if str(item.get("status") or "").strip().lower()
        in {"insufficient_evidence", "partially_compliant"}
    ][:40]

    return {
        "schema_version": COMPLIANCE_REPORT_SCHEMA_VERSION,
        "executive_summary": (
            "Per-control compliance assessment completed. Findings are generated sequentially per retrieved control "
            "to improve coverage breadth over single-pass context windows."
        ),
        "scope_and_inputs": scope_inputs,
        "controls_assessed": control_ids or ["UNMAPPED"],
        "guidance_applied": source_names[:20],
        "findings": findings,
        "overall_risk_rating": risk,
        "missing_evidence": missing_evidence,
        "recommended_actions": [
            "Address non-compliant and partially compliant controls in priority order.",
            "Collect missing evidence for controls marked insufficient_evidence.",
        ],
        "citations": source_names[:40] or ["No evidence sources retrieved"],
    }


def _generate_compliance_report_result(
    payload: ComplianceReportRequest,
    *,
    progress_cb: Callable[[int, int, str, str], None] | None = None,
) -> dict[str, Any]:
    question = payload.question.strip()
    effective_question = question
    if not effective_question:
        framework_hint = _canonical_framework_name(payload.controls_framework) or "selected"
        effective_question = (
            "Perform a general compliance assessment of Corpus C artifacts against "
            f"Corpus A {framework_hint} controls and Corpus B guidance."
        )

    controls, controls_timings = _controls_search(
        effective_question,
        retrieve_k=payload.controls_top_k,
        use_semantic=config.controls_semantic_default,
        framework_filter_override=_normalise_framework_filter(payload.controls_framework),
        comparison_mode=_normalise_controls_comparison_mode(payload.controls_comparison_mode),
    )

    # Compliance reports always assess Corpus C artifacts using Corpus A controls
    # and Corpus B guidance; Corpus C is the assessment target, not an optional
    # grounding corpus selector.
    selected_evidence_corpora = ["b"]
    evidence_corpus_filter_expr = _build_evidence_corpus_filter(selected_evidence_corpora)
    include_corpus_b = True
    include_corpus_c = True

    corpus_b_filter = "corpus eq 'b'"
    corpus_b_filtered_total: int | None = None
    if include_corpus_b:
        corpus_b_filter_expr = corpus_b_filter
        corpus_b_indexed_total = _count_search_documents_total_by_filter(
            search_client, filter_expr=corpus_b_filter
        )
        if payload.corpus_b_upload_batch:
            escaped_batch = payload.corpus_b_upload_batch.replace("'", "''")
            corpus_b_filter = f"{corpus_b_filter} and upload_batch eq '{escaped_batch}'"
            corpus_b_filter_expr = corpus_b_filter
            corpus_b_filtered_total = _count_search_documents_total_by_filter(
                search_client, filter_expr=corpus_b_filter
            )
        corpus_b_chunks, b_timings = _hybrid_search(
            effective_question,
            retrieve_k=payload.retrieve_k,
            evidence_filter=corpus_b_filter,
        )
    else:
        corpus_b_indexed_total = 0
        corpus_b_chunks = []
        b_timings = {"search_s": 0.0}
        corpus_b_filter_expr = None

    corpus_c_filter = "corpus eq 'c'"
    corpus_c_filtered_total: int | None = None
    if include_corpus_c:
        corpus_c_filter_expr = corpus_c_filter
        corpus_c_indexed_total = _count_search_documents_total_by_filter(
            search_client, filter_expr=corpus_c_filter
        )
        if payload.corpus_c_upload_batch:
            escaped_batch = payload.corpus_c_upload_batch.replace("'", "''")
            corpus_c_filter = f"{corpus_c_filter} and upload_batch eq '{escaped_batch}'"
            corpus_c_filter_expr = corpus_c_filter
            corpus_c_filtered_total = _count_search_documents_total_by_filter(
                search_client, filter_expr=corpus_c_filter
            )
        corpus_c_chunks, c_timings = _hybrid_search(
            effective_question,
            retrieve_k=payload.retrieve_k,
            evidence_filter=corpus_c_filter,
        )
    else:
        corpus_c_indexed_total = 0
        corpus_c_chunks = []
        c_timings = {"search_s": 0.0}
        corpus_c_filter_expr = None

    strategy = payload.assessment_strategy
    used_fallback_payload = False
    if strategy == "per_control" and controls:
        report_payload = _build_per_control_report_payload(
            question=effective_question,
            controls=controls,
            corpus_b_chunks=corpus_b_chunks,
            corpus_c_chunks=corpus_c_chunks,
            temperature=payload.temperature,
            progress_cb=progress_cb,
            corpus_b_indexed_total=corpus_b_indexed_total,
            corpus_c_indexed_total=corpus_c_indexed_total,
            corpus_b_upload_batch=payload.corpus_b_upload_batch,
            corpus_c_upload_batch=payload.corpus_c_upload_batch,
            corpus_b_filtered_total=corpus_b_filtered_total,
            corpus_c_filtered_total=corpus_c_filtered_total,
        )
    else:
        controls_context = "\n\n".join(
            (
                f"Requirement ID: {c['requirement_id']}\n"
                f"Framework: {c['framework']} {c['framework_version']}\n"
                f"Control Family: {c['control_family']}\n"
                f"Requirement: {sanitise_untrusted_text(c['requirement_text'][:1200])}\n"
                f"Guidance: {sanitise_untrusted_text(c['guidance_text'][:800]) or 'No supplementary guidance is available for this control; assess solely against the requirement text above.'}"
            )
            for c in controls
        )

        corpus_b_context = "\n\n".join(
            (
                f"Source: {_chunk_reference_label(c)}\n"
                f"Excerpt: {sanitise_untrusted_text(c['content'][:1500])}"
            )
            for c in corpus_b_chunks
        )

        corpus_c_context = "\n\n".join(
            (
                f"Source: {_chunk_reference_label(c)}\n"
                f"Excerpt: {sanitise_untrusted_text(c['content'][:1500])}"
            )
            for c in corpus_c_chunks
        )

        messages = [
            {"role": "system", "content": COMPLIANCE_REPORT_PROMPT},
            {"role": "system", "content": PROMPT_INJECTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Assessment question:\n{sanitise_untrusted_text(effective_question)}\n\n"
                    "Use the following corpora:\n"
                    f"Corpus A (normative requirements):\n{controls_context or 'No Corpus A controls retrieved.'}\n\n"
                    f"Corpus B (narrative guidance):\n{corpus_b_context or 'No Corpus B guidance retrieved.'}\n\n"
                    f"Corpus C (assessed artifacts):\n{corpus_c_context or 'No Corpus C artifacts retrieved.'}\n\n"
                    "Generate only JSON that matches this exact schema and constraints:\n"
                    f"{COMPLIANCE_REPORT_JSON_SCHEMA_HINT}\n\n"
                    "Rules:\n"
                    f"- Set schema_version to exactly {COMPLIANCE_REPORT_SCHEMA_VERSION}.\n"
                    "- Use requirement IDs from Corpus A in findings.requirement_id.\n"
                    "- Include evidence source names from Corpus B/C in findings.evidence_sources.\n"
                    "- If evidence is missing, use status=insufficient_evidence and document it in missing_evidence.\n"
                    "- Provide at least one finding and at least one citation.\n"
                    "- Return raw JSON object only."
                ),
            },
        ]

        model_response = _chat_completion_with_empty_retry(
            messages,
            deployment=config.query_deployment,
            temperature=payload.temperature,
        )
        try:
            report_payload = _extract_json_object(model_response)
            report_payload = _normalise_compliance_report_payload(
                report_payload,
                question=effective_question,
                controls=controls,
                corpus_b_chunks=corpus_b_chunks,
                corpus_c_chunks=corpus_c_chunks,
                corpus_b_indexed_total=corpus_b_indexed_total,
                corpus_c_indexed_total=corpus_c_indexed_total,
                corpus_b_upload_batch=payload.corpus_b_upload_batch,
                corpus_c_upload_batch=payload.corpus_c_upload_batch,
                corpus_b_filtered_total=corpus_b_filtered_total,
                corpus_c_filtered_total=corpus_c_filtered_total,
                assessment_strategy=strategy,
            )
        except Exception as exc:
            if payload.validation_mode == "hard":
                raise
            used_fallback_payload = True
            report_payload = _build_fallback_compliance_report_payload(
                question=effective_question,
                controls=controls,
                corpus_b_chunks=corpus_b_chunks,
                corpus_c_chunks=corpus_c_chunks,
                validation_error=str(exc),
                corpus_b_indexed_total=corpus_b_indexed_total,
                corpus_c_indexed_total=corpus_c_indexed_total,
                corpus_b_upload_batch=payload.corpus_b_upload_batch,
                corpus_c_upload_batch=payload.corpus_c_upload_batch,
                corpus_b_filtered_total=corpus_b_filtered_total,
                corpus_c_filtered_total=corpus_c_filtered_total,
                assessment_strategy=strategy,
            )

    validation_error = ""
    report_structured: ComplianceReportStructured | None = None
    report_markdown = ""
    report_csv = ""
    schema_valid = False

    try:
        report_structured = _validate_compliance_report_payload(report_payload)
        report_markdown = _report_to_markdown(report_structured)
        report_csv = _report_findings_to_csv(report_structured)
        schema_valid = not used_fallback_payload
    except Exception as exc:
        logger.exception("Compliance report schema validation failed: %s", exc)
        validation_error = "Compliance report schema validation failed."
        if payload.validation_mode == "hard":
            raise RuntimeError(
                f"Compliance report schema validation failed: {validation_error}"
            ) from exc
        fallback_payload = _build_fallback_compliance_report_payload(
            question=effective_question,
            controls=controls,
            corpus_b_chunks=corpus_b_chunks,
            corpus_c_chunks=corpus_c_chunks,
            validation_error=str(exc),
            corpus_b_indexed_total=corpus_b_indexed_total,
            corpus_c_indexed_total=corpus_c_indexed_total,
            corpus_b_upload_batch=payload.corpus_b_upload_batch,
            corpus_c_upload_batch=payload.corpus_c_upload_batch,
            corpus_b_filtered_total=corpus_b_filtered_total,
            corpus_c_filtered_total=corpus_c_filtered_total,
            assessment_strategy=strategy,
        )
        report_structured = _validate_compliance_report_payload(fallback_payload)
        report_markdown = _report_to_markdown(report_structured)
        report_csv = _report_findings_to_csv(report_structured)

    report_filename_base = f"compliance-report-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    return {
        "mode": "compliance-report",
        "assessment_strategy": strategy,
        "report": report_markdown,
        "report_markdown": report_markdown,
        "report_structured": report_structured.model_dump() if report_structured else None,
        "report_findings_csv": report_csv,
        "report_filename_base": report_filename_base,
        "report_schema_version": COMPLIANCE_REPORT_SCHEMA_VERSION,
        "validation_mode": payload.validation_mode,
        "schema_valid": schema_valid,
        "validation_error": validation_error,
        "controls_count": len(controls),
        "corpus_b_count": len(corpus_b_chunks),
        "corpus_c_count": len(corpus_c_chunks),
        "corpus_b_indexed_total": corpus_b_indexed_total,
        "corpus_c_indexed_total": corpus_c_indexed_total,
        "corpus_b_upload_batch_filter": payload.corpus_b_upload_batch,
        "corpus_c_upload_batch_filter": payload.corpus_c_upload_batch,
        "assessment_question_supplied": bool(question),
        "effective_assessment_question": effective_question,
        "evidence_corpora_selected": selected_evidence_corpora,
        "audit": {
            "evidence_corpus_filter_expr": evidence_corpus_filter_expr,
            "corpus_b_filter_expr": corpus_b_filter_expr,
            "corpus_c_filter_expr": corpus_c_filter_expr,
        },
        "corpus_b_filtered_total": corpus_b_filtered_total,
        "corpus_c_filtered_total": corpus_c_filtered_total,
        "timings": {
            **controls_timings,
            "corpus_b_search_s": b_timings.get("search_s", 0.0),
            "corpus_c_search_s": c_timings.get("search_s", 0.0),
        },
    }


def _chunk_azure_artifact(artifact: Any, chunk_size: int = 2000) -> list[dict[str, Any]]:
    """Split Azure artifact content into chunk-like dicts for per-control evidence scoring."""
    content = (getattr(artifact, "content", None) or "").strip()
    title = str(getattr(artifact, "title", None) or "Azure scope evidence")
    if not content:
        return []
    chunks = []
    for i in range(0, len(content), chunk_size):
        chunk_text = content[i : i + chunk_size].strip()
        if chunk_text:
            chunks.append({"content": chunk_text, "source_name": title, "cosine_score": 1.0})
    return chunks


def _generate_azure_compliance_report_result(
    payload: AzureComplianceReportRequest,
    *,
    progress_cb: Callable[[int, int, str, str], None] | None = None,
) -> dict[str, Any]:
    subscription_id = payload.subscription_id.strip()
    resource_group = payload.resource_group.strip()
    resource_ids = [item.strip() for item in payload.resource_ids if item.strip()]
    if not subscription_id:
        raise ValueError("subscription_id must not be empty")
    if not resource_group and not resource_ids:
        raise ValueError("resource_group is required when resource_ids are not supplied")

    framework = _canonical_framework_name(payload.controls_framework)
    if framework is None:
        raise ValueError("controls_framework must be a supported framework value")

    resolved_env = dict(os.environ)
    resolved_env["CONTROLS_TOP_K"] = str(payload.controls_top_k)

    validation_error = ""
    report_structured: ComplianceReportStructured | None = None
    report_markdown = ""
    report_csv = ""
    schema_valid = False
    report_payload: dict[str, Any]

    if payload.assessment_strategy == "per_control":
        if progress_cb:
            progress_cb(0, 1, "", "Collecting Azure scope evidence")
        artifact, grounding = collect_azure_grounding(
            subscription_id=subscription_id,
            resource_group=resource_group,
            resource_ids=resource_ids,
            controls_framework=framework,
            env=resolved_env,
            credential=credential,
        )
        controls = list(grounding.corpus_a_results)
        corpus_b_chunks = list(grounding.corpus_b_results)
        corpus_c_chunks = _chunk_azure_artifact(artifact)
        scope_desc = (
            f"Azure {framework} compliance assessment: "
            f"subscription={subscription_id}, resource_group={resource_group or ', '.join(resource_ids[:3])}"
        )
        if progress_cb:
            progress_cb(
                0, len(controls), "", f"Starting per-control assessment: {len(controls)} controls"
            )
        report_payload = _build_per_control_report_payload(
            question=scope_desc,
            controls=controls,
            corpus_b_chunks=corpus_b_chunks,
            corpus_c_chunks=corpus_c_chunks,
            temperature=payload.temperature,
            progress_cb=progress_cb,
            corpus_c_scope_label="Live Azure artifacts collected",
        )
    else:
        if progress_cb:
            progress_cb(0, 1, "", "Collecting Azure scope evidence")
        assessment = run_azure_assessment(
            subscription_id=subscription_id,
            resource_group=resource_group,
            resource_ids=resource_ids,
            controls_framework=framework,
            env=resolved_env,
            credential=credential,
        )
        if progress_cb:
            progress_cb(1, 1, "", "Rendering assessment report")
        report_payload = assessment

    try:
        report_structured = _validate_compliance_report_payload(report_payload)
        report_markdown = _report_to_markdown(report_structured)
        report_csv = _report_findings_to_csv(report_structured)
        schema_valid = True
    except Exception as exc:
        logger.exception("Azure compliance report schema validation failed: %s", exc)
        validation_error = "Compliance report schema validation failed."
        if payload.validation_mode == "hard":
            raise RuntimeError("Compliance report schema validation failed") from exc

    report_filename_base = f"azure-compliance-report-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    return {
        "mode": "azure-compliance-report",
        "assessment_strategy": payload.assessment_strategy,
        "framework": framework,
        "controls_top_k": payload.controls_top_k,
        "temperature": payload.temperature,
        "scope": {
            "subscription_id": subscription_id,
            "resource_group": resource_group,
            "resource_ids": resource_ids,
        },
        "report": report_markdown,
        "report_markdown": report_markdown,
        "report_structured": report_structured.model_dump() if report_structured else None,
        "report_findings_csv": report_csv,
        "report_filename_base": report_filename_base,
        "report_schema_version": COMPLIANCE_REPORT_SCHEMA_VERSION,
        "validation_mode": payload.validation_mode,
        "schema_valid": schema_valid,
        "validation_error": validation_error,
    }


def _delete_search_documents_by_filter(
    client: SearchClient,
    *,
    filter_expr: str,
    key_field: str,
    page_size: int = 500,
    max_rounds: int = 50,
) -> dict[str, int]:
    deleted = 0
    rounds = 0
    while rounds < max_rounds:
        rounds += 1
        pager = client.search(
            search_text="*",
            filter=filter_expr,
            top=page_size,
            select=[key_field],
        )
        keys: list[str] = []
        for item in pager:
            value = str(item.get(key_field, "")).strip()
            if value:
                keys.append(value)

        if not keys:
            break

        client.delete_documents(documents=[{key_field: key} for key in keys])
        deleted += len(keys)

        if len(keys) < page_size:
            break

    return {"deleted": deleted, "rounds": rounds}


def _count_search_documents_by_filter(
    client: SearchClient,
    *,
    filter_expr: str,
) -> dict[str, int]:
    pager = client.search(
        search_text="*",
        filter=filter_expr,
        top=1,
        include_total_count=True,
    )
    # Iterate once so count gets populated by SDK paging implementation.
    for _ in pager:
        break
    count = pager.get_count() or 0
    return {"would_delete": int(count)}


def _list_search_documents_by_filter(
    client: SearchClient,
    *,
    filter_expr: str,
    select_fields: list[str],
    limit: int,
) -> dict[str, Any]:
    capped_limit = max(1, min(limit, 200))
    pager = client.search(
        search_text="*",
        filter=filter_expr,
        top=capped_limit,
        include_total_count=True,
        select=select_fields,
    )

    items: list[dict[str, Any]] = []
    for item in pager:
        row: dict[str, Any] = {}
        for field in select_fields:
            row[field] = item.get(field)
        items.append(row)

    count = pager.get_count() or len(items)
    return {
        "total_count": int(count),
        "returned_count": len(items),
        "items": items,
    }


def _count_search_documents_total_by_filter(client: SearchClient, *, filter_expr: str) -> int:
    try:
        pager = client.search(
            search_text="*",
            filter=filter_expr,
            top=1,
            include_total_count=True,
            select=["id"],
        )
        for _ in pager:
            break
        return int(pager.get_count() or 0)
    except Exception as exc:
        logger.warning("Failed to count search documents for filter %s: %s", filter_expr, exc)
        return 0


def _build_compliance_scope_inputs(
    *,
    question: str | None = None,
    controls_count: int,
    corpus_b_chunk_count: int,
    corpus_c_chunk_count: int,
    corpus_b_indexed_total: int,
    corpus_c_indexed_total: int,
    corpus_b_upload_batch: str | None = None,
    corpus_c_upload_batch: str | None = None,
    corpus_b_filtered_total: int | None = None,
    corpus_c_filtered_total: int | None = None,
    corpus_c_scope_label: str = "Corpus C artifacts retrieved",
    assessment_strategy: str | None = None,
) -> list[str]:
    items: list[str] = []
    if question is not None:
        items.append(f"Assessment question: {question[:200]}")
    items.extend(
        [
            f"Corpus A controls retrieved: {controls_count}",
            f"Corpus B guidance retrieved: {corpus_b_chunk_count}",
            f"{corpus_c_scope_label}: {corpus_c_chunk_count}",
        ]
    )
    if corpus_b_indexed_total > 0:
        items.append(f"Corpus B indexed documents available: {corpus_b_indexed_total}")
    if corpus_c_indexed_total > 0:
        items.append(f"Corpus C indexed documents available: {corpus_c_indexed_total}")
    if corpus_b_upload_batch:
        matched = corpus_b_filtered_total if corpus_b_filtered_total is not None else "unknown"
        items.append(
            f"Corpus B upload batch filter active: {corpus_b_upload_batch} (indexed matches: {matched})"
        )
    if corpus_c_upload_batch:
        matched = corpus_c_filtered_total if corpus_c_filtered_total is not None else "unknown"
        items.append(
            f"Corpus C upload batch filter active: {corpus_c_upload_batch} (indexed matches: {matched})"
        )
    if assessment_strategy and assessment_strategy != "single_pass":
        items.append(f"Assessment strategy: {assessment_strategy}")
    return items



# Move all helper function definitions above their first use
# (The actual function code is already present above, so we just remove this broken stub)

# Fix: The correct _delete_blob_prefix implementation should be placed above its first use, and the unclosed parenthesis removed.

def _delete_blob_prefix(prefix: str) -> dict[str, int]:
    if not _is_corpus_upload_enabled():
        return {"deleted": 0}

    account_url = f"https://{config.storage_account_name}.blob.core.windows.net"
    client = BlobServiceClient(account_url=account_url, credential=credential)
    container = client.get_container_client(config.storage_container_name)
    deleted = 0
    try:
        blobs = container.list_blobs(name_starts_with=prefix)
        for blob in blobs:
            # Delete every blob under the prefix so legacy extensionless
            # dedupe blobs cannot survive between runs.
            if blob.name:
                container.delete_blob(blob.name)
                deleted += 1
    except Exception as exc:
        logger.warning(f"Failed to delete blobs with prefix {prefix}: {exc}")
    return {"deleted": deleted}


def _report_to_markdown(report: ComplianceReportStructured) -> str:
    lines: list[str] = []
    lines.append("# Compliance Report")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append(report.executive_summary)
    lines.append("")
    lines.append("## Scope and Inputs")
    for item in report.scope_and_inputs:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Controls Assessed")
    for control in report.controls_assessed:
        lines.append(f"- {control}")
    lines.append("")
    lines.append("## Guidance Applied")
    if report.guidance_applied:
        for item in report.guidance_applied:
            lines.append(f"- {item}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Findings")
    for finding in report.findings:
        lines.append(f"### {finding.finding_id} - {finding.requirement_id}")
        lines.append(f"- Framework: {finding.framework}")
        lines.append(f"- Status: {finding.status}")
        lines.append(f"- Severity: {finding.severity}")
        lines.append(f"- Rationale: {finding.rationale}")
        lines.append("- Evidence Sources:")
        for source in finding.evidence_sources:
            lines.append(f"  - {source}")
        lines.append("- Gaps:")
        if finding.gaps:
            for gap in finding.gaps:
                lines.append(f"  - {gap}")
        else:
            lines.append("  - None")
        lines.append("- Recommendations:")
        if finding.recommendations:
            for rec in finding.recommendations:
                lines.append(f"  - {rec}")
        else:
            lines.append("  - None")
        lines.append("")
    lines.append("## Overall Risk Rating")
    lines.append(report.overall_risk_rating)
    lines.append("")
    lines.append("## Missing Evidence")
    if report.missing_evidence:
        for item in report.missing_evidence:
            lines.append(f"- {item}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Recommended Actions")
    for item in report.recommended_actions:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Citations")
    for item in report.citations:
        lines.append(f"- {item}")
    return "\n".join(lines)


class AskResponse(BaseModel):
    answer: str
    results: list[dict[str, Any]]
    controls_results: list[dict[str, Any]] = []
    controls_debug: dict[str, Any] | None = None
    evaluation: dict[str, Any] | None
    iterations: int | None
    metrics: dict[str, float] | None
    audit: dict[str, Any] | None = None
    error: str


def _get_user_id(auth_token: str, session_id: str) -> str:
    return _conversations_get_user_id(auth_token, session_id)


def _load_conversation(user_id: str, conversation_id: str) -> ConversationSession:
    return _conversations_load_conversation(user_id, conversation_id, conversations_container)


def _save_conversation(session: ConversationSession) -> None:
    _conversations_save_conversation(session, conversations_container)


def _build_feedback_context(session: ConversationSession, limit: int = 5) -> str:
    return _conversations_build_feedback_context(session, limit=limit)


def _cognitive_token() -> str:
    return credential.get_token("https://cognitiveservices.azure.com/.default").token


def _is_authorised(auth_token: str) -> bool:
    # Legacy shared token auth (optional)
    if config.auth_token and auth_token.strip() != config.auth_token:
        return False

    # Entra group auth (optional): when configured, the request must include
    # an authenticated principal header with the required group claim.
    if not config.required_group_object_id:
        return True

    return False


def _normalise_object_id(value: str) -> str:
    return value.strip().lower()


def _split_group_values(raw_value: str) -> set[str]:
    return {_normalise_object_id(part) for part in re.split(r"[,;\s]+", raw_value) if part.strip()}


def _decode_client_principal(encoded_principal: str) -> dict[str, Any] | None:
    if not encoded_principal:
        return None

    try:
        padded = encoded_principal + "=" * (-len(encoded_principal) % 4)
        decoded = base64.b64decode(padded).decode("utf-8")
        principal = json.loads(decoded)
    except Exception:
        return None

    return principal if isinstance(principal, dict) else None


def _groups_from_client_principal_header(encoded_principal: str) -> set[str]:
    """Extract Entra group object IDs from X-MS-CLIENT-PRINCIPAL header.

    Expected shape is the platform-auth principal object with a ``claims`` array.
    """
    principal = _decode_client_principal(encoded_principal)
    if not principal:
        return set()

    groups: set[str] = set()
    claims = principal.get("claims", [])
    if not isinstance(claims, list):
        return set()

    for claim in claims:
        if not isinstance(claim, dict):
            continue
        typ = str(claim.get("typ", "")).lower()
        val = str(claim.get("val", "")).strip()
        if not val:
            continue
        if typ in {
            "groups",
            "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups",
        }:
            groups.update(_split_group_values(val))

    return groups


def _principal_has_group_overage(encoded_principal: str) -> bool:
    principal = _decode_client_principal(encoded_principal)
    if not principal:
        return False

    claims = principal.get("claims", [])
    if not isinstance(claims, list):
        return False

    overage_claim_types = {
        "hasgroups",
        "_claim_names",
        "_claim_sources",
        "http://schemas.microsoft.com/claims/groups.link",
    }

    for claim in claims:
        if not isinstance(claim, dict):
            continue
        typ = str(claim.get("typ", "")).lower()
        if typ in overage_claim_types:
            return True

    return False


def _request_groups(request: Request | None) -> set[str]:
    if request is None:
        return set()

    encoded_principal = request.headers.get("x-ms-client-principal", "")
    groups = _groups_from_client_principal_header(encoded_principal)
    if groups:
        return groups

    header_groups = request.headers.get("x-ms-client-principal-groups", "")
    if header_groups:
        return _split_group_values(header_groups)

    return set()


def _group_auth_failure_message(request: Request | None) -> str:
    if request is None:
        return "Unauthorised. Request context unavailable for Entra ID group validation."

    encoded_principal = request.headers.get("x-ms-client-principal", "")
    flattened_groups = request.headers.get("x-ms-client-principal-groups", "")
    has_principal_context = bool(
        encoded_principal
        or flattened_groups
        or request.headers.get("x-ms-client-principal-id")
        or request.headers.get("x-ms-client-principal-name")
    )

    if not has_principal_context:
        return (
            "Unauthorised. No Entra ID principal headers were forwarded to the app. "
            "Complete platform sign-in first; an InPrivate session is fine only if it completes that auth flow."
        )

    if _principal_has_group_overage(encoded_principal):
        return (
            "Unauthorised. The signed-in Entra ID token did not include inline group claims "
            "(group overage). The current app gate requires concrete group IDs in platform auth headers."
        )

    if not _request_groups(request):
        return (
            "Unauthorised. An authenticated Entra ID principal reached the app, "
            "but no group claims were forwarded in the platform headers."
        )

    return "Unauthorised. User is not in the required Entra ID security group."


def _is_authorised_request(auth_token: str, request: Request | None) -> bool:
    # Legacy shared token auth (optional)
    if config.auth_token and auth_token.strip() != config.auth_token:
        return False

    # Entra group auth (optional)
    required_group = config.required_group_object_id
    if not required_group:
        return True

    if request is None:
        return False

    groups = _request_groups(request)
    return _normalise_object_id(required_group) in groups


def _unauthorised_message(request: Request | None = None) -> str:
    if config.required_group_object_id:
        return _group_auth_failure_message(request)
    return "Unauthorised. Provide a valid access token."


def _target_env_name() -> str:
    # TARGET_ENV is the canonical flag in this repo; ENV is accepted as fallback.
    return (
        os.getenv("TARGET_ENV", "").strip().lower()
        or os.getenv("ENV", "").strip().lower()
        or "dev"
    )


def _diagnostics_enabled() -> bool:
    return _target_env_name() != "prod"


def _check_diagnostics_access(request: Request, auth_token: str) -> JSONResponse | None:
    # TODO(security): require diagnostics access via a dedicated Entra group
    # separate from the general app access group. Keep this gate stricter than
    # baseline app access because diagnostics can expose operational metadata.
    if not _is_authorised_request(auth_token, request):
        return JSONResponse({"error": _unauthorised_message(request)}, status_code=401)

    if not _diagnostics_enabled():
        return JSONResponse(
            {
                "error": "Diagnostics endpoints are disabled when TARGET_ENV is 'prod'.",
                "target_env": _target_env_name(),
            },
            status_code=403,
        )

    return None


def _resolve_acr_registry_name(explicit_registry_name: str = "") -> str:
    candidates = [
        explicit_registry_name,
        os.getenv("ACR_NAME", ""),
        os.getenv("AZURE_CONTAINER_REGISTRY_NAME", ""),
        os.getenv("CONTAINER_REGISTRY_NAME", ""),
    ]

    login_server_candidates = [
        os.getenv("ACR_LOGIN_SERVER", ""),
        os.getenv("AZURE_CONTAINER_REGISTRY_LOGIN_SERVER", ""),
        os.getenv("CONTAINER_REGISTRY_LOGIN_SERVER", ""),
    ]
    for login_server in login_server_candidates:
        value = (login_server or "").strip().lower()
        if value.endswith(".azurecr.io"):
            candidates.append(value.split(".", 1)[0])

    for candidate in candidates:
        value = (candidate or "").strip()
        if value:
            return value

    return ""


def _list_acr_tags_via_management_api(
    *,
    subscription_id: str,
    resource_group: str,
    registry_name: str,
    repository: str,
    limit: int,
) -> dict[str, Any]:
    token = credential.get_token("https://management.azure.com/.default").token
    encoded_repo = quote(repository, safe="")
    base_url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.ContainerRegistry/registries/{registry_name}"
        f"/repositories/{encoded_repo}/tags"
    )
    url = f"{base_url}?api-version=2023-07-01&orderby=time_desc&n={limit}"

    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )

    if response.status_code >= 400:
        raise RuntimeError(
            "Failed to list ACR tags "
            f"for repository '{repository}': {response.status_code} {response.text}"
        )

    payload = response.json()
    values = payload.get("value", [])
    tags: list[dict[str, Any]] = []
    if isinstance(values, list):
        for item in values:
            if not isinstance(item, dict):
                continue
            digest = str(item.get("digest") or "").strip() or None
            tags.append(
                {
                    "name": str(item.get("name") or "").strip(),
                    "digest": digest,
                    "created_time": item.get("createdTime"),
                    "last_update_time": item.get("lastUpdateTime"),
                }
            )

    return {
        "tags": tags,
        "raw_count": len(values) if isinstance(values, list) else 0,
        "next_link": payload.get("nextLink"),
    }


def _embed_query(question: str) -> list[float]:
    token = _cognitive_token()
    url = (
        f"{config.openai_endpoint}/openai/deployments/"
        f"{config.embedding_deployment}/embeddings?api-version=2023-05-15"
    )
    max_attempts = 4
    base_delay_s = 0.75

    for attempt in range(max_attempts):
        try:
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"input": question},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            return payload["data"][0]["embedding"]
        except requests.RequestException as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            retryable = status_code in {429, 500, 502, 503, 504}
            if attempt >= max_attempts - 1 or not retryable:
                raise

            retry_after = getattr(getattr(exc, "response", None), "headers", {}).get("Retry-After")
            try:
                delay_s = max(float(retry_after), 0.0) if retry_after else 0.0
            except (TypeError, ValueError):
                delay_s = 0.0
            if delay_s <= 0:
                delay_s = base_delay_s * (2**attempt)

            logger.warning(
                "Embedding request failed with status %s (attempt %d/%d); retrying in %.2fs",
                status_code,
                attempt + 1,
                max_attempts,
                delay_s,
            )
            time.sleep(delay_s)

    raise RuntimeError("Embedding request failed after retries")


def _hybrid_search(
    question: str,
    retrieve_k: int,
    evidence_filter: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """hybrid search over documents.

    This path is resilient: if the grounding-index does not exist yet (e.g., ingestion
    not yet run), it returns an empty result set rather than failing the query.
    """
    timings: dict[str, float] = {}

    if evidence_filter == "__none__":
        timings["embedding_s"] = 0.0
        timings["search_s"] = 0.0
        return [], timings

    t0 = time.perf_counter()
    try:
        vector = _embed_query(question)
    except Exception as exc:
        timings["embedding_s"] = round(time.perf_counter() - t0, 3)
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code == 429:
            timings["embedding_rate_limited"] = 1.0
        logger.warning("Embedding failed; returning empty hybrid results: %s", exc)
        timings["search_s"] = 0.0
        return [], timings

    timings["embedding_s"] = round(time.perf_counter() - t0, 3)

    vector_query = VectorizedQuery(
        vector=vector,
        k_nearest_neighbors=retrieve_k,
        fields="content_vector",
    )

    t1 = time.perf_counter()
    try:
        results = search_client.search(
            search_text=question,
            vector_queries=[vector_query],
            top=retrieve_k,
            filter=evidence_filter,
            select=[
                "content",
                "source_name",
                "source_path",
                "corpus",
                "corpus_role",
                "upload_source",
                "uploaded_by",
                "upload_batch",
                "uploaded_at",
                "original_filename",
                "content_sha256",
                "normalised_text_sha256",
                "dedupe_hash",
                "dedupe_method",
            ],
        )
        items: list[dict[str, Any]] = []
        for r in results:
            score = r.get("@search.score")
            items.append(
                {
                    "content": (r.get("content") or "").strip(),
                    "source_name": r.get("source_name") or "unknown",
                    "source_path": r.get("source_path") or "",
                    "corpus": (r.get("corpus") or "").strip().lower(),
                    "corpus_role": (r.get("corpus_role") or "").strip().lower(),
                    "upload_source": r.get("upload_source") or "",
                    "uploaded_by": r.get("uploaded_by") or "",
                    "upload_batch": r.get("upload_batch") or "",
                    "uploaded_at": r.get("uploaded_at") or "",
                    "original_filename": r.get("original_filename") or "",
                    "content_sha256": r.get("content_sha256") or "",
                    "normalised_text_sha256": r.get("normalised_text_sha256") or "",
                    "dedupe_hash": r.get("dedupe_hash") or "",
                    "dedupe_method": r.get("dedupe_method") or "",
                    "score": float(score) if score is not None else 0.0,
                }
            )
    except Exception:
        # Grounding-index may not exist if document ingestion hasn't run yet.
        # Gracefully return empty results so query can proceed with controls-only.
        items = []

    timings["search_s"] = round(time.perf_counter() - t1, 3)
    return items, timings


_CORPUS_A_FRAMEWORKS = {
    "aescsf": "AESCSF",
    "cis_controls": "CIS Controls",
    "essential_eight": "Essential Eight",
    "ism": "ISM",
    "nist_csf": "NIST CSF",
    "pci_dss": "PCI DSS",
    "pspf": "PSPF",
}

_CORPUS_A_REFERENCE_UPLOAD_TARGETS = {
    "cis_controls": {
        ".xlsx": "CIS_Controls_Version_8.xlsx",
        ".pdf": "CIS_Controls__v8__Critical_Security_Controls__2023_08.pdf",
    },
    "pci_dss": {
        ".pdf": "PCI-DSS-v4_0_1.pdf",
    },
}

_CORPUS_A_SOURCE_UPLOAD_REQUIRED_FRAMEWORKS = {
    "cis_controls",
    "pci_dss",
}


def _normalise_corpus_a_framework_key(raw: str) -> str | None:
    key = (raw or "").strip().lower()
    if not key:
        return None
    if key in _CORPUS_A_FRAMEWORKS:
        return key

    if key in {"nist", "nist csf", "csf", "csf 2.0"}:
        return "nist_csf"
    if key in {"essential eight", "e8"}:
        return "essential_eight"
    if key in {"aescsf", "aemo"}:
        return "aescsf"
    if key in {"cis", "cis controls", "cis_controls"}:
        return "cis_controls"
    if key in {"ism", "information security manual"}:
        return "ism"
    if key in {"pci", "pci dss", "pci-dss", "pci_dss", "pci dss v4"}:
        return "pci_dss"
    if key in {"pspf", "protective security policy framework"}:
        return "pspf"
    if key == "all":
        return "all"
    return None


def _selected_corpus_a_frameworks(frameworks: list[str] | None) -> list[str]:
    if not frameworks:
        return sorted(_CORPUS_A_FRAMEWORKS.keys())

    selected: list[str] = []
    for raw in frameworks:
        key = _normalise_corpus_a_framework_key(raw)
        if key == "all":
            return sorted(_CORPUS_A_FRAMEWORKS.keys())
        if key and key not in selected:
            selected.append(key)

    return selected if selected else sorted(_CORPUS_A_FRAMEWORKS.keys())


def _prepare_corpus_a_reference_uploads(
    framework: str,
    files: list[UploadFile],
) -> tuple[str, list[tuple[UploadFile, str, str]]]:
    key = _normalise_corpus_a_framework_key(framework)
    if not key or key not in _CORPUS_A_REFERENCE_UPLOAD_TARGETS:
        raise ValueError(
            "Corpus A reference uploads are only supported for CIS Controls and PCI DSS."
        )

    target_map = _CORPUS_A_REFERENCE_UPLOAD_TARGETS[key]
    selected_by_target: dict[str, tuple[UploadFile, str]] = {}

    for file in files:
        original_name = file.filename or "uploaded.bin"
        ext = Path(original_name).suffix.lower()
        target_name = target_map.get(ext)
        if not target_name:
            allowed = ", ".join(sorted(target_map.keys()))
            raise ValueError(
                f"Unsupported file '{original_name}' for {_CORPUS_A_FRAMEWORKS[key]}; "
                f"expected file types: {allowed}."
            )
        if target_name in selected_by_target:
            raise ValueError(
                f"Received multiple files for {_CORPUS_A_FRAMEWORKS[key]} source type '{ext}'."
            )
        selected_by_target[target_name] = (file, original_name)

    missing_targets = [name for name in target_map.values() if name not in selected_by_target]
    if missing_targets:
        raise ValueError(
            "Missing required source files for "
            f"{_CORPUS_A_FRAMEWORKS[key]}: {', '.join(missing_targets)}."
        )

    prepared = [
        (upload_file, original_name, target_name)
        for target_name, (upload_file, original_name) in selected_by_target.items()
    ]
    return key, prepared


def _classify_corpus_a_auto_uploads(files: list[UploadFile]) -> dict[str, list[UploadFile]]:
    """Classify uploaded Corpus A source files into CIS/PCI framework buckets."""
    grouped: dict[str, list[UploadFile]] = {
        "cis_controls": [],
        "pci_dss": [],
    }
    ambiguous_pdfs: list[UploadFile] = []

    for file in files:
        original_name = (file.filename or "uploaded.bin").strip()
        lower_name = original_name.lower()
        ext = Path(original_name).suffix.lower()

        if ext == ".xlsx":
            grouped["cis_controls"].append(file)
            continue
        if ext != ".pdf":
            raise ValueError(
                f"Unsupported file '{original_name}' for auto mode; expected .pdf or .xlsx."
            )

        if "pci" in lower_name and "dss" in lower_name:
            grouped["pci_dss"].append(file)
        elif "cis" in lower_name and "control" in lower_name:
            grouped["cis_controls"].append(file)
        else:
            ambiguous_pdfs.append(file)

    cis_has_xlsx = any(
        Path((item.filename or "").strip()).suffix.lower() == ".xlsx"
        for item in grouped["cis_controls"]
    )
    cis_pdf_count = sum(
        1
        for item in grouped["cis_controls"]
        if Path((item.filename or "").strip()).suffix.lower() == ".pdf"
    )
    pci_pdf_count = sum(
        1
        for item in grouped["pci_dss"]
        if Path((item.filename or "").strip()).suffix.lower() == ".pdf"
    )

    for file in ambiguous_pdfs:
        if cis_has_xlsx and cis_pdf_count == 0:
            grouped["cis_controls"].append(file)
            cis_pdf_count += 1
            continue
        if pci_pdf_count == 0:
            grouped["pci_dss"].append(file)
            pci_pdf_count += 1
            continue
        raise ValueError(
            "Could not auto-map one or more PDF files. "
            "Choose a specific framework, or use canonical filenames for CIS/PCI sources."
        )

    selected = {framework: items for framework, items in grouped.items() if items}
    if not selected:
        raise ValueError("No supported Corpus A source files were provided.")
    return selected


def _controls_framework_ingestion_status() -> dict[str, Any]:
    status: dict[str, Any] = {}

    for key, framework_name in _CORPUS_A_FRAMEWORKS.items():
        escaped_framework = framework_name.replace("'", "''")
        filter_expr = f"framework eq '{escaped_framework}'"

        pager = controls_search_client.search(
            search_text="*",
            filter=filter_expr,
            top=100,
            include_total_count=True,
            select=["framework_version", "ingestion_manifest_hash", "ingestion_loaded_at"],
        )
        versions: set[str] = set()
        manifests: set[str] = set()
        loaded_at_values: list[str] = []
        for item in pager:
            version = str(item.get("framework_version", "")).strip()
            if version:
                versions.add(version)
            manifest = str(item.get("ingestion_manifest_hash", "")).strip()
            if manifest:
                manifests.add(manifest)
            loaded_at = str(item.get("ingestion_loaded_at", "")).strip()
            if loaded_at:
                loaded_at_values.append(loaded_at)

        total = pager.get_count() or 0
        status[key] = {
            "framework": framework_name,
            "ingested": total > 0,
            "document_count": total,
            "framework_versions": sorted(versions),
            "manifest_hashes": sorted(manifests),
            "latest_loaded_at": max(loaded_at_values) if loaded_at_values else None,
        }

    return status


# ---------------------------------------------------------------------------
# Controls search helpers — delegated to the controls module.
# These thin wrappers preserve the module-level names used by extracted
# modules via svc.xxx while keeping the real implementations in controls.py.
# ---------------------------------------------------------------------------


def _normalise_framework_filter(raw_value: str | None) -> str | None:
    return controls._normalise_framework_filter(raw_value, svc=sys.modules[__name__])


def _normalise_controls_comparison_mode(raw_value: str | None) -> str:
    return controls._normalise_controls_comparison_mode(raw_value)


def _normalise_evidence_corpus(raw_value: str) -> str | None:
    return controls._normalise_evidence_corpus(raw_value)


def _normalise_evidence_corpora(values: Iterable[str] | None) -> list[str] | None:
    return controls._normalise_evidence_corpora(values)


def _parse_evidence_corpora_csv(raw_value: str | None) -> list[str] | None:
    return controls._parse_evidence_corpora_csv(raw_value)


def _resolve_evidence_corpora(
    include: Iterable[str] | None,
    exclude: Iterable[str] | None,
    *,
    default_corpora: Iterable[str] | None = None,
) -> list[str]:
    return controls._resolve_evidence_corpora(include, exclude, default_corpora=default_corpora)


def _build_evidence_corpus_filter(selected_corpora: Iterable[str]) -> str | None:
    return controls._build_evidence_corpus_filter(selected_corpora)


def _controls_coverage_disclaimer(
    *,
    controls_debug: dict[str, Any] | None,
    comparison_detected: bool,
    comparison_mode: str,
) -> str | None:
    return controls._controls_coverage_disclaimer(
        controls_debug=controls_debug,
        comparison_detected=comparison_detected,
        comparison_mode=comparison_mode,
    )


def _prepend_disclaimer(answer: str, disclaimer: str | None) -> str:
    return controls._prepend_disclaimer(answer, disclaimer)


def _framework_authority_rank(framework_name: str) -> int:
    return controls._framework_authority_rank(framework_name, svc=sys.modules[__name__])


def _preferred_framework_for_question(question: str) -> str | None:
    return controls._preferred_framework_for_question(question, svc=sys.modules[__name__])


def _precedence_policy_summary() -> str:
    return controls._precedence_policy_summary(svc=sys.modules[__name__])


def _apply_framework_authority_preference(
    items: list[dict[str, Any]],
    top_k: int,
    question: str,
) -> list[dict[str, Any]]:
    return controls._apply_framework_authority_preference(
        items, top_k, question, svc=sys.modules[__name__]
    )


def _is_cross_framework_comparison_intent(question: str) -> bool:
    return controls._is_cross_framework_comparison_intent(question)


def _select_diverse_controls(items: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    return controls._select_diverse_controls(items, top_k)


def _summarise_controls_distribution(
    controls_list: list[dict[str, Any]],
    controls_timings: dict[str, float],
    *,
    preferred_framework: str | None = None,
) -> dict[str, Any]:
    return controls._summarise_controls_distribution(
        controls_list, controls_timings, preferred_framework=preferred_framework
    )


def _question_focus_terms(question: str) -> list[str]:
    return controls._question_focus_terms(question)


def _controls_query_variants(question: str) -> list[str]:
    return controls._controls_query_variants(question)


def _merge_control_candidates(
    base_items: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return controls._merge_control_candidates(base_items, new_items)


def _fetch_controls(
    search_text: str,
    retrieve_k: int,
    use_semantic: bool,
    framework_filter: str | None = None,
) -> list[dict[str, Any]]:
    return controls._fetch_controls(
        search_text, retrieve_k, use_semantic,
        framework_filter=framework_filter, svc=sys.modules[__name__]
    )


def _controls_search(
    question: str,
    retrieve_k: int,
    *,
    use_semantic: bool,
    framework_filter_override: str | None = None,
    comparison_mode: str = "auto-detect",
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    return controls.controls_search(
        question,
        retrieve_k,
        use_semantic=use_semantic,
        framework_filter_override=framework_filter_override,
        comparison_mode=comparison_mode,
        svc=sys.modules[__name__],
    )


def _is_temperature_unsupported_error(exc: Exception) -> bool:
    return llm_chat._is_temperature_unsupported_error(exc)


def _chat_completion(
    messages: list[dict[str, str]], deployment: str, temperature: float, timeout: int = 45
) -> str:
    return llm_chat._chat_completion(
        messages,
        deployment,
        temperature,
        svc=sys.modules[__name__],
        timeout=timeout,
    )


def _chat_completion_with_empty_retry(
    messages: list[dict[str, str]],
    *,
    deployment: str,
    temperature: float,
    timeout: int = 45,
) -> str:
    return llm_chat._chat_completion_with_empty_retry(
        messages,
        deployment=deployment,
        temperature=temperature,
        svc=sys.modules[__name__],
        timeout=timeout,
    )


def _evaluate(question: str, context: str, answer: str) -> dict[str, Any]:
    return llm_chat._evaluate(question, context, answer, svc=sys.modules[__name__])


def _call_validator(text: str, timeout_s: int = 15) -> dict[str, Any]:
    return llm_chat._call_validator(text, svc=sys.modules[__name__], timeout_s=timeout_s)


def _run_rag(
    question: str,
    retrieve_k: int,
    temperature: float,
    controls_semantic: bool,
    controls_framework: str | None = None,
    controls_comparison_mode: str = "auto-detect",
    evidence_corpora_include: list[str] | None = None,
    evidence_corpora_exclude: list[str] | None = None,
    conversation_history: list[ConversationMessage] | None = None,
    feedback_context: str = "",
) -> dict[str, Any]:
    return rag_pipeline._run_rag(
        question,
        retrieve_k,
        temperature,
        controls_semantic,
        svc=sys.modules[__name__],
        controls_framework=controls_framework,
        controls_comparison_mode=controls_comparison_mode,
        evidence_corpora_include=evidence_corpora_include,
        evidence_corpora_exclude=evidence_corpora_exclude,
        conversation_history=conversation_history,
        feedback_context=feedback_context,
    )


# Blob name sanitization moved to utils.py module


def _is_corpus_upload_enabled() -> bool:
    return bool(config.storage_account_name)


def _is_ingestion_job_trigger_enabled() -> bool:
    return bool(
        config.ingestion_job_subscription_id
        and config.ingestion_job_resource_group
        and config.ingestion_job_name
    )


def _trigger_ingestion_job() -> dict[str, Any]:
    return _trigger_ingestion_job_with_args(None)


def _is_indexer_running(status: Any) -> bool:
    """Best-effort detection for active indexer execution across SDK shapes.

    Top-level IndexerStatus.running means the indexer is healthy/operational, NOT that an
    execution is in flight.  Only last_result.status == "inprogress" reliably signals an
    active execution.
    """
    try:
        last_result = getattr(status, "last_result", None)
        if last_result is not None:
            run_status = str(getattr(last_result, "status", "")).strip().lower()
            if run_status == "inprogress":
                return True
    except Exception:
        pass

    return False


def _wait_for_indexer_idle(indexer_name: str, timeout_seconds: int = 900) -> bool:
    """Wait until the target indexer is no longer actively running."""
    client = SearchIndexerClient(endpoint=config.search_endpoint, credential=credential)
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        try:
            status = client.get_indexer_status(indexer_name)
        except Exception:
            # If status cannot be resolved, keep retrying briefly.
            time.sleep(5)
            continue

        if not _is_indexer_running(status):
            return True
        time.sleep(5)

    return False


def _reset_grounding_indexer_state() -> str:
    """Reset the grounding indexer high-watermark so unchanged blobs can be reprocessed."""
    indexer_name = os.getenv(
        "AZURE_SEARCH_INDEXER_NAME", f"{config.search_index_name}-indexer"
    ).strip()
    if not indexer_name:
        raise RuntimeError("AZURE_SEARCH_INDEXER_NAME is empty.")

    client = SearchIndexerClient(endpoint=config.search_endpoint, credential=credential)
    try:
        client.reset_indexer(indexer_name)
    except HttpResponseError as exc:
        if exc.status_code != 409:
            raise

        # 409 ConflictingOperation means an active run is holding the indexer.
        # For dedupe reindexing we must perform a real reset, so wait for idle
        # and retry once rather than silently treating this as success.
        logger.warning(
            "Indexer %s reset blocked by active run (409); waiting for idle before retry",
            indexer_name,
        )
        if not _wait_for_indexer_idle(indexer_name):
            raise RuntimeError(
                f"Timed out waiting for indexer '{indexer_name}' to become idle for reset."
            ) from exc

        client.reset_indexer(indexer_name)
        logger.info(
            "Indexer %s reset succeeded after waiting for active run to finish", indexer_name
        )
    return indexer_name


def _get_ingestion_job_template_container(token: str) -> dict[str, Any]:
    """Fetch the current job template container for safe args override starts."""
    get_url = (
        f"https://management.azure.com/subscriptions/{config.ingestion_job_subscription_id}"
        f"/resourceGroups/{config.ingestion_job_resource_group}"
        f"/providers/Microsoft.App/jobs/{config.ingestion_job_name}"
        "?api-version=2024-03-01"
    )
    resp = requests.get(
        get_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Failed to fetch ingestion job definition: {resp.status_code} {resp.text}"
        )
    containers = resp.json().get("properties", {}).get("template", {}).get("containers", [])
    if not containers:
        raise RuntimeError("Ingestion job definition contains no containers.")
    return dict(containers[0])


def _trigger_ingestion_job_with_args(args_override: list[str] | None) -> dict[str, Any]:
    if not _is_ingestion_job_trigger_enabled():
        raise RuntimeError(
            "Ingestion job trigger is not configured. "
            "Set INGESTION_JOB_SUBSCRIPTION_ID, INGESTION_JOB_RESOURCE_GROUP, and INGESTION_JOB_NAME."
        )

    token = credential.get_token("https://management.azure.com/.default").token
    url = (
        f"https://management.azure.com/subscriptions/{config.ingestion_job_subscription_id}"
        f"/resourceGroups/{config.ingestion_job_resource_group}"
        f"/providers/Microsoft.App/jobs/{config.ingestion_job_name}/start"
        "?api-version=2024-03-01"
    )

    if args_override:
        container = _get_ingestion_job_template_container(token)
        container["args"] = args_override
        body: dict[str, Any] = {"containers": [container]}
    else:
        body = {}

    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Failed to start ingestion job: {response.status_code} {response.text}")

    execution_name: str | None = None
    try:
        payload = response.json()
        if isinstance(payload, dict):
            execution_name = str(payload.get("name") or "").strip() or None
    except Exception:
        execution_name = None

    location_header = str(response.headers.get("Location") or "").strip()
    if not execution_name and "/executions/" in location_header:
        execution_name = location_header.rsplit("/executions/", 1)[-1].split("?", 1)[0] or None

    return {
        "status_code": response.status_code,
        "resource_group": config.ingestion_job_resource_group,
        "job_name": config.ingestion_job_name,
        "execution_name": execution_name,
        "args_override": args_override or [],
    }


_REQUIRED_INGESTION_METADATA_KEYS = {
    "corpus",
    "corpus_role",
    "upload_source",
    "uploaded_by",
    "upload_batch",
    "uploaded_at",
    "original_filename",
    "dedupe_hash",
    "dedupe_method",
}


def _blob_has_required_ingestion_metadata(metadata: dict[str, str] | None) -> bool:
    if not metadata:
        return False
    for key in _REQUIRED_INGESTION_METADATA_KEYS:
        if not str(metadata.get(key) or "").strip():
            return False
    return True


def _mark_dedupe_blobs_for_reindex(
    corpus: str, dedupe_hashes: list[str], *, user_id: str
) -> dict[str, Any]:
    if not dedupe_hashes:
        return {"requested": 0, "touched": 0, "not_found": [], "failed": []}

    account_url = f"https://{config.storage_account_name}.blob.core.windows.net"
    client = BlobServiceClient(account_url=account_url, credential=credential)
    container = client.get_container_client(config.storage_container_name)

    touched = 0
    not_found: list[str] = []
    failed: list[str] = []

    for dedupe_hash in dedupe_hashes:
        dedupe_prefix = _dedupe_blob_prefix(corpus, dedupe_hash)
        matching_blob_names = [
            blob.name for blob in container.list_blobs(name_starts_with=dedupe_prefix)
        ]
        if not matching_blob_names:
            not_found.append(f"{dedupe_prefix}*")
            continue

        for blob_name in matching_blob_names:
            blob = container.get_blob_client(blob_name)
            try:
                props = blob.get_blob_properties()
                metadata = dict(props.metadata or {})
                metadata["reindex_requested_at"] = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                metadata["reindex_requested_by"] = _sanitise_blob_name_component(
                    user_id or "anonymous"
                )
                blob.set_blob_metadata(metadata=metadata)
                touched += 1
            except Exception as exc:
                failed.append(f"{blob_name}: {exc}")

    return {
        "requested": len(dedupe_hashes),
        "touched": touched,
        "not_found": not_found,
        "failed": failed,
    }


def _latest_ingestion_job_execution() -> dict[str, Any] | None:
    if not _is_ingestion_job_trigger_enabled():
        return None

    token = credential.get_token("https://management.azure.com/.default").token
    url = (
        f"https://management.azure.com/subscriptions/{config.ingestion_job_subscription_id}"
        f"/resourceGroups/{config.ingestion_job_resource_group}"
        f"/providers/Microsoft.App/jobs/{config.ingestion_job_name}/executions"
        "?api-version=2024-03-01"
    )
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Failed to list ingestion job executions: {response.status_code} {response.text}"
        )

    values = response.json().get("value", [])
    if not values:
        return None

    def _sort_key(item: dict[str, Any]) -> str:
        props = item.get("properties", {})
        return str(props.get("startTime") or "")

    latest = max(values, key=_sort_key)
    props = latest.get("properties", {})
    return {
        "name": latest.get("name"),
        "status": props.get("status"),
        "start_time": props.get("startTime"),
        "end_time": props.get("endTime"),
    }


def _upload_corpus_files(
    files: list[UploadFile],
    user_id: str,
    *,
    corpus: str,
    corpus_role: str,
) -> dict[str, Any]:
    if not _is_corpus_upload_enabled():
        raise RuntimeError(
            "Corpus upload is not configured. Set AZURE_STORAGE_ACCOUNT_NAME in query web configuration."
        )

    account_url = f"https://{config.storage_account_name}.blob.core.windows.net"
    client = BlobServiceClient(account_url=account_url, credential=credential)
    container = client.get_container_client(config.storage_container_name)

    uploaded: list[dict[str, Any]] = []
    skipped: list[str] = []
    failed: list[str] = []

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    upload_batch_id: str | None = None

    for file in files:
        original_name = file.filename or "uploaded.bin"
        ext = Path(original_name).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            skipped.append(f"{original_name}: disallowed filetype {ext}")
            try:
                file.file.close()
            except Exception:
                pass
            continue

        try:
            content = file.file.read()
            if not content:
                skipped.append(original_name)
                continue

            content_sha256 = hashlib.sha256(content).hexdigest()
            normalised_text_sha256, hash_method = _compute_normalised_text_hash(
                content,
                filename=original_name,
                content_type=file.content_type or "",
            )
            dedupe_hash = normalised_text_sha256 or content_sha256
            dedupe_method = "normalised_text_sha256" if normalised_text_sha256 else "content_sha256"
            hash_blob_prefix = _dedupe_blob_prefix(corpus, dedupe_hash)
            hash_blob_name = f"{hash_blob_prefix}{ext}"
            existing_blob_names = [
                blob.name for blob in container.list_blobs(name_starts_with=hash_blob_prefix)
            ]

            if upload_batch_id is None:
                upload_batch_id = str(uuid.uuid4())

            metadata = {
                "corpus": corpus,
                "corpus_role": corpus_role,
                "upload_source": "query_web",
                "uploaded_by": _sanitise_blob_name_component(user_id or "anonymous"),
                "upload_batch": upload_batch_id,
                "uploaded_at": ts,
                "original_filename": _sanitise_blob_name_component(original_name),
                "content_sha256": content_sha256,
                "normalised_text_sha256": normalised_text_sha256 or "",
                "dedupe_hash": dedupe_hash,
                "dedupe_method": dedupe_method,
                "hash_method": hash_method,
            }

            should_repair_existing = False
            for existing_blob_name in existing_blob_names:
                existing_blob = container.get_blob_client(existing_blob_name)
                try:
                    existing_props = existing_blob.get_blob_properties()
                    existing_metadata = dict(existing_props.metadata or {})
                except Exception:
                    existing_metadata = {}
                existing_ext = Path(existing_blob_name).suffix.lower()
                metadata_ok = _blob_has_required_ingestion_metadata(existing_metadata)
                if not metadata_ok or existing_ext != ext:
                    should_repair_existing = True
                    break

            if existing_blob_names and not should_repair_existing:
                skipped.append(f"{original_name}: duplicate-{dedupe_method}:{dedupe_hash}")
                continue

            container.upload_blob(
                name=hash_blob_name,
                data=content,
                overwrite=True,
                metadata=metadata,
                content_settings=ContentSettings(
                    content_type=file.content_type or "application/octet-stream"
                ),
            )

            if should_repair_existing:
                for existing_blob_name in existing_blob_names:
                    if existing_blob_name == hash_blob_name:
                        continue
                    try:
                        container.delete_blob(existing_blob_name)
                    except Exception as exc:
                        logger.warning(
                            "Failed to delete stale dedupe blob %s during repair: %s",
                            existing_blob_name,
                            exc,
                        )

            uploaded.append(
                {
                    "blob_name": hash_blob_name,
                    "size_bytes": len(content),
                    "content_type": file.content_type or "application/octet-stream",
                    "content_sha256": content_sha256,
                    "normalised_text_sha256": normalised_text_sha256,
                    "dedupe_hash": dedupe_hash,
                    "dedupe_method": dedupe_method,
                    "repaired_existing": should_repair_existing,
                    "metadata": metadata,
                }
            )
        except Exception as exc:
            logger.warning("Failed to upload file %s: %s", original_name, exc, exc_info=True)
            failed.append(f"{original_name}: upload failed")
        finally:
            try:
                file.file.close()
            except Exception:
                pass

    return {
        "upload_batch_id": upload_batch_id,
        "prefix": f"corpus-{corpus}/by-dedupe",
        "uploaded": uploaded,
        "skipped": skipped,
        "failed": failed,
    }


def _upload_corpus_b_files(files: list[UploadFile], user_id: str) -> dict[str, Any]:
    return _upload_corpus_files(
        files,
        user_id,
        corpus="b",
        corpus_role="narrative_guidance",
    )


def _upload_corpus_c_files(files: list[UploadFile], user_id: str) -> dict[str, Any]:
    return _upload_corpus_files(
        files,
        user_id,
        corpus="c",
        corpus_role="assessed_artifact",
    )


def _upload_corpus_a_reference_files(
    files: list[UploadFile],
    user_id: str,
    *,
    framework: str,
) -> dict[str, Any]:
    if not _is_corpus_upload_enabled():
        raise RuntimeError(
            "Corpus upload is not configured. Set AZURE_STORAGE_ACCOUNT_NAME in query web configuration."
        )

    framework_key, prepared_uploads = _prepare_corpus_a_reference_uploads(framework, files)

    account_url = f"https://{config.storage_account_name}.blob.core.windows.net"
    client = BlobServiceClient(account_url=account_url, credential=credential)
    container = client.get_container_client(config.storage_container_name)

    uploaded: list[dict[str, Any]] = []
    failed: list[str] = []

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    upload_batch_id = str(uuid.uuid4())
    source_prefix = f"corpus-a/source/{framework_key}/{upload_batch_id}"

    for file, original_name, target_name in prepared_uploads:
        try:
            content = file.file.read()
            if not content:
                raise ValueError(f"{original_name} is empty")

            blob_name = f"{source_prefix}/{target_name}"
            metadata = {
                "corpus": "a",
                "framework": framework_key,
                "upload_source": "query_web",
                "uploaded_by": _sanitise_blob_name_component(user_id or "anonymous"),
                "upload_batch": upload_batch_id,
                "uploaded_at": ts,
                "original_filename": _sanitise_blob_name_component(original_name),
                "target_filename": target_name,
            }
            container.upload_blob(
                name=blob_name,
                data=content,
                overwrite=True,
                metadata=metadata,
                content_settings=ContentSettings(
                    content_type=file.content_type or "application/octet-stream"
                ),
            )
            uploaded.append(
                {
                    "blob_name": blob_name,
                    "size_bytes": len(content),
                    "content_type": file.content_type or "application/octet-stream",
                    "original_filename": original_name,
                    "target_filename": target_name,
                    "metadata": metadata,
                }
            )
        except Exception as exc:
            logger.warning("Failed to upload file %s: %s", original_name, exc, exc_info=True)
            failed.append(f"{original_name}: upload failed")
        finally:
            try:
                file.file.close()
            except Exception:
                pass

    return {
        "framework": framework_key,
        "framework_name": _CORPUS_A_FRAMEWORKS[framework_key],
        "upload_batch_id": upload_batch_id,
        "source_prefix": source_prefix,
        "uploaded": uploaded,
        "failed": failed,
    }


# Register diagnostics endpoints
register_diagnostics_endpoints(
    app,
    credential,
    config,
    search_client,
    _is_corpus_upload_enabled,
    _is_ingestion_job_trigger_enabled,
    _latest_ingestion_job_execution,
    _count_blob_prefix,
    _count_search_documents_total_by_filter,
    _utc_now_iso,
    _REQUIRED_INGESTION_METADATA_KEYS,
    svc=sys.modules[__name__],
)


# Register status endpoints
register_status_endpoints(
    app,
    config,
    search_client,
    controls_search_client,
    QUERY_WEB_VERSION_SIGNATURE,
    precedence_policy,
    _CONTROLS_FRAMEWORK_FILTERS,
    _CORPUS_A_FRAMEWORKS,
    _is_corpus_upload_enabled,
    _is_ingestion_job_trigger_enabled,
    COMPLIANCE_REPORT_SCHEMA_VERSION,
)

# Register extracted compliance and corpus endpoints.
register_compliance_endpoints(app, svc=sys.modules[__name__])
register_corpus_endpoints(app, svc=sys.modules[__name__])
register_home_endpoints(app, svc=sys.modules[__name__])
register_ask_endpoints(
    app,
    svc=sys.modules[__name__],
    ask_request_model=AskRequest,
    ask_response_model=AskResponse,
)

# Register conversations endpoints
from conversations import register_conversations_endpoints

register_conversations_endpoints(
    app,
    conversations_container,
    _is_authorised_request,
    _unauthorised_message,
)
