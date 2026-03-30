from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import requests
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field


import uuid
from dataclasses import field
from datetime import UTC, datetime

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam

try:
    from azure.cosmos.exceptions import CosmosResourceNotFoundError as _CosmosResourceNotFoundError
except Exception:
    _CosmosResourceNotFoundError = Exception

CosmosResourceNotFoundError: type[Exception] = _CosmosResourceNotFoundError


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class QueryConfig:
    search_endpoint: str
    search_index_name: str
    controls_index_name: str
    openai_endpoint: str
    embedding_deployment: str
    query_deployment: str
    evaluator_deployment: str
    search_top_k: int
    controls_top_k: int
    controls_semantic_default: bool
    controls_semantic_configuration_name: str
    default_temperature: float
    evaluation_threshold: float
    auth_token: str
    required_group_object_id: str

    cosmos_endpoint: str
    cosmos_database_name: str
    cosmos_container_name: str

def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable not set: {name}")
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _form_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    text = value.strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


def load_config() -> QueryConfig:
    return QueryConfig(
        search_endpoint=_require_env("AZURE_SEARCH_ENDPOINT"),
        search_index_name=os.getenv("AZURE_SEARCH_INDEX_NAME", "grounding-index"),
        controls_index_name=os.getenv("AZURE_SEARCH_CONTROLS_INDEX_NAME", "controls-index"),
        openai_endpoint=_require_env("AZURE_OPENAI_ENDPOINT"),
        embedding_deployment=os.getenv("EMBEDDING_DEPLOYMENT_NAME", "text-embedding-ada-002"),
        query_deployment=os.getenv("QUERY_DEPLOYMENT_NAME", "gpt-5.1-chat"),
        evaluator_deployment=os.getenv("EVALUATOR_DEPLOYMENT_NAME", "gpt-4.1-mini"),
        search_top_k=int(os.getenv("SEARCH_TOP_K", "5")),
        controls_top_k=int(os.getenv("CONTROLS_TOP_K", "4")),
        controls_semantic_default=_env_bool("CONTROLS_SEMANTIC_DEFAULT", default=False),
        controls_semantic_configuration_name=os.getenv("AZURE_SEARCH_CONTROLS_SEMANTIC_CONFIG", "controls-semantic"),
        default_temperature=float(os.getenv("DEFAULT_TEMPERATURE", "1")),
        evaluation_threshold=float(os.getenv("ACCEPTABLE_SCORE_THRESHOLD", "0.72")),
        auth_token=os.getenv("QUERY_WEB_AUTH_TOKEN", "").strip(),
        required_group_object_id=os.getenv("QUERY_WEB_REQUIRED_GROUP_OBJECT_ID", "").strip(),
        cosmos_endpoint=_require_env("AZURE_COSMOS_ENDPOINT"),
        cosmos_database_name=_require_env("AZURE_COSMOS_DATABASE_NAME"),
        cosmos_container_name=_require_env("AZURE_COSMOS_CONTAINER_NAME"),
    )

@dataclass
class ConversationMessage:
    """A single message in a conversation."""
    role: str  # "user" or "assistant"
    content: str
    timestamp: str = field(default_factory=_utc_now_iso)


@dataclass
class ResponseRating:
    """User rating and TODO feedback for a prior assistant response."""
    rating: int  # 1..5
    todo: str = ""
    assistant_timestamp: str = ""
    timestamp: str = field(default_factory=_utc_now_iso)


