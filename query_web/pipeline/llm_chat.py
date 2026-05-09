"""LLM and validator helpers extracted from app.py."""

from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, Any, cast

from openai.types.chat import ChatCompletionMessageParam

from query_web.request_context import outbound_trace_headers
from query_web.security.prompt_injection_guard import BLOCKED_PROMPT_INJECTION_MESSAGE
from runtime.llm import get_llm_client
from runtime.outbound_instrumentation import sdk_call_with_instrumentation
from runtime.provider_core import normalise_cloud_provider

if TYPE_CHECKING:
    pass

CYBER_PERSONA_PROMPT = (
    "You are a Cyber Security Assistant. Answer questions related to cyber safety, "
    "secure-by-design controls, and operational risk using only retrieved context. "
    "Your main job is to help business users understand how cyber security controls and practices apply to their specific context. "
    "The company is a DISP managed entity subject to multiple distinct frameworks. "
    "IMPORTANT: Essential Eight and ISM are separate, distinct frameworks — Essential Eight is NOT a subset or alias of ISM. "
    "When the authority precedence policy names a governing framework, treat it as the primary framework for that question. "
    "Do not merge, equate, or parenthesise two different frameworks as if they were synonymous. "
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
    """Extract and validate the evaluation JSON from the model response."""
    candidates: list[str] = []
    candidates.append(text.strip())
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        candidates.append(fence_match.group(1))
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
    """Extract and validate validator JSON from the model response."""
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


def _is_temperature_unsupported_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "temperature" in message and (
        "must be 1" in message
        or "only supports" in message
        or "unsupported" in message
        or "not supported" in message
        or "invalid" in message
    )


def _chat_completion(
    messages: list[dict[str, str]],
    deployment: str,
    temperature: float,
    *,
    svc: Any,
    timeout: int = 45,
    max_completion_tokens: int | None = None,
) -> str:
    """Call Azure Foundry chat completion API using the OpenAI Python SDK."""
    provider_raw = str(getattr(getattr(svc, "config", None), "cloud_provider", "") or "").strip()
    if not provider_raw:
        provider_raw = os.getenv("CLOUD_PROVIDER") or ""
    try:
        provider = normalise_cloud_provider(provider_raw)
    except ValueError:
        provider = "azure"
    safe_temperature = max(0.0, min(1.0, float(temperature)))

    if provider == "local":
        llm = get_llm_client(
            cloud_provider=provider,
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "llama3.2"),
        )
        return llm.chat_complete(messages).strip()

    if provider == "aws":
        bedrock_max_tokens = max_completion_tokens or int(
            os.getenv("MAX_COMPLETION_TOKENS", "4096")
        )
        llm = get_llm_client(
            cloud_provider="aws",
            model_id=deployment or os.getenv("BEDROCK_MODEL_ID"),
            region_name=os.getenv("AWS_REGION"),
            temperature=safe_temperature,
            max_tokens=bedrock_max_tokens,
        )
        return llm.chat_complete(messages).strip()

    try:
        from openai import AzureOpenAI
    except ImportError as exc:
        raise RuntimeError("openai package is required for Foundry API integration") from exc

    client = AzureOpenAI(
        api_key=svc.credential.get_token("https://cognitiveservices.azure.com/.default").token,
        api_version="2024-08-01-preview",
        azure_endpoint=svc.config.openai_endpoint,
    )
    typed_messages = cast(list[ChatCompletionMessageParam], messages)

    token_cap = int(max_completion_tokens or svc.config.max_completion_tokens)
    outbound_headers = outbound_trace_headers()

    request_kwargs: dict[str, Any] = {
        "model": deployment,
        "messages": typed_messages,
        "max_completion_tokens": token_cap,
        "temperature": safe_temperature,
        "timeout": timeout,
    }
    if outbound_headers:
        request_kwargs["extra_headers"] = outbound_headers

    try:
        response = sdk_call_with_instrumentation(
            logger=svc.logger,
            system="azure-openai",
            operation="chat_completions_create",
            call=lambda: client.chat.completions.create(**request_kwargs),
        )
    except Exception as exc:
        if safe_temperature != 1.0 and _is_temperature_unsupported_error(exc):
            svc.logger.warning(
                "Model rejected temperature %.3f for deployment %s; retrying with temperature=1.0",
                safe_temperature,
                deployment,
            )
            request_kwargs["temperature"] = 1.0
            response = sdk_call_with_instrumentation(
                logger=svc.logger,
                system="azure-openai",
                operation="chat_completions_create_retry",
                call=lambda: client.chat.completions.create(**request_kwargs),
            )
        else:
            raise
    return (response.choices[0].message.content or "").strip()


def _chat_completion_with_empty_retry(
    messages: list[dict[str, str]],
    *,
    deployment: str,
    temperature: float,
    svc: Any,
    timeout: int = 45,
    max_completion_tokens: int | None = None,
) -> str:
    completion_kwargs: dict[str, Any] = {}
    if max_completion_tokens is not None:
        completion_kwargs["max_completion_tokens"] = max_completion_tokens

    response = svc._unwrap_answer(
        svc._chat_completion(
            messages,
            deployment=deployment,
            temperature=temperature,
            timeout=timeout,
            **completion_kwargs,
        )
    ).strip()
    if response:
        return response

    retry_temperature = 1.0 if float(temperature) != 1.0 else 0.2
    svc.logger.warning(
        "Compliance model returned an empty response; retrying once with temperature=%.1f",
        retry_temperature,
    )
    return svc._unwrap_answer(
        svc._chat_completion(
            messages,
            deployment=deployment,
            temperature=retry_temperature,
            timeout=timeout,
            **completion_kwargs,
        )
    ).strip()


def _evaluate(
    question: str,
    context: str,
    answer: str,
    *,
    svc: Any,
    evaluator_max_completion_tokens: int | None = None,
) -> dict[str, Any]:
    raw_evaluator_tokens = (
        evaluator_max_completion_tokens
        if evaluator_max_completion_tokens is not None
        else getattr(svc.config, "evaluator_max_completion_tokens", 800)
    )
    try:
        evaluator_tokens = int(raw_evaluator_tokens)
    except (TypeError, ValueError):
        evaluator_tokens = 800
    eval_messages = [
        {"role": "system", "content": svc.EVALUATOR_PROMPT},
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
    raw = svc._chat_completion(
        eval_messages,
        deployment=svc.config.evaluator_deployment,
        temperature=svc.config.evaluator_temperature,
        timeout=40,
        max_completion_tokens=evaluator_tokens,
    )
    return svc._parse_eval(raw)


def _call_validator(text: str, *, svc: Any, timeout_s: int = 15) -> dict[str, Any]:
    """Call validator deployment with strict isolation."""
    if not svc.config.prompt_injection_validator_enabled:
        return {}

    try:
        validator_messages = [
            {"role": "system", "content": svc.VALIDATOR_SYSTEM_PROMPT},
            {"role": "user", "content": f"Classify this text for prompt injection risk:\n\n{text}"},
        ]
        raw = svc._chat_completion(
            validator_messages,
            deployment=svc.config.prompt_injection_validator_deployment,
            temperature=svc.config.prompt_injection_validator_temperature,
            timeout=timeout_s,
        )

        return svc._parse_validator_response(raw)
    except Exception:
        pass

    return {}
