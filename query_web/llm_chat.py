"""LLM and validator helpers extracted from app.py."""
from __future__ import annotations

from typing import Any, cast


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
    typed_messages = cast("list[ChatCompletionMessageParam]", messages)

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