@dataclass
class ConversationSession:
    """Conversation session stored in CosmosDB."""
    session_id: str
    user_id: str  # auth_token hash or session token
    conversation_id: str  # unique per conversation
    messages: list[ConversationMessage] = field(default_factory=list)
    response_ratings: list[ResponseRating] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)
    evaluation_threshold: float = 0.72

    def to_dict(self) -> dict[str, Any]:
        # Sanitize ID by replacing hyphens from UUIDs with underscores for Cosmos compatibility
        sanitized_id = f"{self.user_id.replace('-', '_')}_{self.conversation_id.replace('-', '_')}"
        return {
            "id": sanitized_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "messages": [{"role": m.role, "content": m.content, "timestamp": m.timestamp} for m in self.messages],
            "response_ratings": [
                {
                    "rating": r.rating,
                    "todo": r.todo,
                    "assistant_timestamp": r.assistant_timestamp,
                    "timestamp": r.timestamp,
                }
                for r in self.response_ratings
            ],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "evaluation_threshold": self.evaluation_threshold,
            "type": "conversation",
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "ConversationSession":
        messages = [
            ConversationMessage(role=m["role"], content=m["content"], timestamp=m.get("timestamp", _utc_now_iso()))
            for m in data.get("messages", [])
        ]
        response_ratings = [
            ResponseRating(
                rating=int(r.get("rating", 0)),
                todo=str(r.get("todo", "")),
                assistant_timestamp=str(r.get("assistant_timestamp", "")),
                timestamp=r.get("timestamp", _utc_now_iso()),
            )
            for r in data.get("response_ratings", [])
        ]
        return ConversationSession(
            session_id=data["session_id"],
            user_id=data["user_id"],
            conversation_id=data["conversation_id"],
            messages=messages,
            response_ratings=response_ratings,
            created_at=data.get("created_at", _utc_now_iso()),
            updated_at=data.get("updated_at", _utc_now_iso()),
            evaluation_threshold=data.get("evaluation_threshold", 0.72),
        )

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


app = FastAPI(title="RAG Query Console")
templates = Jinja2Templates(directory="templates")
credential = DefaultAzureCredential()
config = load_config()
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

# Initialize CosmosDB client
try:
    from azure.cosmos import CosmosClient
    cosmos_client = CosmosClient(url=config.cosmos_endpoint, credential=credential)
    cosmos_db = cosmos_client.get_database_client(config.cosmos_database_name)
    conversations_container = cosmos_db.get_container_client(config.cosmos_container_name)
except (ImportError, Exception) as exc:
    # If CosmosDB is unavailable, continue with in-memory conversation tracking
    cosmos_client = None
    conversations_container = None
    import logging
    logging.warning(f"CosmosDB unavailable: {exc}. Conversations will not be persisted.")


class AskRequest(BaseModel):
    question: str
    retrieve_k: int = Field(default=5, ge=1, le=20)
    temperature: float = Field(default=1.0, ge=0.0, le=1.0)
    auth_token: str = ""
    controls_semantic: bool | None = None


class AskResponse(BaseModel):
    answer: str
    results: list[dict[str, Any]]
    controls_results: list[dict[str, Any]] = []
    evaluation: dict[str, Any] | None
    iterations: int | None
    metrics: dict[str, float] | None
    error: str


def _get_user_id(auth_token: str, session_id: str) -> str:
    """Generate a stable user identifier from auth token or session ID."""
    import hashlib
    if auth_token.strip():
        return hashlib.sha256(auth_token.encode()).hexdigest()[:16]
    return session_id[:16]


def _load_conversation(user_id: str, conversation_id: str) -> ConversationSession:
    """Load conversation from CosmosDB or create new one."""
    if not conversations_container:
        # Fallback to in-memory new conversation
        return ConversationSession(
            session_id=str(uuid.uuid4()),
            user_id=user_id,
            conversation_id=conversation_id,
        )
    
    # Sanitize ID by replacing hyphens from UUIDs with underscores for Cosmos compatibility
    doc_id = f"{user_id.replace('-', '_')}_{conversation_id.replace('-', '_')}"
    try:
        doc = conversations_container.read_item(item=doc_id, partition_key=user_id)
        return ConversationSession.from_dict(doc)
    except CosmosResourceNotFoundError:
        # Conversation doesn't exist yet
        return ConversationSession(
            session_id=str(uuid.uuid4()),
            user_id=user_id,
            conversation_id=conversation_id,
        )
    except Exception as exc:
        raise RuntimeError(f"Conversation persistence read failed: {exc}") from exc


def _save_conversation(session: ConversationSession) -> None:
    """Save conversation to CosmosDB."""
    if not conversations_container:
        return
    try:
        conversations_container.upsert_item(session.to_dict())
    except Exception as exc:
        raise RuntimeError(f"Conversation persistence write failed: {exc}") from exc


def _build_feedback_context(session: ConversationSession, limit: int = 5) -> str:
    """Build short feedback context from recent user ratings/TODO notes."""
    if not session.response_ratings:
        return ""

    lines: list[str] = []
    for rating in session.response_ratings[-limit:]:
        todo_text = rating.todo.strip() or "No TODO provided"
        lines.append(f"- rating={rating.rating}/5; todo={todo_text}")

    return "Recent user feedback on prior answers:\n" + "\n".join(lines)

def _cognitive_token() -> str:
    return credential.get_token("https://cognitiveservices.azure.com/.default").token


def _is_authorized(auth_token: str) -> bool:
    # Legacy shared token auth (optional)
    if config.auth_token and auth_token.strip() != config.auth_token:
        return False

    # Entra group auth (optional): when configured, the request must include
    # an authenticated principal header with the required group claim.
    if not config.required_group_object_id:
        return True

    return False


def _normalize_object_id(value: str) -> str:
    return value.strip().lower()


def _split_group_values(raw_value: str) -> set[str]:
    return {
        _normalize_object_id(part)
        for part in re.split(r"[,;\s]+", raw_value)
        if part.strip()
    }


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
        return "Unauthorized. Request context unavailable for Entra ID group validation."

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
            "Unauthorized. No Entra ID principal headers were forwarded to the app. "
            "Complete platform sign-in first; an InPrivate session is fine only if it completes that auth flow."
        )

    if _principal_has_group_overage(encoded_principal):
        return (
            "Unauthorized. The signed-in Entra ID token did not include inline group claims "
            "(group overage). The current app gate requires concrete group IDs in platform auth headers."
        )

    if not _request_groups(request):
        return (
            "Unauthorized. An authenticated Entra ID principal reached the app, "
            "but no group claims were forwarded in the platform headers."
        )

    return "Unauthorized. User is not in the required Entra ID security group."


