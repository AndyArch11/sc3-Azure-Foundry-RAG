"""Qdrant retrieval-only golden-set evaluator.

This runner validates retrieval behaviour directly against the Qdrant-backed
local search index, independent of answer generation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime.search.local_qdrant import LocalQdrantSearchClient


class ContractSearchClient:
    """Deterministic in-memory SearchClient for contract-level evals.

    This client avoids infra dependencies (Qdrant/Ollama) and is intended to
    validate evaluator math, thresholds, and reporting logic.

    It does not attempt to simulate realistic retrieval behaviour, and is not
    suitable for integration-level evals.

    Attributes:
        _index: The name of the search index.
        index_name: The name of the search index.
    """

    def __init__(self, index: str) -> None:
        """Initialise the ContractSearchClient.

        Args:
            index: The name of the search index.
        """
        self._index = index

    @property
    def index_name(self) -> str:
        """Return the name of the search index.

        Returns:
            The name of the search index.
        """
        return self._index

    def _doc_for_framework(self, framework: str, ordinal: int) -> dict[str, Any]:
        """Generate a deterministic document for a given framework and ordinal.

        Args:
            framework: The framework identifier (e.g., "pci_dss", "nist_csf").
            ordinal: The ordinal number for the document (used to vary content).
        Returns:
            A dictionary representing the deterministic document.
        """
        framework_key = str(framework).strip().lower() or "unknown"
        return {
            "id": f"contract-{framework_key}-{ordinal}",
            "content": f"Contract fixture retrieval document for {framework_key}.",
            "source_name": f"{framework_key}_contract_fixture.md",
            "source_path": f"tests/fixtures/{framework_key}_contract_fixture.md",
            "original_filename": f"{framework_key}_contract_fixture.md",
            "upload_batch": "contract",
            "@search.score": float(max(0.0, 1.0 - (ordinal * 0.01))),
        }

    def search(
        self,
        *,
        query_text: str,
        top: int,
        vector_query: list[float] | None = None,
        filters: str | None = None,
        select: list[str] | None = None,
        **extra_kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Execute a deterministic search query for contract-level evaluation.

        Args:
            query_text: The search query text (ignored in contract mode).
            top: The maximum number of results to return.
            vector_query: Optional vector query for semantic search (ignored).
            filters: Optional filter expression for search (ignored).
            select: Optional list of fields to include in the results.
            extra_kwargs: Additional keyword arguments, including expected frameworks.

        Returns:
            A list of deterministic documents matching the expected frameworks.
        """
        del query_text, vector_query, filters
        expected_frameworks = [
            str(v).strip().lower() for v in (extra_kwargs.get("expected_frameworks") or []) if v
        ]
        controls_framework = str(extra_kwargs.get("controls_framework") or "").strip().lower()

        if controls_framework:
            frameworks = [controls_framework]
        elif expected_frameworks:
            frameworks = expected_frameworks
        else:
            frameworks = ["unknown"]

        docs = [
            self._doc_for_framework(frameworks[i % len(frameworks)], i) for i in range(max(1, top))
        ]
        if select:
            docs = [{k: d[k] for k in select if k in d} for d in docs]
        return docs[:top]

    def load_documents(self, docs: list[dict[str, Any]]) -> None:
        del docs

    def delete_documents(self, *, documents: list[dict[str, Any]]) -> None:
        del documents


