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

from query_web.pipeline.rag_pipeline import (
    _new_token_usage_accumulator,
    _record_token_usage,
    _run_rag,
)
from runtime.llm.token_usage import record_token_usage


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

    assert result["answer"].startswith("No selected corpus is currently in scope")
    assert result["results"] == []
    assert result["controls_results"] == []
    assert result["iterations"] == 1
    assert result["audit"]["scope_mode"] == "none_in_scope"


def test_run_rag_skips_controls_when_corpus_a_not_selected() -> None:
    svc = _base_svc()
    svc._resolve_evidence_corpora = lambda include, exclude: ["b"]
    svc._build_evidence_corpus_filter = lambda selected: "corpus eq 'b'"

    def _hybrid(
        question: str, retrieve_k: int, evidence_filter: str
    ) -> tuple[list[dict[str, Any]], dict[str, float]]:
        return (
            [{"corpus": "b", "content": "guidance", "source_name": "b1"}],
            {"embedding_s": 0.01, "search_s": 0.02},
        )

    svc._hybrid_search = _hybrid
    svc._controls_search = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("should skip")
    )

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


def test_run_rag_classifies_mixed_case_corpus_b_chunks() -> None:
    svc = _base_svc()
    captured_context: dict[str, str] = {}

    svc._resolve_evidence_corpora = lambda include, exclude: ["b", "c"]
    svc._build_evidence_corpus_filter = lambda selected: "|".join(selected)

    def _hybrid(
        question: str, retrieve_k: int, evidence_filter: str
    ) -> tuple[list[dict[str, Any]], dict[str, float]]:
        if evidence_filter == "b":
            return (
                [
                    {
                        "corpus": "B",
                        "corpus_role": "",
                        "content": "Viva secure by design guidance text.",
                        "source_name": "b-upper.pdf",
                    },
                    {
                        "corpus": "",
                        "corpus_role": "NARRATIVE_GUIDANCE",
                        "content": "Additional narrative guidance from role-only metadata.",
                        "source_name": "b-role.pdf",
                    },
                ],
                {"embedding_s": 0.01, "search_s": 0.02},
            )
        return ([], {"embedding_s": 0.01, "search_s": 0.02})

    svc._hybrid_search = _hybrid
    svc._controls_search = lambda question, **kwargs: ([], {"controls_comparison_detected": 0.0})
    svc._chat_completion_with_empty_retry = lambda *args, **kwargs: "good answer"

    def _capture_evaluate(
        question: str, context: str, answer: str, **kwargs: Any
    ) -> dict[str, Any]:
        captured_context["value"] = context
        return {"acceptable": True, "score": 1.0, "reason": "ok"}

    svc._evaluate = _capture_evaluate

    _run_rag(
        "What does Viva secure by design require?",
        5,
        0.2,
        True,
        svc=svc,
        evidence_corpora_include=["b", "c"],
    )

    context = captured_context.get("value", "")
    assert "No Corpus B items were retrieved for this query." not in context
    assert "Source: b-upper.pdf" in context
    assert "Source: b-role.pdf" in context


