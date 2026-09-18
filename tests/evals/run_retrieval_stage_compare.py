"""Compare retrieval stages to isolate where ranking/diversity is lost.

Stages compared:
- raw_qdrant: direct LocalQdrantSearchClient vector search
- hybrid_filtered: query_web.pipeline.search._hybrid_search with evidence corpus filter
- post_rebalance: query_web.pipeline.rag_pipeline._small_k_chunk_rebalance output
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import JSON

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from query_web.pipeline import controls as controls_pipeline
from query_web.pipeline import rag_pipeline
from query_web.pipeline import search as search_pipeline
from runtime.search.local_qdrant import LocalQdrantSearchClient


@dataclass(frozen=True)
class GoldenCase:
    """A single golden eval case for retrieval stage comparison.

    Attributes:
        id: Unique identifier for the case.
        question: The question text for the retrieval case.
        retrieve_k: The number of documents to retrieve for the case.
        expected_relevant_frameworks: List of expected relevant frameworks for the case.
        controls_framework: Optional explicit controls framework to filter by.
        min_precision: Optional minimum precision threshold for the case.
        min_diversity: Optional minimum diversity threshold for the case.
        max_dominant_framework_share: Optional maximum share of a single framework in results.
    """

    id: str
    question: str
    retrieve_k: int
    expected_relevant_frameworks: list[str]
    controls_framework: str | None
    min_precision: float | None
    min_diversity: int | None
    max_dominant_framework_share: float | None


class _CompareSvc:
    """Minimal service surface needed by _hybrid_search.

    Attributes:
        search_client: The LocalQdrantSearchClient instance used for embedding and search.
        logger: Logger instance for logging messages.
        config: Configuration object with cloud_provider attribute.
    """

    def __init__(self, search_client: LocalQdrantSearchClient) -> None:
        self.search_client = search_client
        self.logger = logging.getLogger("retrieval_stage_compare")
        self.config = type("Config", (), {"cloud_provider": "azure"})()

    def _embed_query(self, question: str) -> list[float]:
        return self.search_client._embed_text(question)


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON file and return its contents as a dictionary.

    Args:
        path: The path to the JSON file.
    Returns:
        A dictionary containing the parsed JSON data.
    """

    return json.loads(path.read_text(encoding="utf-8"))


def _load_cases(path: Path) -> list[GoldenCase]:
    """Load golden eval cases from a JSON file.

    Args:
        path: The path to the JSON file containing the golden cases.
    Returns:
        A list of GoldenCase instances.
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


def _extract_framework_hint(chunk: dict[str, Any]) -> str:
    """Extract a framework hint from a chunk's metadata.

    Args:
        chunk: The chunk metadata dictionary.
    Returns:
        A string representing the framework hint, or "unknown" if not identifiable.
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


def _duplicate_key(chunk: dict[str, Any]) -> str:
    """Return a stable duplicate-suppression key for retrieved payloads.

    Args:
        chunk: The chunk metadata dictionary.
    Returns:
        A string representing the deduplication key.
    """
    return (
        str(chunk.get("dedupe_hash") or "").strip()
        or str(chunk.get("content_sha256") or "").strip()
        or str(chunk.get("normalised_text_sha256") or "").strip()
        or (
            f"fallback:{str(chunk.get('source_name') or '').strip().lower()}:"
            f"{str(chunk.get('content') or '').strip().lower()[:160]}"
        )
    )


def _stage_summary(*, chunks: list[dict[str, Any]], expected: list[str]) -> dict[str, Any]:
    """Summarise a retrieval stage with counts, coverage, and duplicates.

    Args:
        chunks: The list of retrieved chunks for the stage.
        expected: The list of expected relevant frameworks for the case.
    Returns:
        A dictionary summarising the stage with counts, coverage, and duplicates.
    """
    framework_hints = [_extract_framework_hint(chunk) for chunk in chunks]
    hint_counts = Counter(framework_hints)
    expected_set = set(expected)
    covered = sorted(expected_set.intersection(set(framework_hints)))
    duplicate_counts = Counter(_duplicate_key(chunk) for chunk in chunks)
    duplicate_groups = {key: count for key, count in duplicate_counts.items() if count > 1}
    return {
        "count": len(chunks),
        "framework_hint_counts": dict(hint_counts),
        "expected_framework_hits": sum(1 for hint in framework_hints if hint in expected_set),
        "expected_framework_coverage": covered,
        "duplicate_group_count": len(duplicate_groups),
        "duplicate_item_count": sum(count - 1 for count in duplicate_groups.values()),
        "duplicate_groups": duplicate_groups,
        "top_sources": [str(chunk.get("source_name") or "") for chunk in chunks[:5]],
    }