def _is_authorized_request(auth_token: str, request: Request | None) -> bool:
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
    return _normalize_object_id(required_group) in groups


def _unauthorized_message(request: Request | None = None) -> str:
    if config.required_group_object_id:
        return _group_auth_failure_message(request)
    return "Unauthorized. Provide a valid access token."


def _embed_query(question: str) -> list[float]:
    token = _cognitive_token()
    url = (
        f"{config.openai_endpoint}/openai/deployments/"
        f"{config.embedding_deployment}/embeddings?api-version=2023-05-15"
    )
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"input": question},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return payload["data"][0]["embedding"]


def _hybrid_search(question: str, retrieve_k: int) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """hybrid search over documents.
    
    This path is resilient: if the grounding-index does not exist yet (e.g., ingestion
    not yet run), it returns an empty result set rather than failing the query.
    """
    timings: dict[str, float] = {}

    t0 = time.perf_counter()
    vector = _embed_query(question)
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
            select=["content", "source_name", "source_path"],
        )
        items: list[dict[str, Any]] = []
        for r in results:
            score = r.get("@search.score")
            items.append(
                {
                    "content": (r.get("content") or "").strip(),
                    "source_name": r.get("source_name") or "unknown",
                    "source_path": r.get("source_path") or "",
                    "score": float(score) if score is not None else 0.0,
                }
            )
    except Exception:
        # Grounding-index may not exist if document ingestion hasn't run yet.
        # Gracefully return empty results so query can proceed with controls-only.
        items = []
    
    timings["search_s"] = round(time.perf_counter() - t1, 3)
    return items, timings


