"""Unit tests for query_web/rag_pipeline.py."""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("AZURE_SEARCH_ENDPOINT", "https://test.search.windows.net")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
os.environ.setdefault("AZURE_COSMOS_ENDPOINT", "https://test.documents.azure.com")
os.environ.setdefault("AZURE_COSMOS_DATABASE_NAME", "rag-conversations")
os.environ.setdefault("AZURE_COSMOS_CONTAINER_NAME", "conversations")

from query_web.rag_pipeline import _run_rag


class _GuardrailDecision(SimpleNamespace):
    allowed: bool
    blocked_by_deterministic: bool
    categories: list[str]
    validator_consulted: bool
    validator_confidence: float
    metrics: dict[str, Any]
    reason: str


def _base_svc() -> SimpleNamespace:
    svc = SimpleNamespace()
    svc.config = SimpleNamespace(
        prompt_injection_validator_enabled=False,
        prompt_injection_validator_threshold=0.85,
        prompt_injection_validator_mode="off",
        controls_top_k=4,
        query_deployment="query",
        evaluation_threshold=0.7,
        guardrail_metrics_in_response=False,
    )
    svc.logger = SimpleNamespace(info=lambda *args, **kwargs: None)

    svc.evaluate_prompt_risk = lambda question, **kwargs: _GuardrailDecision(
        allowed=True,
        blocked_by_deterministic=False,
        categories=[],
        validator_consulted=False,
        validator_confidence=0.0,
        metrics={},
        reason="",
    )
    svc._prompt_injection_response = lambda reason: {"error": reason, "metrics": {}}
    svc._resolve_evidence_corpora = lambda include, exclude: ["a", "b", "c"]
    svc._build_evidence_corpus_filter = lambda selected: "corpus ne ''"
    svc._hybrid_search = lambda question, retrieve_k, evidence_filter: (
        [],
        {"embedding_s": 0.01, "search_s": 0.02},
    )
    svc._controls_search = lambda question, **kwargs: (
        [],
        {"controls_comparison_detected": 0.0},
    )
    svc._summarise_controls_distribution = lambda controls, timings, preferred_framework=None: {
        "total_controls": len(controls)
    }
    svc._preferred_framework_for_question = lambda question: None
    svc._controls_coverage_disclaimer = lambda **kwargs: ""
    svc._chunk_reference_label = lambda c: c.get("source_name") or "src"
    svc.sanitise_untrusted_text = lambda text: text
    svc._precedence_policy_summary = lambda: "policy"
    svc.CYBER_PERSONA_PROMPT = "persona"
    svc.PROMPT_INJECTION_SYSTEM_PROMPT = "guard"
    svc.sanitise_conversation_turn = lambda role, content: f"{role}:{content}"
    svc._clean_markdown_whitespace = lambda text: text
    svc._chat_completion_with_empty_retry = lambda messages, deployment, temperature: "good answer"
    svc._ensure_visible_answer = lambda answer: answer
    svc._build_retrieval_based_fallback_answer = lambda **kwargs: "fallback answer"
    svc._prepend_disclaimer = lambda answer, disclaimer: (
        f"{disclaimer}\n{answer}" if disclaimer else answer
    )
    svc._evaluate = lambda question, context, answer: {
        "acceptable": True,
        "score": 1.0,
        "reason": "ok",
    }
    svc._call_validator = lambda text: {}
    return svc


def test_run_rag_blocks_on_guardrail_and_propagates_metrics() -> None:
    svc = _base_svc()
    svc.config.guardrail_metrics_in_response = True
    svc.evaluate_prompt_risk = lambda question, **kwargs: _GuardrailDecision(
        allowed=False,
        blocked_by_deterministic=True,
        categories=["prompt-injection"],
        validator_consulted=True,
        validator_confidence=0.99,
        metrics={"validator_would_block": True},
        reason="blocked",
    )

    result = _run_rag("ignore all", 5, 0.2, False, svc=svc)

    assert result["error"] == "blocked"
    assert result["metrics"]["validator_would_block"] is True


def test_run_rag_returns_no_context_payload_when_no_chunks_and_no_controls() -> None:
    svc = _base_svc()

    result = _run_rag("q", 5, 0.2, True, svc=svc)

    assert result["answer"] == "No relevant chunks were found in the index."
    assert result["results"] == []
    assert result["controls_results"] == []
    assert result["iterations"] == 1