def _filter_explicit_framework(
    *,
    chunks: list[dict[str, Any]],
    controls_framework: str | None,
    retrieve_k: int,
) -> list[dict[str, Any]]:
    """Filter chunks to those explicitly matching the controls_framework, if provided.

    Args:
        chunks: The list of retrieved chunks to filter.
        controls_framework: The explicit controls framework to filter by (e.g., "pci_dss").
        retrieve_k: The maximum number of chunks to return after filtering.

    Returns:
        A list of chunks explicitly matching the controls_framework, up to retrieve_k in length.
    """
    if not controls_framework:
        return chunks[:retrieve_k]
    target = str(controls_framework).strip().lower()
    matched = [chunk for chunk in chunks if _extract_framework_hint(chunk) == target]
    return matched[:retrieve_k]


def main() -> int:
    """Compare retrieval stages for a golden eval suite and write a report.

    Returns:
        Exit code: 0 for success, non-zero for failure.
    """
    parser = argparse.ArgumentParser(description="Compare raw retrieval stages for golden cases")
    parser.add_argument(
        "--golden-set",
        default="tests/evals/rag_golden_set.json",
        help="Path to the golden eval suite JSON file",
    )
    parser.add_argument(
        "--output",
        default="local_state/evals/retrieval_stage_compare_latest.json",
        help="Path to write stage comparison report JSON",
    )
    parser.add_argument(
        "--index-name",
        default=os.getenv("AZURE_SEARCH_INDEX_NAME")
        or os.getenv("SEARCH_INDEX_NAME")
        or "grounding-index",
        help="Qdrant collection name used for retrieval",
    )
    parser.add_argument(
        "--qdrant-url",
        default=os.getenv("QDRANT_URL", "http://localhost:6333"),
        help="Qdrant base URL",
    )
    parser.add_argument(
        "--raw-multiplier",
        type=int,
        default=8,
        help="Oversampling multiplier for raw integration retrieval before filtering",
    )
    args = parser.parse_args()

    cases = _load_cases(Path(args.golden_set))
    client = LocalQdrantSearchClient(index=args.index_name, qdrant_url=args.qdrant_url)
    svc = _CompareSvc(client)

    selected_evidence_corpora = controls_pipeline._resolve_evidence_corpora(None, None)
    selected_chunk_corpora = [corpus for corpus in selected_evidence_corpora if corpus != "a"]
    evidence_filter = controls_pipeline._build_evidence_corpus_filter(selected_chunk_corpora)

    rows: list[dict[str, Any]] = []
    for case in cases:
        raw_top = max(case.retrieve_k * max(1, args.raw_multiplier), 20)
        raw_results = client.search(query_text=case.question, top=raw_top)
        raw_chunks = [chunk for chunk in raw_results if isinstance(chunk, dict)]
        raw_filtered = _filter_explicit_framework(
            chunks=raw_chunks,
            controls_framework=case.controls_framework,
            retrieve_k=case.retrieve_k,
        )

        hybrid_chunks, hybrid_timings = search_pipeline._hybrid_search(
            case.question,
            retrieve_k=case.retrieve_k,
            evidence_filter=evidence_filter,
            svc=svc,
        )
        rebalanced_chunks, rebalance_debug = rag_pipeline._small_k_chunk_rebalance(
            chunks=list(hybrid_chunks),
            retrieve_k=case.retrieve_k,
            question=case.question,
            controls_framework=case.controls_framework,
        )

        rows.append(
            {
                "case_id": case.id,
                "question": case.question,
                "controls_framework": case.controls_framework,
                "expected_relevant_frameworks": case.expected_relevant_frameworks,
                "evidence_filter": evidence_filter,
                "hybrid_timings": hybrid_timings,
                "rebalance_debug": rebalance_debug,
                "stages": {
                    "raw_qdrant": {
                        "summary": _stage_summary(
                            chunks=raw_filtered,
                            expected=case.expected_relevant_frameworks,
                        ),
                        "top_k": raw_filtered,
                        "raw_candidate_count": len(raw_chunks),
                    },
                    "hybrid_filtered": {
                        "summary": _stage_summary(
                            chunks=hybrid_chunks,
                            expected=case.expected_relevant_frameworks,
                        ),
                        "top_k": hybrid_chunks,
                    },
                    "post_rebalance": {
                        "summary": _stage_summary(
                            chunks=rebalanced_chunks,
                            expected=case.expected_relevant_frameworks,
                        ),
                        "top_k": rebalanced_chunks,
                    },
                },
            }
        )

    summary = {
        "suite": args.golden_set,
        "index_name": args.index_name,
        "qdrant_url": args.qdrant_url,
        "evidence_filter": evidence_filter,
        "results": rows,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Report written: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