def _fetch_controls(search_text: str, retrieve_k: int, use_semantic: bool) -> list[dict[str, Any]]:
    """Execute a controls-index search and return hydrated items.

    Raises exceptions on error so callers can decide how to handle them.
    """
    _SELECT = [
        "requirement_id", "framework", "framework_version", "control_family",
        "maturity_level", "requirement_text", "guidance_text", "source_uri",
    ]
    search_kwargs: dict[str, Any] = {
        "search_text": search_text,
        "top": retrieve_k,
        "select": _SELECT,
    }
    if use_semantic:
        search_kwargs["query_type"] = "semantic"
        search_kwargs["semantic_configuration_name"] = config.controls_semantic_configuration_name

    items: list[dict[str, Any]] = []
    for r in controls_search_client.search(**search_kwargs):
        requirement_text = (r.get("requirement_text") or "").strip()
        if not requirement_text:
            continue
        score = r.get("@search.score")
        items.append(
            {
                "requirement_id": r.get("requirement_id") or "",
                "framework": r.get("framework") or "",
                "framework_version": r.get("framework_version") or "",
                "control_family": r.get("control_family") or "",
                "maturity_level": r.get("maturity_level"),
                "requirement_text": requirement_text,
                "guidance_text": (r.get("guidance_text") or "").strip(),
                "source_uri": r.get("source_uri") or "",
                "score": float(score) if score is not None else 0.0,
            }
        )
    return items