def _framework_filter_candidates(
    *,
    chunks: list[dict[str, Any]],
    controls_framework: str | None,
    retrieve_k: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply framework-intent narrowing to an oversampled candidate set.

    This is used by the integration eval path so explicit framework cases can
    be assessed without requiring the underlying Qdrant payload schema to carry
    a native framework filter field.

    Args:
        chunks: The list of retrieved candidate chunks.
        controls_framework: The specific controls framework to filter by (if any).
        retrieve_k: The number of top retrieval results to consider.

    Returns:
        A tuple containing the filtered list of chunks and a diagnostics dictionary
        summarising the framework filtering process.
    """
    if not controls_framework:
        return chunks[:retrieve_k], {
            "framework_filter_applied": False,
            "framework_filter_requested": None,
            "framework_filter_candidate_count": len(chunks),
            "framework_filter_match_count": len(chunks[:retrieve_k]),
        }

    target = str(controls_framework).strip().lower()
    matched = [chunk for chunk in chunks if _extract_framework_hint(chunk) == target]
    narrowed = matched[:retrieve_k]
    return narrowed, {
        "framework_filter_applied": True,
        "framework_filter_requested": target,
        "framework_filter_candidate_count": len(chunks),
        "framework_filter_match_count": len(matched),
    }


def _duplicate_diagnostics(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarise duplicate-like chunks in the evaluated top-k set.

    Args:
        chunks: The list of retrieved candidate chunks.

    Returns:
        A dictionary containing duplicate diagnostics.
    """
    key_counts: Counter[str] = Counter()
    for chunk in chunks:
        key = (
            str(chunk.get("dedupe_hash") or "").strip()
            or str(chunk.get("content_sha256") or "").strip()
            or str(chunk.get("normalised_text_sha256") or "").strip()
        )
        if not key:
            source_name = str(chunk.get("source_name") or "").strip().lower()
            content = str(chunk.get("content") or "").strip().lower()
            key = f"fallback:{source_name}:{content[:160]}"
        key_counts[key] += 1

    duplicate_groups = {key: count for key, count in key_counts.items() if count > 1}
    return {
        "top_k_count": len(chunks),
        "unique_retrieval_keys": len(key_counts),
        "duplicate_item_count": sum(count - 1 for count in duplicate_groups.values()),
        "duplicate_group_count": len(duplicate_groups),
        "duplicate_groups": duplicate_groups,
        "framework_hint_counts": dict(Counter(_extract_framework_hint(chunk) for chunk in chunks)),
    }


@dataclass(frozen=True)
class GoldenCase:
    """A single retrieval evaluation case.

    Attributes:
        id: Unique identifier for the case.
        question: The natural language question to evaluate.
        retrieve_k: The number of top retrieval results to consider.
        expected_relevant_frameworks: List of expected relevant frameworks for the question.
        controls_framework: Optional specific controls framework to filter by.
        min_precision: Optional minimum precision threshold for the case.
        min_diversity: Optional minimum framework diversity threshold for the case.
        max_dominant_framework_share: Optional maximum share of the dominant framework in results.
    """

    id: str
    question: str
    retrieve_k: int
    expected_relevant_frameworks: list[str]
    controls_framework: str | None
    min_precision: float | None
    min_diversity: int | None
    max_dominant_framework_share: float | None


@dataclass(frozen=True)
class CaseResult:
    """Evaluation result for a single retrieval case.

    Attributes:
        case_id: Unique identifier for the evaluated case.
        retrieve_k: The number of top retrieval results considered.
        precision_at_k: The calculated precision at k for the case.
        dominant_framework_share: The share of the most common framework in the results.
        framework_diversity_at_k: The count of unique frameworks in the top k results.
        expected_framework_coverage: The proportion of expected frameworks covered in the results.
        effective_min_precision: The effective minimum precision threshold applied for evaluation.
        effective_min_diversity: The effective minimum diversity threshold applied for evaluation.
        effective_max_dominant_framework_share: The effective maximum dominant framework share threshold applied for evaluation (if any).
        pass_precision: Whether the case passed the precision threshold.
        pass_diversity: Whether the case passed the diversity threshold.
        pass_dominance: Whether the case passed the dominance threshold (if applicable).
        failure_reasons: List of reasons for failure, if any thresholds were not met.
    """

    case_id: str
    retrieve_k: int
    precision_at_k: float
    dominant_framework_share: float
    framework_diversity_at_k: int
    expected_framework_coverage: float
    effective_min_precision: float
    effective_min_diversity: int
    effective_max_dominant_framework_share: float | None
    pass_precision: bool
    pass_diversity: bool
    pass_dominance: bool
    failure_reasons: list[str]


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON file and return its contents as a dictionary.

    Args:
        path: Path to the JSON file.

    Returns:
        Dictionary containing the JSON file contents.
    """
    return json.loads(path.read_text(encoding="utf-8"))


def _load_cases(path: Path) -> list[GoldenCase]:
    """Load retrieval evaluation cases from a JSON file.

    Args:
        path: Path to the JSON file.

    Returns:
        List of GoldenCase instances loaded from the JSON file.
    """
    raw = _read_json(path)
    if int(raw.get("version", 0)) != 1:
        raise ValueError("Unsupported golden set version; expected version=1")

    cases: list[GoldenCase] = []
    for item in raw.get("cases", []):
        expected = [str(x).strip().lower() for x in item.get("expected_relevant_frameworks", [])]
        if not expected:
            raise ValueError(f"Case {item.get('id')} is missing expected_relevant_frameworks")
        cases.append(
            GoldenCase(
                id=str(item["id"]),
                question=str(item["question"]),
                retrieve_k=int(item["retrieve_k"]),
                expected_relevant_frameworks=expected,
                controls_framework=(
                    str(item["controls_framework"]).strip().lower()
                    if item.get("controls_framework") not in {None, ""}
                    else None
                ),
                min_precision=(
                    float(item["min_precision"])
                    if item.get("min_precision") not in {None, ""}
                    else None
                ),
                min_diversity=(
                    int(item["min_diversity"])
                    if item.get("min_diversity") not in {None, ""}
                    else None
                ),
                max_dominant_framework_share=(
                    float(item["max_dominant_framework_share"])
                    if item.get("max_dominant_framework_share") not in {None, ""}
                    else None
                ),
            )
        )
    return cases


def _effective_thresholds(
    *,
    case: GoldenCase,
    default_min_precision: float,
    default_min_diversity: int,
) -> tuple[float, int]:
    """Determine the effective thresholds for precision and diversity for a given case.

    Args:
        case: The GoldenCase instance being evaluated.
        default_min_precision: The default minimum precision threshold.
        default_min_diversity: The default minimum diversity threshold.
    Returns:
        A tuple containing the effective minimum precision and diversity thresholds.
    """
    min_precision = case.min_precision if case.min_precision is not None else default_min_precision
    if case.min_diversity is not None:
        min_diversity = case.min_diversity
    elif case.controls_framework:
        min_diversity = 1
    else:
        min_diversity = default_min_diversity
    return float(min_precision), int(min_diversity)


def _extract_framework_hint(chunk: dict[str, Any]) -> str:
    """Extract a framework hint from a retrieved chunk.

    Args:
        chunk: A dictionary representing a retrieved chunk.
    Returns:
        A string representing the framework hint extracted from the chunk.
    """
    joined = " ".join(
        [
            str(chunk.get("source_name") or "").lower(),
            str(chunk.get("source_path") or "").lower(),
            str(chunk.get("original_filename") or "").lower(),
            str(chunk.get("upload_batch") or "").lower(),
        ]
    )
    normalised = joined.replace("-", "_").replace("/", "_").replace(".", "_").replace(" ", "_")
    if "pci_dss" in normalised or ("pci" in normalised and "dss" in normalised):
        return "pci_dss"
    if "aescsf" in normalised:
        return "aescsf"
    if "nist_csf" in normalised:
        return "nist_csf"
    if "nist_ai_rmf" in normalised:
        return "nist_ai_rmf"
    if "cis_controls" in normalised:
        return "cis_controls"
    if "essential_eight" in normalised:
        return "essential_eight"
    if "pspf" in normalised:
        return "pspf"
    if "ism" in normalised:
        return "ism"
    return "unknown"


def _score_case(
    *,
    case: GoldenCase,
    chunks: list[dict[str, Any]],
    default_min_precision: float,
    default_min_diversity: int,
) -> CaseResult:
    """Score a single retrieval case based on retrieved chunks.

    Args:
        case: The GoldenCase instance being evaluated.
        chunks: A list of retrieved chunk dictionaries.
        default_min_precision: The default minimum precision threshold.
        default_min_diversity: The default minimum diversity threshold.
    Returns:
        A CaseResult instance containing the evaluation results for the case.
    """
    top_k = chunks[: case.retrieve_k]
    framework_hints = [_extract_framework_hint(chunk) for chunk in top_k]
    hint_counts = Counter(framework_hints)

    expected = set(case.expected_relevant_frameworks)
    hits = [hint for hint in framework_hints if hint in expected]
    precision_at_k = len(hits) / float(max(1, case.retrieve_k))

    covered_expected = expected.intersection(set(framework_hints))
    expected_framework_coverage = len(covered_expected) / float(max(1, len(expected)))

    dominant_framework_share = 0.0
    if hint_counts:
        dominant_framework_share = max(hint_counts.values()) / float(max(1, len(top_k)))

    framework_diversity = len({hint for hint in framework_hints if hint != "unknown"})
    effective_min_precision, effective_min_diversity = _effective_thresholds(
        case=case,
        default_min_precision=default_min_precision,
        default_min_diversity=default_min_diversity,
    )

    pass_precision = precision_at_k >= effective_min_precision
    pass_diversity = framework_diversity >= effective_min_diversity
    effective_max_dominant_framework_share = case.max_dominant_framework_share
    pass_dominance = (
        True
        if effective_max_dominant_framework_share is None
        else dominant_framework_share <= effective_max_dominant_framework_share
    )

    failure_reasons: list[str] = []
    if not pass_precision:
        failure_reasons.append(
            f"precision_at_k<{effective_min_precision:.3f} (actual={precision_at_k:.3f})"
        )
    if not pass_diversity:
        failure_reasons.append(
            f"framework_diversity_at_k<{effective_min_diversity} (actual={framework_diversity})"
        )
    if not pass_dominance and effective_max_dominant_framework_share is not None:
        failure_reasons.append(
            "dominant_framework_share"
            f">{effective_max_dominant_framework_share:.3f} (actual={dominant_framework_share:.3f})"
        )

    return CaseResult(
        case_id=case.id,
        retrieve_k=case.retrieve_k,
        precision_at_k=round(precision_at_k, 4),
        dominant_framework_share=round(dominant_framework_share, 4),
        framework_diversity_at_k=framework_diversity,
        expected_framework_coverage=round(expected_framework_coverage, 4),
        effective_min_precision=round(effective_min_precision, 4),
        effective_min_diversity=effective_min_diversity,
        effective_max_dominant_framework_share=(
            round(effective_max_dominant_framework_share, 4)
            if effective_max_dominant_framework_share is not None
            else None
        ),
        pass_precision=pass_precision,
        pass_diversity=pass_diversity,
        pass_dominance=pass_dominance,
        failure_reasons=failure_reasons,
    )


def main() -> int:
    """Run the Qdrant retrieval-only golden-set evaluation.

    Returns:
        Exit code indicating the result of the evaluation (0 for success, 1 for failure, 2 for errors).
    """
    parser = argparse.ArgumentParser(description="Run Qdrant retrieval-only golden-set evaluations")
    parser.add_argument(
        "--golden-set",
        default="tests/evals/rag_golden_set.json",
        help="Path to the golden eval suite JSON file",
    )
    parser.add_argument(
        "--output",
        default="",
        help=("Path to write eval report JSON. " "Default depends on --backend-mode."),
    )
    parser.add_argument(
        "--index-name",
        default=(
            os.getenv("AZURE_SEARCH_INDEX_NAME")
            or os.getenv("SEARCH_INDEX_NAME")
            or "grounding-index"
        ),
        help="Qdrant collection name used for retrieval",
    )
    parser.add_argument(
        "--qdrant-url",
        default=os.getenv("QDRANT_URL", "http://localhost:6333"),
        help="Qdrant base URL",
    )
    parser.add_argument(
        "--backend-mode",
        choices=["integration", "contract"],
        default=os.getenv("QDRANT_EVAL_BACKEND_MODE", "integration"),
        help="Eval backend mode: integration=live Qdrant, contract=in-memory deterministic",
    )
    parser.add_argument(
        "--min-precision",
        type=float,
        default=0.3,
        help="Minimum precision@k threshold per case",
    )
    parser.add_argument(
        "--min-diversity",
        type=int,
        default=2,
        help="Minimum framework diversity@k threshold per case",
    )

    args = parser.parse_args()

    output_path = (
        Path(args.output)
        if args.output
        else Path(f"local_state/evals/qdrant_retrieval_eval_{args.backend_mode}_latest.json")
    )

    cases = _load_cases(Path(args.golden_set))
    if args.backend_mode == "contract":
        client: Any = ContractSearchClient(index=args.index_name)
        collection_exists: bool | None = None
    else:
        client = LocalQdrantSearchClient(index=args.index_name, qdrant_url=args.qdrant_url)
        collection_exists = False
        try:
            collection_exists = bool(client._client.collection_exists(args.index_name))  # type: ignore[attr-defined]
        except Exception:
            collection_exists = False

        if not collection_exists:
            print(
                f"Qdrant collection not found: {args.index_name} at {args.qdrant_url}",
                file=sys.stderr,
            )
            return 2

    detailed_rows: list[dict[str, Any]] = []
    case_scores: list[CaseResult] = []
    for case in cases:
        query_top = case.retrieve_k
        if args.backend_mode == "integration" and case.controls_framework:
            query_top = max(case.retrieve_k * 8, 20)

        results = client.search(
            query_text=case.question,
            top=query_top,
            expected_frameworks=case.expected_relevant_frameworks,
            controls_framework=case.controls_framework,
        )
        raw_chunks = [c for c in results if isinstance(c, dict)]
        chunks, framework_filter_diagnostics = _framework_filter_candidates(
            chunks=raw_chunks,
            controls_framework=(
                case.controls_framework if args.backend_mode == "integration" else None
            ),
            retrieve_k=case.retrieve_k,
        )
        diagnostics = {
            **framework_filter_diagnostics,
            **_duplicate_diagnostics(chunks),
            "raw_candidate_count": len(raw_chunks),
        }
        scored = _score_case(
            case=case,
            chunks=chunks,
            default_min_precision=args.min_precision,
            default_min_diversity=args.min_diversity,
        )
        case_scores.append(scored)
        detailed_rows.append(
            {
                "case_id": case.id,
                "question": case.question,
                "raw_retrieval_results": raw_chunks,
                "retrieval_results": chunks,
                "diagnostics": diagnostics,
                "score": scored.__dict__,
            }
        )

    failed = [
        s for s in case_scores if not (s.pass_precision and s.pass_diversity and s.pass_dominance)
    ]

    summary = {
        "suite": args.golden_set,
        "backend_mode": args.backend_mode,
        "qdrant": {
            "url": (args.qdrant_url if args.backend_mode == "integration" else None),
            "collection": args.index_name,
            "collection_exists": collection_exists,
        },
        "thresholds": {
            "min_precision": args.min_precision,
            "min_diversity": args.min_diversity,
        },
        "totals": {
            "cases": len(case_scores),
            "failed": len(failed),
            "pass_rate": round(
                (len(case_scores) - len(failed)) / float(max(1, len(case_scores))),
                4,
            ),
            "mean_precision_at_k": round(
                sum(s.precision_at_k for s in case_scores) / float(max(1, len(case_scores))),
                4,
            ),
            "mean_dominant_framework_share": round(
                sum(s.dominant_framework_share for s in case_scores)
                / float(max(1, len(case_scores))),
                4,
            ),
            "mean_framework_diversity_at_k": round(
                sum(float(s.framework_diversity_at_k) for s in case_scores)
                / float(max(1, len(case_scores))),
                4,
            ),
            "mean_expected_framework_coverage": round(
                sum(s.expected_framework_coverage for s in case_scores)
                / float(max(1, len(case_scores))),
                4,
            ),
            "mean_duplicate_item_count": round(
                sum(
                    float((row.get("diagnostics") or {}).get("duplicate_item_count") or 0)
                    for row in detailed_rows
                )
                / float(max(1, len(detailed_rows))),
                4,
            ),
        },
        "results": detailed_rows,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"Report written: {output_path}")
    print(json.dumps(summary["totals"], indent=2))
    if failed:
        print("Failed cases:", ", ".join(s.case_id for s in failed), file=sys.stderr)
        for failed_case in failed:
            details = "; ".join(failed_case.failure_reasons) or "no failure reason captured"
            print(f"- {failed_case.case_id}: {details}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
