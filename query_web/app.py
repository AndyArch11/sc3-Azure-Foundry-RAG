from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

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


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class QueryConfig:
    search_endpoint: str
    search_index_name: str
    openai_endpoint: str
    embedding_deployment: str
    query_deployment: str
    evaluator_deployment: str
    search_top_k: int
    default_temperature: float
    evaluation_threshold: float
    auth_token: str

    cosmos_endpoint: str
    cosmos_database_name: str
    cosmos_container_name: str

def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable not set: {name}")
    return value


def load_config() -> QueryConfig:
    return QueryConfig(
        search_endpoint=_require_env("AZURE_SEARCH_ENDPOINT"),
        search_index_name=os.getenv("AZURE_SEARCH_INDEX_NAME", "grounding-index"),
        openai_endpoint=_require_env("AZURE_OPENAI_ENDPOINT"),
        embedding_deployment=os.getenv("EMBEDDING_DEPLOYMENT_NAME", "text-embedding-ada-002"),
        query_deployment=os.getenv("QUERY_DEPLOYMENT_NAME", "gpt-5.1-chat"),
        evaluator_deployment=os.getenv("EVALUATOR_DEPLOYMENT_NAME", "gpt-4.1-mini"),
        search_top_k=int(os.getenv("SEARCH_TOP_K", "5")),
        default_temperature=float(os.getenv("DEFAULT_TEMPERATURE", "1")),
        evaluation_threshold=float(os.getenv("ACCEPTABLE_SCORE_THRESHOLD", "0.72")),
        auth_token=os.getenv("QUERY_WEB_AUTH_TOKEN", "").strip(),
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
class ConversationSession:
    """Conversation session stored in CosmosDB."""
    session_id: str
    user_id: str  # auth_token hash or session token
    conversation_id: str  # unique per conversation
    messages: list[ConversationMessage] = field(default_factory=list)
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
        return ConversationSession(
            session_id=data["session_id"],
            user_id=data["user_id"],
            conversation_id=data["conversation_id"],
            messages=messages,
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
    try:
        data = json.loads(text)
        acceptable = bool(data.get("acceptable", False))
        score = max(0.0, min(1.0, float(data.get("score", 0.0))))
        reason = str(data.get("reason", "No reason provided.")).strip()
        return {"acceptable": acceptable, "score": score, "reason": reason}
    except Exception:
        return _json_fallback_eval()


app = FastAPI(title="RAG Query Console")
templates = Jinja2Templates(directory="templates")
credential = DefaultAzureCredential()
config = load_config()
search_client = SearchClient(
    endpoint=config.search_endpoint,
    index_name=config.search_index_name,
    credential=credential,
)

# Initialize CosmosDB client
try:
    from azure.cosmos import CosmosClient
    from azure.cosmos.exceptions import CosmosResourceNotFoundError
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


class AskResponse(BaseModel):
    answer: str
    results: list[dict[str, Any]]
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
        conversations_container.upsert_item(session.to_dict(), partition_key=session.user_id)
    except Exception as exc:
        raise RuntimeError(f"Conversation persistence write failed: {exc}") from exc

def _cognitive_token() -> str:
    return credential.get_token("https://cognitiveservices.azure.com/.default").token


def _is_authorized(auth_token: str) -> bool:
    if not config.auth_token:
        return True
    return auth_token.strip() == config.auth_token


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
    results = search_client.search(
        search_text=question,
        vector_queries=[vector_query],
        top=retrieve_k,
        select=["content", "source_name", "source_path"],
    )
    timings["search_s"] = round(time.perf_counter() - t1, 3)

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
    
    response = client.chat.completions.create(
        model=deployment,
        messages=messages,
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


def _run_rag(question: str, retrieve_k: int, temperature: float) -> dict[str, Any]:
    started = time.perf_counter()
    chunks, retrieval_timings = _hybrid_search(question, retrieve_k=retrieve_k)

    if not chunks:
        return {
            "answer": "No relevant chunks were found in the index.",
            "results": [],
            "evaluation": {"acceptable": False, "score": 0.0, "reason": "No search context returned."},
            "iterations": 1,
            "metrics": {
                **retrieval_timings,
                "rag_retrieval_s": round(retrieval_timings.get("embedding_s", 0.0) + retrieval_timings.get("search_s", 0.0), 3),
                "llm_reply_s": 0.0,
                "evaluator_s": 0.0,
                "llm_retry_s": 0.0,
                "llm_total_s": 0.0,
                "total_s": round(time.perf_counter() - started, 3),
            },
        }

    context = "\n\n".join(
        f"Source: {c['source_name']}\nExcerpt: {c['content'][:1500]}"
        for c in chunks
    )

    messages = [
        {"role": "system", "content": CYBER_PERSONA_PROMPT},
        {
            "role": "user",
            "content": (
                f"Question:\n{question}\n\n"
                "Grounding context:\n"
                f"{context}\n\n"
                "Respond in markdown with short, practical cyber-security guidance and cite source names inline."
            ),
        },
    ]
    # Add conversation history if available
    if hasattr(_run_rag, "_current_messages") and _run_rag._current_messages:  # type: ignore
        messages = _run_rag._current_messages + messages  # type: ignore

    t_llm = time.perf_counter()
    answer = _chat_completion(messages, deployment=config.query_deployment, temperature=temperature)
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
            "auth_enabled": bool(config.auth_token),
        }
    )


@app.get("/api/config")
def api_config() -> JSONResponse:
    return JSONResponse(
        {
            "search_index_name": config.search_index_name,
            "embedding_deployment": config.embedding_deployment,
            "query_deployment": config.query_deployment,
            "evaluator_deployment": config.evaluator_deployment,
            "default_top_k": config.search_top_k,
            "default_temperature": config.default_temperature,
            "evaluation_threshold": config.evaluation_threshold,
            "auth_enabled": bool(config.auth_token),
        }
    )


@app.get("/api/conversations/{user_id}")
def get_conversations(user_id: str, auth_token: str = "") -> JSONResponse:
    """List all conversations for a user."""
    if not _is_authorized(auth_token):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
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
def get_conversation_history(user_id: str, conversation_id: str, auth_token: str = "") -> JSONResponse:
    """Get full conversation history."""
    if not _is_authorized(auth_token):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
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
        })
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/conversations/new")
def create_conversation(auth_token: str = Form("")) -> JSONResponse:
    """Create a new conversation session."""
    if not _is_authorized(auth_token):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
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
    conversation_id: str,
    user_id: str = Form(...),
    role: str = Form(...),
    content: str = Form(...),
    auth_token: str = Form(""),
) -> JSONResponse:
    """Add a message to a conversation and optionally get a response."""
    if not _is_authorized(auth_token):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
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