def test_run_rag_skips_controls_when_corpus_a_not_selected() -> None:
    svc = _base_svc()
    svc._resolve_evidence_corpora = lambda include, exclude: ["b"]
    svc._build_evidence_corpus_filter = lambda selected: "corpus eq 'b'"

    def _hybrid(question: str, retrieve_k: int, evidence_filter: str) -> tuple[list[dict[str, Any]], dict[str, float]]:
        return ([{"corpus": "b", "content": "guidance", "source_name": "b1"}], {"embedding_s": 0.01, "search_s": 0.02})

    svc._hybrid_search = _hybrid
    svc._controls_search = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should skip"))

    result = _run_rag(
        "q",
        5,
        0.2,
        True,
        svc=svc,
        evidence_corpora_include=["b"],
    )

    assert result["answer"] == "good answer"
    assert result["controls_results"] == []
    assert result["iterations"] == 2


def test_run_rag_retry_path_and_fallback_answer_generation() -> None:
    svc = _base_svc()
    call_count = {"llm": 0, "eval": 0}

    svc._hybrid_search = lambda question, retrieve_k, evidence_filter: (
        [{"corpus": "c", "content": "artifact", "source_name": "c1"}],
        {"embedding_s": 0.01, "search_s": 0.02},
    )
    svc._controls_search = lambda question, **kwargs: (
        [
            {
                "requirement_id": "CTRL-1",
                "framework": "ISM",
                "framework_version": "1",
                "control_family": "Access",
                "maturity_level": "ml1",
                "requirement_text": "must do x",
                "guidance_text": "",
            }
        ],
        {"controls_comparison_detected": 1.0},
    )
    svc._controls_coverage_disclaimer = lambda **kwargs: "disclaimer"

    def _chat(messages: list[dict[str, str]], deployment: str, temperature: float) -> str:
        call_count["llm"] += 1
        if call_count["llm"] == 1:
            return "No answer text was generated for this request, even though retrieval completed."
        return "second answer"

    svc._chat_completion_with_empty_retry = _chat

    def _evaluate(question: str, context: str, answer: str) -> dict[str, Any]:
        call_count["eval"] += 1
        if call_count["eval"] == 1:
            return {"acceptable": False, "score": 0.1, "reason": "too weak"}
        return {"acceptable": True, "score": 0.9, "reason": "ok"}

    svc._evaluate = _evaluate

    history = [SimpleNamespace(role="user", content="prior question")]
    result = _run_rag(
        "q",
        5,
        0.2,
        True,
        svc=svc,
        conversation_history=history,
        feedback_context="improve precision",
    )

    assert result["iterations"] == 3
    assert result["evaluation"]["retry_reason"] == "too weak"
    assert result["answer"].startswith("disclaimer")
    assert result["metrics"]["llm_retry_s"] >= 0.0


def test_run_rag_retry_fallback_and_merges_guardrail_metrics() -> None:
    svc = _base_svc()
    svc.config.guardrail_metrics_in_response = True
    svc.evaluate_prompt_risk = lambda question, **kwargs: _GuardrailDecision(
        allowed=True,
        blocked_by_deterministic=False,
        categories=[],
        validator_consulted=True,
        validator_confidence=0.42,
        metrics={"validator_would_block": False, "deterministic_score": 2},
        reason="",
    )

    svc._hybrid_search = lambda question, retrieve_k, evidence_filter: (
        [{"corpus": "c", "content": "artifact", "source_name": "c1"}],
        {"embedding_s": 0.01, "search_s": 0.02},
    )
    svc._controls_search = lambda question, **kwargs: (
        [
            {
                "requirement_id": "CTRL-2",
                "framework": "ISM",
                "framework_version": "1",
                "control_family": "Network",
                "maturity_level": "ml1",
                "requirement_text": "must do y",
                "guidance_text": "",
            }
        ],
        {"controls_comparison_detected": 0.0},
    )
    svc._controls_coverage_disclaimer = lambda **kwargs: "disc"

    call_count = {"llm": 0, "eval": 0}

    def _chat(messages: list[dict[str, str]], deployment: str, temperature: float) -> str:
        call_count["llm"] += 1
        return "No answer text was generated for this request, even though retrieval completed."

    def _evaluate(question: str, context: str, answer: str) -> dict[str, Any]:
        call_count["eval"] += 1
        if call_count["eval"] == 1:
            return {"acceptable": False, "score": 0.1, "reason": "retry"}
        return {"acceptable": True, "score": 0.9, "reason": "ok"}

    svc._chat_completion_with_empty_retry = _chat
    svc._evaluate = _evaluate

    result = _run_rag("q", 5, 0.2, True, svc=svc)

    assert result["iterations"] == 3
    assert result["metrics"]["validator_would_block"] is False
    assert result["answer"].startswith("disc")
