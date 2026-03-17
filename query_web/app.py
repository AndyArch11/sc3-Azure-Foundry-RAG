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
        default_temperature=float(os.getenv("DEFAULT_TEMPERATURE", "0.2")),
        evaluation_threshold=float(os.getenv("ACCEPTABLE_SCORE_THRESHOLD", "0.72")),
        auth_token=os.getenv("QUERY_WEB_AUTH_TOKEN", "").strip(),
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


class AskRequest(BaseModel):
    question: str
    retrieve_k: int = Field(default=5, ge=1, le=20)
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    auth_token: str = ""


class AskResponse(BaseModel):
    answer: str
    results: list[dict[str, Any]]
    evaluation: dict[str, Any] | None
    iterations: int | None
    metrics: dict[str, float] | None
    error: str


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
    token = _cognitive_token()
    url = (
        f"{config.openai_endpoint}/openai/deployments/"
        f"{deployment}/chat/completions?api-version=2024-10-21"
    )
    body = {
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 600,
    }
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    return (payload["choices"][0]["message"]["content"] or "").strip()


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
    raw = _chat_completion(eval_messages, deployment=config.evaluator_deployment, temperature=0.0, timeout=40)
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
) -> HTMLResponse:
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
            },
            status_code=401,
        )

    retrieve_k = max(1, min(20, retrieve_k))
    temperature = max(0.0, min(1.0, temperature))

    try:
        result = _run_rag(question=question, retrieve_k=retrieve_k, temperature=temperature)
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
