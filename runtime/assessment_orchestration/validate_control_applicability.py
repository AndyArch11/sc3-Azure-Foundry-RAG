"""
Validate heuristics against Corpus A controls to classify applicability scope.
Runs a gauge on deterministic classification to identify ambiguous cases.
Output shows distribution, confidence levels, and edge cases for secondary LLM review.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from typing import Any

from .control_applicability import classify_control_applicability
from .dev_llms import create_chat_completion_fn, get_llm_backend


_APPLICABILITY_REVIEW_PROMPT = (
    "You classify cybersecurity controls by applicability scope for cloud posture assessment. "
    "Return JSON only with keys: scope, confidence, rationale. "
    "Allowed scope values: technical, process, governance, mixed. "
    "technical = implementation/posture/configuration controls. "
    "process = procedural/oversight/policy/documentation/training controls. "
    "governance = explicit governance-program controls. "
    "mixed = genuinely balanced technical and process content."
)


def _extract_json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("LLM response did not contain a JSON object")
        parsed = json.loads(value[start : end + 1])

    if not isinstance(parsed, dict):
        raise ValueError("LLM response JSON must be an object")
    return parsed


def _review_control_with_llm(
    control: dict[str, Any],
    *,
    heuristic_scope: str,
    heuristic_confidence: float,
    chat_completion,
    max_attempts: int = 2,
) -> dict[str, Any]:
    base_messages = [
        {"role": "system", "content": _APPLICABILITY_REVIEW_PROMPT},
        {
            "role": "user",
            "content": (
                "Review this control and classify its applicability scope.\n\n"
                f"Requirement ID: {control.get('requirement_id') or 'unknown'}\n"
                f"Framework: {control.get('framework') or 'unknown'}\n"
                f"Control Family: {control.get('control_family') or ''}\n"
                f"Requirement Text: {str(control.get('requirement_text') or '')[:1600]}\n"
                f"Guidance Text: {str(control.get('guidance_text') or '')[:1200]}\n\n"
                "Heuristic classifier context for comparison only:\n"
                f"- heuristic_scope: {heuristic_scope}\n"
                f"- heuristic_confidence: {heuristic_confidence:.3f}\n\n"
                "Return JSON only in this shape:\n"
                '{"scope":"technical|process|governance|mixed","confidence":0.0,"rationale":"..."}'
            ),
        },
    ]

    last_error: Exception | None = None
    for attempt in range(max_attempts):
        messages = list(base_messages)
        if attempt > 0:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous response was invalid or incomplete. "
                        "Return JSON only and ensure rationale is a non-empty string."
                    ),
                }
            )

        try:
            payload = _extract_json_object(chat_completion(messages))
            scope = str(payload.get("scope") or "").strip().lower()
            if scope not in {"technical", "process", "governance", "mixed"}:
                raise ValueError(f"Unsupported LLM applicability scope: {scope}")

            confidence_raw = payload.get("confidence", 0.0)
            try:
                confidence = max(0.0, min(1.0, float(confidence_raw)))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid LLM confidence: {confidence_raw}") from exc

            rationale = str(payload.get("rationale") or "").strip()
            if not rationale:
                raise ValueError("LLM rationale was empty")

            return {
                "scope": scope,
                "confidence": round(confidence, 3),
                "rationale": rationale,
            }
        except Exception as exc:
            last_error = exc

    assert last_error is not None
    raise last_error


def review_ambiguous_controls_with_llm(
    controls: list[dict[str, Any]],
    *,
    confidence_threshold: float,
    max_controls: int = 20,
    chat_completion=None,
) -> dict[str, Any]:
    if chat_completion is None:
        chat_completion = create_chat_completion_fn()

    ambiguous_controls: list[dict[str, Any]] = []
    for control in controls:
        metadata = classify_control_applicability(control)
        if metadata.confidence < confidence_threshold:
            ambiguous_controls.append(
                {
                    "control": control,
                    "heuristic_scope": metadata.scope,
                    "heuristic_confidence": metadata.confidence,
                }
            )

    reviewed = []
    errors = []
    for candidate in ambiguous_controls[:max_controls]:
        control = candidate["control"]
        try:
            llm_result = _review_control_with_llm(
                control,
                heuristic_scope=candidate["heuristic_scope"],
                heuristic_confidence=candidate["heuristic_confidence"],
                chat_completion=chat_completion,
            )
            reviewed.append(
                {
                    "requirement_id": control.get("requirement_id"),
                    "framework": control.get("framework"),
                    "heuristic_scope": candidate["heuristic_scope"],
                    "heuristic_confidence": round(candidate["heuristic_confidence"], 3),
                    "llm_scope": llm_result["scope"],
                    "llm_confidence": llm_result["confidence"],
                    "agrees_with_heuristic": llm_result["scope"] == candidate["heuristic_scope"],
                    "llm_rationale": llm_result["rationale"],
                }
            )
        except Exception as exc:
            errors.append(
                {
                    "requirement_id": control.get("requirement_id"),
                    "framework": control.get("framework"),
                    "error": str(exc),
                }
            )

    agreements = sum(1 for item in reviewed if item["agrees_with_heuristic"])
    disagreements = len(reviewed) - agreements
    return {
        "backend": get_llm_backend(),
        "confidence_threshold": confidence_threshold,
        "requested_reviews": min(len(ambiguous_controls), max_controls),
        "reviewed_controls": len(reviewed),
        "agreements": agreements,
        "disagreements": disagreements,
        "agreement_rate": round(agreements / len(reviewed), 3) if reviewed else 0.0,
        "errors": errors,
        "results": reviewed,
    }


def validate_controls_applicability(
    controls_source: str | None = None,
    confidence_threshold: float = 0.75,
    max_results: int = 5000,
    *,
    review_with_llm: bool = False,
    llm_max_controls: int = 20,
    chat_completion=None,
) -> dict[str, Any]:
    """
    Gauge heuristics against Corpus A controls.
    Returns classification distribution, confidence histogram, and ambiguous cases.
    
    If controls_source is None, loads all local parsed-controls/*.jsonl files.
    """
    controls_data: list[dict[str, Any]] = []
    
    if controls_source:
        with open(controls_source) as f:
            for line in f:
                line = line.strip()
                if line:
                    controls_data.append(json.loads(line))
    else:
        controls_dir = os.path.join(os.path.dirname(__file__), "..", "..", "parsed-controls")
        for jsonl_file in sorted(glob.glob(os.path.join(controls_dir, "*.jsonl"))):
            if ".gitignore" in jsonl_file:
                continue
            with open(jsonl_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        controls_data.append(json.loads(line))
    
    sampled_controls = controls_data[:max_results]

    classifications: list[dict[str, Any]] = []
    for control in sampled_controls:
        try:
            metadata = classify_control_applicability(control)
            classification = {
                "requirement_id": control.get("requirement_id"),
                "framework": control.get("framework"),
                "scope": metadata.scope,
                "confidence": metadata.confidence,
                "technical_matches": metadata.technical_matches,
                "process_matches": metadata.process_matches,
                "uncertain": metadata.uncertain,
            }
            classifications.append(classification)
        except Exception as exc:
            print(f"Warning: failed to classify {control.get('requirement_id')}: {exc}")
    
    scope_counts = {}
    confidence_histogram = {}
    ambiguous = []
    
    for classification in classifications:
        scope_counts[classification["scope"]] = scope_counts.get(classification["scope"], 0) + 1
        
        confidence_bucket = int(classification["confidence"] * 10) / 10
        confidence_histogram[confidence_bucket] = confidence_histogram.get(confidence_bucket, 0) + 1
        
        if classification["confidence"] < confidence_threshold:
            ambiguous.append(
                {
                    "requirement_id": classification["requirement_id"],
                    "framework": classification["framework"],
                    "scope": classification["scope"],
                    "confidence": round(classification["confidence"], 3),
                    "technical_matches": classification["technical_matches"],
                    "process_matches": classification["process_matches"],
                    "uncertain": classification["uncertain"],
                }
            )
    
    result = {
        "total_controls_classified": len(classifications),
        "scope_distribution": dict(sorted(scope_counts.items())),
        "average_confidence": round(
            sum(c["confidence"] for c in classifications) / len(classifications) if classifications else 0,
            3,
        ),
        "confidence_histogram": {
            f"{k:.0%}": v for k, v in sorted(confidence_histogram.items())
        },
        "ambiguous_controls_below_threshold": len(ambiguous),
        "ambiguous_controls": sorted(
            ambiguous,
            key=lambda x: (x["confidence"], x["framework"], x["requirement_id"]),
        )[:20],  # Top 20 most ambiguous
    }

    if review_with_llm:
        result["llm_review"] = review_ambiguous_controls_with_llm(
            sampled_controls,
            confidence_threshold=confidence_threshold,
            max_controls=llm_max_controls,
            chat_completion=chat_completion,
        )

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate control applicability heuristics and optionally review ambiguous controls with an LLM")
    parser.add_argument("threshold", nargs="?", type=float, default=0.75)
    parser.add_argument("controls_file", nargs="?", default=None)
    parser.add_argument("--llm-review", action="store_true", default=False)
    parser.add_argument("--llm-max-controls", type=int, default=20)
    parser.add_argument("--max-results", type=int, default=5000)
    args = parser.parse_args()

    results = validate_controls_applicability(
        controls_source=args.controls_file,
        confidence_threshold=args.threshold,
        max_results=args.max_results,
        review_with_llm=args.llm_review,
        llm_max_controls=args.llm_max_controls,
    )
    print(json.dumps(results, indent=2))