def test_run_rag_graph_expansion_augments_controls_and_chunks() -> None:
    svc = _base_svc()
    svc.config.graph_enabled = True
    captured_context: dict[str, str] = {}

    svc._hybrid_search = lambda question, retrieve_k, evidence_filter: (
        [
            {
                "corpus": "b",
                "corpus_role": "narrative_guidance",
                "content": "Original guidance chunk.",
                "source_name": "guidance-1.md",
                "source_path": "/tmp/guidance-1.md",
                "normalised_text_sha256": "abc123",
                "content_sha256": "",
            }
        ],
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
                "score": 0.8,
            }
        ],
        {"controls_comparison_detected": 0.0},
    )

    class _GraphStore:
        def subgraph(
            self, *, seed_node_id: str, depth: int = 1, max_edges: int = 1000
        ) -> dict[str, list[dict[str, Any]]]:
            return {
                "nodes": [
                    {
                        "node_id": "a:ctrl-2",
                        "node_type": "CorpusAControl",
                        "label": "CTRL-2",
                        "attributes": {
                            "requirement_id": "CTRL-2",
                            "framework": "ISM",
                            "framework_version": "1",
                            "control_family": "Network",
                            "maturity_level": "ml2",
                            "requirement_text": "must do y",
                            "guidance_text": "supplemental guidance",
                            "source_uri": "controls://ctrl-2",
                        },
                    },
                    {
                        "node_id": "b:expanded-1",
                        "node_type": "CorpusBGuidanceChunk",
                        "label": "guidance-2.md",
                        "attributes": {
                            "source_name": "guidance-2.md",
                            "source_path": "/tmp/guidance-2.md",
                            "original_filename": "guidance-2.md",
                            "normalised_text_sha256": "def456",
                            "content_sha256": "",
                            "corpus": "b",
                            "corpus_role": "narrative_guidance",
                            "content": "Expanded guidance chunk.",
                        },
                    },
                ],
                "edges": [
                    {
                        "edge_id": "e:1",
                        "from_id": seed_node_id,
                        "to_id": "a:ctrl-2",
                        "edge_type": "GUIDANCE_SUPPORTS_CONTROL",
                        "confidence": 0.9,
                        "evidence_key": "ev:1",
                    },
                    {
                        "edge_id": "e:2",
                        "from_id": seed_node_id,
                        "to_id": "b:expanded-1",
                        "edge_type": "GUIDANCE_SUPPORTS_CONTROL",
                        "confidence": 0.7,
                        "evidence_key": "ev:2",
                    },
                ],
            }

    svc._create_graph_store = lambda: _GraphStore()
    svc._chat_completion_with_empty_retry = lambda *args, **kwargs: "good answer"

    def _capture_evaluate(
        question: str, context: str, answer: str, **kwargs: Any
    ) -> dict[str, Any]:
        captured_context["value"] = context
        return {"acceptable": True, "score": 1.0, "reason": "ok"}

    svc._evaluate = _capture_evaluate

    result = _run_rag(
        "What else is related?",
        5,
        0.2,
        True,
        svc=svc,
        include_graph_expansion=True,
        graph_expansion_depth=1,
        graph_expansion_max_edges=20,
    )

    assert result["graph_summary"]["enabled"] is True
    assert result["graph_summary"]["expanded_controls"] >= 1
    assert result["graph_summary"]["expanded_chunks"] >= 1
    assert any(item.get("requirement_id") == "CTRL-2" for item in result["controls_results"])
    assert any(item.get("source_name") == "guidance-2.md" for item in result["results"])
    context = captured_context.get("value", "")
    assert "must do y" in context
    assert "Expanded guidance chunk." in context


def test_run_rag_low_k_rebalances_dominant_framework_chunks() -> None:
    svc = _base_svc()
    seen_sources: list[str] = []

    svc._resolve_evidence_corpora = lambda include, exclude: ["b", "c"]
    svc._build_evidence_corpus_filter = lambda selected: "|".join(selected)

    def _hybrid(
        question: str, retrieve_k: int, evidence_filter: str
    ) -> tuple[list[dict[str, Any]], dict[str, float]]:
        if evidence_filter == "b":
            return (
                [
                    {
                        "corpus": "b",
                        "corpus_role": "narrative_guidance",
                        "content": "pci chunk 1",
                        "source_name": "pci_dss_v4_0_1-enriched.jsonl",
                    },
                    {
                        "corpus": "b",
                        "corpus_role": "narrative_guidance",
                        "content": "pci chunk 2",
                        "source_name": "pci_dss_v4_0_1-enriched.jsonl",
                    },
                    {
                        "corpus": "b",
                        "corpus_role": "narrative_guidance",
                        "content": "pci chunk 3",
                        "source_name": "pci_dss_v4_0_1-enriched.jsonl",
                    },
                    {
                        "corpus": "b",
                        "corpus_role": "narrative_guidance",
                        "content": "aescsf chunk",
                        "source_name": "aescsf_v2-enriched.jsonl",
                    },
                ],
                {"embedding_s": 0.01, "search_s": 0.02},
            )
        return (
            [
                {
                    "corpus": "c",
                    "corpus_role": "assessed_artifact",
                    "content": "artifact chunk",
                    "source_name": "artifact-report.md",
                }
            ],
            {"embedding_s": 0.01, "search_s": 0.02},
        )

    svc._hybrid_search = _hybrid

    def _capture_context(question: str, context: str, answer: str, **kwargs: Any) -> dict[str, Any]:
        for line in context.splitlines():
            if line.startswith("Source: "):
                seen_sources.append(line.replace("Source: ", "", 1).strip())
        return {"acceptable": True, "score": 1.0, "reason": "ok"}

    svc._evaluate = _capture_context

    result = _run_rag(
        "How do these controls compare?",
        5,
        0.2,
        True,
        svc=svc,
        evidence_corpora_include=["b", "c"],
    )

    assert result["metrics"]["small_k_rebalance_enabled"] == 1.0
    assert result["metrics"]["small_k_rebalance_applied"] == 1.0
    assert result["audit"]["small_k_chunk_rebalance"]["reason"] == "rebalanced"
    assert "aescsf_v2-enriched.jsonl" in seen_sources[:3]