def _controls_search(
    question: str,
    retrieve_k: int,
    *,
    use_semantic: bool,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Retrieve requirement records from the dedicated controls index.

    Resilient: falls back from semantic to keyword on FeatureNotSupported, and
    returns empty results (not an exception) for any other search failure so the
    query can still proceed with grounding-index context alone.
    """
    timings: dict[str, float] = {}
    timings["controls_semantic_enabled"] = 1.0 if use_semantic else 0.0

    t0 = time.perf_counter()
    try:
        items = _fetch_controls(question, retrieve_k, use_semantic)
    except Exception as e:
        # Fall back to keyword search when semantic is unavailable on this tier.
        if use_semantic and "SemanticQueriesNotAvailable" in str(e):
            try:
                items = _fetch_controls(question, retrieve_k, use_semantic=False)
            except Exception:
                items = []
        else:
            items = []
    timings["controls_search_s"] = round(time.perf_counter() - t0, 3)
    return items, timings


def _chat_completion(messages: list[dict[str, str]], deployment: str, temperature: float, timeout: int = 45) -> str:
    """Call Azure Foundry chat completion API using the OpenAI Python SDK."""
    try:
        from openai import AzureOpenAI
    except ImportError as exc:
        raise RuntimeError("openai package is required for Foundry API integration") from exc
    
    # Use Foundry API via Azure SDK
    client = AzureOpenAI(
        api_key=credential.get_token("https://cognitiveservices.azure.com/.default").token,
        api_version="2024-08-01-preview",
        azure_endpoint=config.openai_endpoint,
    )
    typed_messages = cast("list[ChatCompletionMessageParam]", messages)
    
    response = client.chat.completions.create(
        model=deployment,
        messages=typed_messages,
        max_completion_tokens=600,
        temperature=temperature,
        timeout=timeout,
    )
    return (response.choices[0].message.content or "").strip()


def _evaluate(question: str, context: str, answer: str) -> dict[str, Any]:
    eval_messages = [
        {"role": "system", "content": EVALUATOR_PROMPT},
        {
            "role": "user",
            "content": (
                f"Question:\n{question}\n\n"
                f"Context:\n{context}\n\n"
                f"Answer:\n{answer}\n\n"
                "Return JSON only."
            ),
        },
    ]
    raw = _chat_completion(eval_messages, deployment=config.evaluator_deployment, temperature=1.0, timeout=40)
    return _parse_eval(raw)


def _run_rag(
    question: str,
    retrieve_k: int,
    temperature: float,
    controls_semantic: bool,
    conversation_history: list[ConversationMessage] | None = None,
    feedback_context: str = "",
) -> dict[str, Any]:
    started = time.perf_counter()
    chunks, retrieval_timings = _hybrid_search(question, retrieve_k=retrieve_k)
    controls, controls_timings = _controls_search(
        question,
        retrieve_k=config.controls_top_k,
        use_semantic=controls_semantic,
    )

    if not chunks and not controls:
        return {
            "answer": "No relevant chunks were found in the index.",
            "results": [],
            "controls_results": [],
            "evaluation": {"acceptable": False, "score": 0.0, "reason": "No search context returned."},
            "iterations": 1,
            "metrics": {
                **retrieval_timings,
                **controls_timings,
                "rag_retrieval_s": round(retrieval_timings.get("embedding_s", 0.0) + retrieval_timings.get("search_s", 0.0), 3),
                "llm_reply_s": 0.0,
                "evaluator_s": 0.0,
                "llm_retry_s": 0.0,
                "llm_total_s": 0.0,
                "total_s": round(time.perf_counter() - started, 3),
            },
        }

    evidence_context = "\n\n".join(
        f"Source: {c['source_name']}\nExcerpt: {c['content'][:1500]}"
        for c in chunks
    )

    controls_context = "\n\n".join(
        (
            f"Requirement ID: {c['requirement_id']}\n"
            f"Framework: {c['framework']} {c['framework_version']}\n"
            f"Control Family: {c['control_family']}\n"
            f"Maturity Level: {c['maturity_level']}\n"
            f"Requirement: {c['requirement_text'][:1200]}\n"
            f"Guidance: {c['guidance_text'][:800]}"
        )
        for c in controls
    )

    context_sections: list[str] = []
    if controls_context:
        context_sections.append("Controls context (authoritative requirements):\n" + controls_context)
    if evidence_context:
        context_sections.append("Evidence context (implementation artifacts):\n" + evidence_context)
    context = "\n\n".join(context_sections)

    messages = [{"role": "system", "content": CYBER_PERSONA_PROMPT}]

    if feedback_context.strip():
        messages.append(
            {
                "role": "system",
                "content": (
                    "Use this user feedback to improve quality and relevance while staying grounded in retrieved context.\n"
                    f"{feedback_context}"
                ),
            }
        )

    if conversation_history:
        for m in conversation_history:
            if m.role in ("user", "assistant"):
                messages.append({"role": m.role, "content": m.content})

    messages.append(
        {
            "role": "user",
            "content": (
                f"Question:\n{question}\n\n"
                "Grounding context:\n"
                f"{context}\n\n"
                "Respond in markdown with short, practical cyber-security guidance and cite source names inline."
            ),
        }
    )

    t_llm = time.perf_counter()
    answer = _unwrap_answer(_chat_completion(messages, deployment=config.query_deployment, temperature=temperature))
    llm_reply_s = round(time.perf_counter() - t_llm, 3)

    t_eval = time.perf_counter()
    evaluation = _evaluate(question, context, answer)
    evaluator_s = round(time.perf_counter() - t_eval, 3)

    llm_retry_s = 0.0
    iterations = 2
    acceptable = bool(evaluation.get("acceptable", False))
    score = float(evaluation.get("score", 0.0))

    if (not acceptable) or score < config.evaluation_threshold:
        retry_reason = str(evaluation.get("reason", "Quality below threshold.")).strip()
        messages.extend(
            [
                {"role": "assistant", "content": answer},
                {
                    "role": "user",
                    "content": (
                        "The previous response was below acceptable threshold. "
                        f"Evaluator reason: {retry_reason}\n\n"
                        "Amend the response to improve grounding, relevance, and precision."
                    ),
                },
            ]
        )

        t_retry = time.perf_counter()
        answer = _chat_completion(messages, deployment=config.query_deployment, temperature=temperature)
        llm_retry_s = round(time.perf_counter() - t_retry, 3)

        # Re-evaluate final answer and expose reason used for retry.
        t_eval2 = time.perf_counter()
        evaluation = _evaluate(question, context, answer)
        evaluator_s = round(evaluator_s + (time.perf_counter() - t_eval2), 3)
        evaluation["retry_reason"] = retry_reason
        iterations = 3

    rag_retrieval_s = round(retrieval_timings.get("embedding_s", 0.0) + retrieval_timings.get("search_s", 0.0), 3)
    llm_total_s = round(llm_reply_s + llm_retry_s, 3)

    metrics = {
        **retrieval_timings,
        **controls_timings,
        "rag_retrieval_s": rag_retrieval_s,
        "llm_reply_s": llm_reply_s,
        "evaluator_s": evaluator_s,
        "llm_retry_s": llm_retry_s,
        "llm_total_s": llm_total_s,
        "total_s": round(time.perf_counter() - started, 3),
    }

    return {
        "answer": answer,
        "results": chunks,
        "controls_results": controls,
        "evaluation": evaluation,
        "iterations": iterations,
        "metrics": metrics,
    }


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "service": "rag-query-web",
            "index": config.search_index_name,
            "controls_index": config.controls_index_name,
            "controls_semantic_default": config.controls_semantic_default,
            "auth_enabled": bool(config.auth_token),
            "entra_group_auth_enabled": bool(config.required_group_object_id),
        }
    )


@app.get("/api/index-status")
def index_status() -> JSONResponse:
    """Diagnostic endpoint — returns document counts and reachability for both indexes."""
    def _probe(client: SearchClient, index_name: str) -> dict[str, Any]:
        try:
            pager = client.search(search_text="*", top=1, include_total_count=True)
            results = list(pager)
            # get_count() is on the pager object, available after first iteration
            count = pager.get_count() if hasattr(pager, "get_count") else ("1+" if results else 0)
            return {"reachable": True, "document_count": count}
        except Exception as exc:
            return {"reachable": False, "error": str(exc)}

    return JSONResponse(
        {
            "grounding_index": {"name": config.search_index_name, **_probe(search_client, config.search_index_name)},
            "controls_index": {"name": config.controls_index_name, **_probe(controls_search_client, config.controls_index_name)},
        }
    )


@app.get("/api/config")
def api_config() -> JSONResponse:
    return JSONResponse(
        {
            "search_index_name": config.search_index_name,
            "controls_index_name": config.controls_index_name,
            "embedding_deployment": config.embedding_deployment,
            "query_deployment": config.query_deployment,
            "evaluator_deployment": config.evaluator_deployment,
            "default_top_k": config.search_top_k,
            "controls_top_k": config.controls_top_k,
            "controls_semantic_default": config.controls_semantic_default,
            "controls_semantic_configuration_name": config.controls_semantic_configuration_name,
            "default_temperature": config.default_temperature,
            "evaluation_threshold": config.evaluation_threshold,
            "auth_enabled": bool(config.auth_token),
            "entra_group_auth_enabled": bool(config.required_group_object_id),
        }
    )


@app.get("/api/conversations/{user_id}")
def get_conversations(request: Request, user_id: str, auth_token: str = "") -> JSONResponse:
    """List all conversations for a user."""
    if not _is_authorized_request(auth_token, request):
        return JSONResponse({"error": _unauthorized_message(request)}, status_code=401)

    if not conversations_container:
        return JSONResponse({"conversations": []})
    
    try:
        query = "SELECT c.session_id, c.conversation_id, c.created_at, c.updated_at, c.messages FROM c WHERE c.user_id = @user_id AND c.type = 'conversation' ORDER BY c.updated_at DESC"
        items = list(conversations_container.query_items(
            query=query,
            parameters=[{"name": "@user_id", "value": user_id}],
        ))
        return JSONResponse({"conversations": items})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/conversations/{user_id}/{conversation_id}")
def get_conversation_history(request: Request, user_id: str, conversation_id: str, auth_token: str = "") -> JSONResponse:
    """Get full conversation history."""
    if not _is_authorized_request(auth_token, request):
        return JSONResponse({"error": _unauthorized_message(request)}, status_code=401)

    try:
        session = _load_conversation(user_id, conversation_id)
        return JSONResponse({
            "session_id": session.session_id,
            "conversation_id": session.conversation_id,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "messages": [
                {"role": m.role, "content": m.content, "timestamp": m.timestamp}
                for m in session.messages
            ],
            "response_ratings": [
                {
                    "rating": r.rating,
                    "todo": r.todo,
                    "assistant_timestamp": r.assistant_timestamp,
                    "timestamp": r.timestamp,
                }
                for r in session.response_ratings
            ],
        })
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/conversations/new")
def create_conversation(request: Request, auth_token: str = Form("")) -> JSONResponse:
    """Create a new conversation session."""
    if not _is_authorized_request(auth_token, request):
        return JSONResponse({"error": _unauthorized_message(request)}, status_code=401)

    session_id = str(uuid.uuid4())
    conversation_id = str(uuid.uuid4())
    user_id = _get_user_id(auth_token, session_id)
    
    try:
        session = ConversationSession(
            session_id=session_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        _save_conversation(session)

        return JSONResponse({
            "session_id": session_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
        })
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/conversations/{conversation_id}/message")
def add_message_to_conversation(
    request: Request,
    conversation_id: str,
    user_id: str = Form(...),
    role: str = Form(...),
    content: str = Form(...),
    auth_token: str = Form(""),
) -> JSONResponse:
    """Add a message to a conversation and optionally get a response."""
    if not _is_authorized_request(auth_token, request):
        return JSONResponse({"error": _unauthorized_message(request)}, status_code=401)

    try:
        session = _load_conversation(user_id, conversation_id)
        session.messages.append(ConversationMessage(role=role, content=content))
        session.updated_at = _utc_now_iso()
        _save_conversation(session)

        return JSONResponse({
            "message_id": len(session.messages),
            "timestamp": session.messages[-1].timestamp,
            "updated_at": session.updated_at,
        })
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/conversations/{conversation_id}/rating")
def add_response_rating(
    request: Request,
    conversation_id: str,
    user_id: str = Form(...),
    rating: int = Form(...),
    todo: str = Form(default=""),
    assistant_timestamp: str = Form(default=""),
    auth_token: str = Form(""),
) -> JSONResponse:
    """Store user rating/TODO feedback for a prior assistant response."""
    if not _is_authorized_request(auth_token, request):
        return JSONResponse({"error": _unauthorized_message(request)}, status_code=401)

    if rating < 1 or rating > 5:
        return JSONResponse({"error": "rating must be between 1 and 5"}, status_code=400)

    try:
        session = _load_conversation(user_id, conversation_id)

        if assistant_timestamp:
            has_target = any(m.role == "assistant" and m.timestamp == assistant_timestamp for m in session.messages)
            if not has_target:
                return JSONResponse({"error": "assistant message not found for assistant_timestamp"}, status_code=404)

        session.response_ratings.append(
            ResponseRating(
                rating=rating,
                todo=todo.strip(),
                assistant_timestamp=assistant_timestamp.strip(),
            )
        )
        session.updated_at = _utc_now_iso()
        _save_conversation(session)

        return JSONResponse(
            {
                "ratings_count": len(session.response_ratings),
                "updated_at": session.updated_at,
            }
        )
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)

@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    if not _is_authorized_request("", request):
        return HTMLResponse(content=_unauthorized_message(request), status_code=401)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "question": "",
            "answer": "",
            "results": [],
            "controls_results": [],
            "error": "",
            "evaluation": None,
            "metrics": None,
            "iterations": None,
            "retrieve_k": config.search_top_k,
            "temperature": config.default_temperature,
            "controls_semantic": config.controls_semantic_default,
            "auth_token": "",
            "index_name": config.search_index_name,
            "embedding_deployment": config.embedding_deployment,
            "query_deployment": config.query_deployment,
            "evaluation_threshold": config.evaluation_threshold,
            "auth_enabled": bool(config.auth_token),
            "user_id": "",
            "session_id": "",
            "conversation_id": "",
        },
    )


@app.post("/ask", response_class=HTMLResponse)
def ask(
    request: Request,
    question: str = Form(...),
    retrieve_k: int = Form(...),
    temperature: float = Form(...),
    controls_semantic: str = Form(""),
    auth_token: str = Form(""),
    session_id: str = Form(default=""),
    conversation_id: str = Form(default=""),
) -> HTMLResponse:
    user_id = _get_user_id(auth_token, session_id)
    session = None

    if not _is_authorized_request(auth_token, request):
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "question": question,
                "answer": "",
                "results": [],
                "controls_results": [],
                "error": _unauthorized_message(request),
                "evaluation": None,
                "metrics": None,
                "iterations": None,
                "retrieve_k": retrieve_k,
                "temperature": temperature,
                "controls_semantic": _form_bool(controls_semantic, default=config.controls_semantic_default),
                "auth_token": "",
                "index_name": config.search_index_name,
                "embedding_deployment": config.embedding_deployment,
                "query_deployment": config.query_deployment,
                "evaluation_threshold": config.evaluation_threshold,
                "auth_enabled": bool(config.auth_token),
                "user_id": user_id,
                "session_id": session_id,
                "conversation_id": conversation_id,
            },
            status_code=401,
        )

    if session_id and conversation_id:
        session = _load_conversation(user_id, conversation_id)

    retrieve_k = max(1, min(20, retrieve_k))
    temperature = max(0, min(1.0, temperature))
    controls_semantic_enabled = _form_bool(controls_semantic, default=config.controls_semantic_default)

    try:
        conversation_history = session.messages if session else []
        feedback_context = _build_feedback_context(session) if session else ""

        result = _run_rag(
            question=question,
            retrieve_k=retrieve_k,
            temperature=temperature,
            controls_semantic=controls_semantic_enabled,
            conversation_history=conversation_history,
            feedback_context=feedback_context,
        )
        
        # Add user and assistant messages to conversation history
        if session:
            session.messages.append(ConversationMessage(role="user", content=question))
            session.messages.append(ConversationMessage(role="assistant", content=result["answer"]))
            session.updated_at = _utc_now_iso()
            _save_conversation(session)
        
        error = ""
    except Exception as exc:
        result = {
            "answer": "",
            "results": [],
            "controls_results": [],
            "evaluation": None,
            "metrics": None,
            "iterations": None,
        }
        error = str(exc)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "question": question,
            "answer": result["answer"],
            "results": result["results"],
            "controls_results": result.get("controls_results", []),
            "error": error,
            "evaluation": result["evaluation"],
            "metrics": result["metrics"],
            "iterations": result["iterations"],
            "retrieve_k": retrieve_k,
            "temperature": temperature,
            "controls_semantic": controls_semantic_enabled,
            "auth_token": auth_token,
            "index_name": config.search_index_name,
            "embedding_deployment": config.embedding_deployment,
            "query_deployment": config.query_deployment,
            "evaluation_threshold": config.evaluation_threshold,
            "auth_enabled": bool(config.auth_token),
            "user_id": user_id,
            "session_id": session_id,
            "conversation_id": conversation_id,
        },
    )


@app.post("/api/ask", response_model=AskResponse)
def ask_api(request: Request, payload: AskRequest) -> AskResponse:
    question = payload.question.strip()
    if not question:
        return AskResponse(
            answer="",
            results=[],
            controls_results=[],
            evaluation=None,
            iterations=None,
            metrics=None,
            error="Question must not be empty.",
        )

    if not _is_authorized_request(payload.auth_token, request):
        return AskResponse(
            answer="",
            results=[],
            controls_results=[],
            evaluation=None,
            iterations=None,
            metrics=None,
            error=_unauthorized_message(request),
        )

    try:
        result = _run_rag(
            question=question,
            retrieve_k=payload.retrieve_k,
            temperature=payload.temperature,
            controls_semantic=(
                payload.controls_semantic
                if payload.controls_semantic is not None
                else config.controls_semantic_default
            ),
        )
        return AskResponse(
            answer=result["answer"],
            results=result["results"],
            controls_results=result.get("controls_results", []),
            evaluation=result["evaluation"],
            iterations=result["iterations"],
            metrics=result["metrics"],
            error="",
        )
    except Exception as exc:
        return AskResponse(
            answer="",
            results=[],
            controls_results=[],
            evaluation=None,
            iterations=None,
            metrics=None,
            error=str(exc),
        )
