"""LLM and validator helpers extracted from app.py."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, cast

from query_web.security.prompt_injection_guard import BLOCKED_PROMPT_INJECTION_MESSAGE

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam

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
) -> str:
    """Call Azure Foundry chat completion API using the OpenAI Python SDK."""
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

    safe_temperature = max(0.0, min(1.0, float(temperature)))

    try:
        response = client.chat.completions.create(
            model=deployment,
            messages=typed_messages,
            max_completion_tokens=600,
            temperature=safe_temperature,
            timeout=timeout,
        )
    except Exception as exc:
        if safe_temperature != 1.0 and _is_temperature_unsupported_error(exc):
            svc.logger.warning(
                "Model rejected temperature %.3f for deployment %s; retrying with temperature=1.0",
                safe_temperature,
                deployment,
            )
            response = client.chat.completions.create(
                model=deployment,
                messages=typed_messages,
                max_completion_tokens=600,
                temperature=1.0,
                timeout=timeout,
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
) -> str:
    response = svc._unwrap_answer(
        svc._chat_completion(
            messages,
            deployment=deployment,
            temperature=temperature,
            timeout=timeout,
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
        )
    ).strip()


def _evaluate(question: str, context: str, answer: str, *, svc: Any) -> dict[str, Any]:
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