def test_run_rag_low_k_skips_rebalance_for_explicit_framework_intent() -> None:
    svc = _base_svc()

    svc._resolve_evidence_corpora = lambda include, exclude: ["b", "c"]
    svc._build_evidence_corpus_filter = lambda selected: "|".join(selected)

    def _hybrid(
        question: str, retrieve_k: int, evidence_filter: str
    ) -> tuple[list[dict[str, Any]], dict[str, float]]:
        del question, retrieve_k, evidence_filter
        return (
            [
                {
                    "corpus": "b",
                    "corpus_role": "narrative_guidance",
                    "content": "pci chunk 1",
                    "source_name": "pci_dss_v4_0_1-enriched.jsonl",
                },
                {
                    "corpus": "b",
                    "corpus_role": "narrative_guidance",
                    "content": "pci chunk 2",
                    "source_name": "pci_dss_v4_0_1-enriched.jsonl",
                },
                {
                    "corpus": "b",
                    "corpus_role": "narrative_guidance",
                    "content": "pci chunk 3",
                    "source_name": "pci_dss_v4_0_1-enriched.jsonl",
                },
                {
                    "corpus": "b",
                    "corpus_role": "narrative_guidance",
                    "content": "aescsf chunk",
                    "source_name": "aescsf_v2-enriched.jsonl",
                },
            ],
            {"embedding_s": 0.01, "search_s": 0.02},
        )

    svc._hybrid_search = _hybrid

    result = _run_rag(
        "Only compare PCI-DSS controls and evidence.",
        5,
        0.2,
        True,
        svc=svc,
        evidence_corpora_include=["b", "c"],
    )

    assert result["metrics"]["small_k_rebalance_enabled"] == 1.0
    assert result["metrics"]["small_k_rebalance_applied"] == 0.0
    assert result["audit"]["small_k_chunk_rebalance"]["reason"] == "framework_specific_intent"


def test_run_rag_splits_guidance_and_evidence_retrieval_when_b_and_c_selected() -> None:
    svc = _base_svc()
    calls: list[str] = []

    svc._resolve_evidence_corpora = lambda include, exclude: ["b", "c"]
    svc._build_evidence_corpus_filter = lambda selected: "|".join(selected)

    def _hybrid(
        question: str, retrieve_k: int, evidence_filter: str
    ) -> tuple[list[dict[str, Any]], dict[str, float]]:
        calls.append(evidence_filter)
        if evidence_filter == "b":
            return (
                [
                    {
                        "corpus": "b",
                        "corpus_role": "narrative_guidance",
                        "content": "guidance",
                        "source_name": "b1",
                    }
                ],
                {"embedding_s": 0.01, "search_s": 0.02},
            )
        if evidence_filter == "c":
            return (
                [
                    {
                        "corpus": "c",
                        "corpus_role": "assessed_artifact",
                        "content": "evidence",
                        "source_name": "c1",
                    }
                ],
                {"embedding_s": 0.03, "search_s": 0.04},
            )
        raise AssertionError(f"unexpected filter: {evidence_filter}")

    svc._hybrid_search = _hybrid

    result = _run_rag(
        "q",
        5,
        0.2,
        True,
        svc=svc,
        evidence_corpora_include=["b", "c"],
    )

    assert calls == ["b", "c"]
    assert [item["source_name"] for item in result["results"]] == ["b1", "c1"]
    assert result["metrics"]["split_guidance_evidence_search"] == 1.0
    assert result["metrics"]["guidance_search_s"] == 0.02
    assert result["metrics"]["evidence_search_s"] == 0.04
    assert result["audit"]["evidence_chunk_retrieval"]["split_guidance_evidence_search"] is True


