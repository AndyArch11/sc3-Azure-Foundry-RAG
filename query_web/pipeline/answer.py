"""Answer formatting and retrieval-grounded fallback helpers extracted from app.py."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from query_web.security.prompt_injection_guard import sanitise_untrusted_text
import query_web.pipeline.controls as _controls_module


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

    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    normalised = "\n".join(line.rstrip() for line in normalised.split("\n"))
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

    focus_terms = _controls_module._question_focus_terms(question)

    def _normalise_excerpt_text(text: str) -> str:
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

    comparison_intent = _controls_module._is_cross_framework_comparison_intent(question)
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