@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "question": "",
            "answer": "",
            "results": [],
            "error": "",
            "evaluation": None,
            "metrics": None,
            "iterations": None,
            "retrieve_k": config.search_top_k,
            "temperature": config.default_temperature,
            "auth_token": "",
            "index_name": config.search_index_name,
            "embedding_deployment": config.embedding_deployment,
            "query_deployment": config.query_deployment,
            "evaluation_threshold": config.evaluation_threshold,
            "auth_enabled": bool(config.auth_token),
        },
    )


@app.post("/ask", response_class=HTMLResponse)
def ask(
    request: Request,
    question: str = Form(...),
    retrieve_k: int = Form(...),
    temperature: float = Form(...),
    auth_token: str = Form(""),
    session_id: str = Form(default=""),
    conversation_id: str = Form(default=""),
) -> HTMLResponse:
    # Initialize conversation tracking if provided
    session = None
    if session_id and conversation_id:
        user_id = _get_user_id(auth_token, session_id)
        session = _load_conversation(user_id, conversation_id)
    
    if not _is_authorized(auth_token):
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "question": question,
                "answer": "",
                "results": [],
                "error": "Unauthorized. Provide a valid access token.",
                "evaluation": None,
                "metrics": None,
                "iterations": None,
                "retrieve_k": retrieve_k,
                "temperature": temperature,
                "auth_token": "",
                "index_name": config.search_index_name,
                "embedding_deployment": config.embedding_deployment,
                "query_deployment": config.query_deployment,
                "evaluation_threshold": config.evaluation_threshold,
                "auth_enabled": bool(config.auth_token),
                "session_id": session_id,
                "conversation_id": conversation_id,
            },
            status_code=401,
        )

    retrieve_k = max(1, min(20, retrieve_k))
    temperature = max(0, min(1.0, temperature))

    try:
        # Inject conversation history into RAG context if available
        if session and session.messages:
            prev_messages = [m for m in session.messages if m.role in ("user", "assistant")]
            _run_rag._current_messages = prev_messages  # type: ignore
        
        result = _run_rag(question=question, retrieve_k=retrieve_k, temperature=temperature)
        
        # Add user and assistant messages to conversation history
        if session:
            session.messages.append(ConversationMessage(role="user", content=question))
            session.messages.append(ConversationMessage(role="assistant", content=result["answer"]))
            session.updated_at = datetime.utcnow().isoformat()
            _save_conversation(session)
        
        error = ""
    except Exception as exc:
        result = {
            "answer": "",
            "results": [],
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
            "error": error,
            "evaluation": result["evaluation"],
            "metrics": result["metrics"],
            "iterations": result["iterations"],
            "retrieve_k": retrieve_k,
            "temperature": temperature,
            "auth_token": auth_token,
            "index_name": config.search_index_name,
            "embedding_deployment": config.embedding_deployment,
            "query_deployment": config.query_deployment,
            "evaluation_threshold": config.evaluation_threshold,
            "auth_enabled": bool(config.auth_token),
            "session_id": session_id,
            "conversation_id": conversation_id,
        },
    )


@app.post("/api/ask", response_model=AskResponse)
def ask_api(payload: AskRequest) -> AskResponse:
    question = payload.question.strip()
    if not question:
        return AskResponse(
            answer="",
            results=[],
            evaluation=None,
            iterations=None,
            metrics=None,
            error="Question must not be empty.",
        )

    if not _is_authorized(payload.auth_token):
        return AskResponse(
            answer="",
            results=[],
            evaluation=None,
            iterations=None,
            metrics=None,
            error="Unauthorized. Provide a valid access token.",
        )

    try:
        result = _run_rag(
            question=question,
            retrieve_k=payload.retrieve_k,
            temperature=payload.temperature,
        )
        return AskResponse(
            answer=result["answer"],
            results=result["results"],
            evaluation=result["evaluation"],
            iterations=result["iterations"],
            metrics=result["metrics"],
            error="",
        )
    except Exception as exc:
        return AskResponse(
            answer="",
            results=[],
            evaluation=None,
            iterations=None,
            metrics=None,
            error=str(exc),
        )