def test_run_rag_adapts_corpus_c_prompt_for_empty_and_populated_cases() -> None:
    empty_svc = _base_svc()
    empty_messages: list[dict[str, str]] = []
    empty_svc._corpus_has_content = lambda corpus: {"a": False, "b": True, "c": False}.get(corpus)

    empty_svc._resolve_evidence_corpora = lambda include, exclude: ["b", "c"]
    empty_svc._build_evidence_corpus_filter = lambda selected: "|".join(selected)

    def _hybrid_empty(
        question: str, retrieve_k: int, evidence_filter: str
    ) -> tuple[list[dict[str, Any]], dict[str, float]]:
        del question, retrieve_k
        if evidence_filter == "b":
            return (
                [
                    {
                        "corpus": "b",
                        "corpus_role": "narrative_guidance",
                        "content": "guidance chunk",
                        "source_name": "guidance-1.md",
                    }
                ],
                {"embedding_s": 0.01, "search_s": 0.02},
            )
        return ([], {"embedding_s": 0.01, "search_s": 0.02})

    empty_svc._hybrid_search = _hybrid_empty

    def _capture_empty(messages: list[dict[str, str]], *args: Any, **kwargs: Any) -> str:
        del args, kwargs
        empty_messages.extend(messages)
        return "good answer"

    empty_svc._chat_completion_with_empty_retry = _capture_empty

    _run_rag(
        "What changed in the review set?",
        5,
        0.2,
        True,
        svc=empty_svc,
        evidence_corpora_include=["b", "c"],
    )

    empty_user_message = next(msg["content"] for msg in empty_messages if msg["role"] == "user")
    assert "mode: b_only" in empty_user_message
    assert (
        "Corpus C (assessed artifacts/review):\nOut of scope for this request."
        in empty_user_message
    )

    populated_svc = _base_svc()
    populated_messages: list[dict[str, str]] = []
    populated_svc._corpus_has_content = lambda corpus: {"a": False, "b": True, "c": True}.get(
        corpus
    )

    populated_svc._resolve_evidence_corpora = lambda include, exclude: ["b", "c"]
    populated_svc._build_evidence_corpus_filter = lambda selected: "|".join(selected)

    def _hybrid_populated(
        question: str, retrieve_k: int, evidence_filter: str
    ) -> tuple[list[dict[str, Any]], dict[str, float]]:
        del question, retrieve_k
        if evidence_filter == "c":
            return (
                [
                    {
                        "corpus": "c",
                        "corpus_role": "assessed_artifact",
                        "content": "review artifact",
                        "source_name": "artifact-1.md",
                    }
                ],
                {"embedding_s": 0.01, "search_s": 0.02},
            )
        return ([], {"embedding_s": 0.01, "search_s": 0.02})

    populated_svc._hybrid_search = _hybrid_populated

    def _capture_populated(messages: list[dict[str, str]], *args: Any, **kwargs: Any) -> str:
        del args, kwargs
        populated_messages.extend(messages)
        return "good answer"

    populated_svc._chat_completion_with_empty_retry = _capture_populated

    _run_rag(
        "What changed in the review set?",
        5,
        0.2,
        True,
        svc=populated_svc,
        evidence_corpora_include=["b", "c"],
    )

    populated_user_message = next(
        msg["content"] for msg in populated_messages if msg["role"] == "user"
    )
    assert "Use the retrieved Corpus C artifacts below" in populated_user_message
    assert "mode: c_plus_a_or_b" in populated_user_message
    assert "Source: artifact-1.md" in populated_user_message


def test_run_rag_keeps_selected_nonempty_corpus_in_scope_without_retrieval() -> None:
    svc = _base_svc()
    captured_messages: list[dict[str, str]] = []

    svc._resolve_evidence_corpora = lambda include, exclude: ["c"]
    svc._corpus_has_content = lambda corpus: True if corpus == "c" else False
    svc._build_evidence_corpus_filter = lambda selected: "c"
    svc._hybrid_search = lambda question, retrieve_k, evidence_filter: (
        [],
        {"embedding_s": 0.01, "search_s": 0.02},
    )

    def _capture(messages: list[dict[str, str]], *args: Any, **kwargs: Any) -> str:
        del args, kwargs
        captured_messages.extend(messages)
        return "good answer"

    svc._chat_completion_with_empty_retry = _capture

    result = _run_rag(
        "Assess the uploaded review docs",
        5,
        0.2,
        True,
        svc=svc,
        evidence_corpora_include=["c"],
    )

    assert result["answer"] == "good answer"
    assert result["audit"]["scope_mode"] == "c_only"
    assert result["audit"]["scope_profile"]["in_scope_corpora"] == ["c"]
    assert result["audit"]["scope_profile"]["retrieved_corpora"] == []
    assert result["audit"]["scope_profile"]["in_scope_without_retrieval"] == ["c"]

    user_message = next(msg["content"] for msg in captured_messages if msg["role"] == "user")
    assert "mode: c_only" in user_message
    assert "No Corpus C items were retrieved for this query." in user_message


def test_token_usage_accumulator_preserves_provider_counts() -> None:
    accumulator = _new_token_usage_accumulator()
    record_token_usage(prompt_tokens=120, completion_tokens=30, total_tokens=150)

    _record_token_usage(accumulator, prompt_text="ignored", completion_text="ignored")

    assert accumulator == {
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "total_tokens": 150,
        "llm_calls": 1,
        "estimated": False,
    }
