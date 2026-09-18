"""Golden-set retrieval evaluation runner for query-web /api/ask.

This runner supports:
- deterministic retrieval metrics for CI and local regression checks
- optional RAGAS answer-quality metrics when explicitly enabled
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import types
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib import error, request


@dataclass(frozen=True)
class GoldenCase:
    """Represents a single golden test case.

    Attributes:
        id: Unique identifier for the test case.
        question: The question to be sent to the /api/ask endpoint.
        retrieve_k: The number of top retrieval results to consider for evaluation.
        expected_relevant_frameworks: List of expected relevant framework keys for the question.
        controls_framework: Optional framework key to restrict retrieval to a specific framework.
        min_precision: Optional minimum precision@k threshold for the test case.
        min_diversity: Optional minimum framework diversity@k threshold for the test case.
        max_dominant_framework_share: Optional maximum share of the most frequently retrieved framework in the top-k results.
    """

    id: str
    question: str
    retrieve_k: int
    expected_relevant_frameworks: list[str]
    controls_framework: str | None
    evidence_corpora_include: list[str] | None
    evidence_corpora_exclude: list[str] | None
    expected_scope_mode: str | None
    expected_scope_mode_any: list[str] | None
    expected_in_scope_corpora: list[str] | None
    expected_retrieved_corpora: list[str] | None
    expected_in_scope_without_retrieval: list[str] | None
    min_precision: float | None
    min_diversity: int | None
    max_dominant_framework_share: float | None


@dataclass(frozen=True)
class CaseResult:
    """Represents the evaluation result for a single golden test case.

    Attributes:
        case_id: Unique identifier for the test case.
        retrieve_k: The number of top retrieval results considered for evaluation.
        precision_at_k: The precision@k score for the test case.
        dominant_framework_share: The share of the most frequently retrieved framework in the top-k results.
        framework_diversity_at_k: The number of unique frameworks represented in the top-k results.
        expected_framework_coverage: The proportion of expected relevant frameworks covered in the top-k results.
        effective_min_precision: The effective minimum precision@k threshold used for evaluation.
        effective_min_diversity: The effective minimum framework diversity@k threshold used for evaluation.
        effective_max_dominant_framework_share: The effective maximum share of the most frequently retrieved framework in the top-k results used for evaluation.
        pass_precision: Boolean indicating if the precision@k meets or exceeds the minimum threshold.
        pass_diversity: Boolean indicating if the framework diversity meets or exceeds the minimum threshold.
        pass_dominance: Boolean indicating if the dominant framework share is within the maximum threshold.
        failure_reasons: List of strings describing the reasons for failure, if any.
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
    pass_scope: bool
    expected_scope_mode: str | None
    observed_scope_mode: str | None
    failure_reasons: list[str]


def _normalise_corpora_list(values: Any) -> list[str]:
    """Normalise corpus identifiers into stable sorted a/b/c order.

    Any non-a/b/c values are ignored, and the returned list is sorted in a deterministic order.

    Args:
        values: A single corpus identifier or a list of identifiers to normalise.

    Returns:
        A list of corpus identifiers sorted in a deterministic order, containing only 'a', 'b', or 'c'.
    """
    if values is None:
        return []
    items = values if isinstance(values, list) else [values]
    parsed = {str(item or "").strip().lower() for item in items if str(item or "").strip()}
    order = {"a": 0, "b": 1, "c": 2}
    return sorted(parsed, key=lambda item: order.get(item, 99))


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON file from the given path and return its contents as a dictionary.

    Args:
        path: Path to the JSON file.

    Returns:
        Dictionary containing the JSON file contents.
    """
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _env_float(value: str | None, default: float) -> float:
    """Parse an optional environment variable as float."""
    if value is None or value == "":
        return default
    return float(value)


def _env_int(value: str | None, default: int) -> int:
    """Parse an optional environment variable as int."""
    if value is None or value == "":
        return default
    return int(value)


def _normalise_ollama_model_name(*, model: str, base_url: str) -> str:
    """Map common Ollama-prefixed aliases to plain model names.

    Ollama's OpenAI-compatible /v1 endpoint expects model names such as
    ``qwen2.5:7b-instruct`` and does not recognise prefixes like
    ``ollama_chat/`` used by some adapter examples.

    Args:
        model: The model name to normalise.
        base_url: The base URL of the LLM endpoint, used to detect Ollama-like endpoints.

    Returns:
        The normalised model name without Ollama-specific prefixes.
    """
    model_name = model.strip()
    if not model_name:
        return model_name

    url = base_url.lower()
    is_ollama_like = "11434" in url or "ollama" in url
    if not is_ollama_like:
        return model_name

    for prefix in ("ollama_chat/", "ollama/"):
        if model_name.startswith(prefix):
            return model_name[len(prefix) :]

    return model_name


def _print_ragas_precheck(*, enabled: bool) -> None:
    """Print effective RAGAS evaluator configuration for easier troubleshooting.

    Args:
        enabled: Whether the RAGAS evaluator is enabled.
    """
    if not enabled:
        return

    model = str(os.getenv("RAGAS_LLM_MODEL") or "").strip()
    base_url = str(os.getenv("RAGAS_LLM_BASE_URL") or "").strip()
    provider = str(os.getenv("RAGAS_LLM_PROVIDER") or "openai").strip().lower()
    adapter = str(os.getenv("RAGAS_LLM_ADAPTER") or "auto").strip().lower()
    max_tokens = _env_int(os.getenv("RAGAS_LLM_MAX_TOKENS"), 4096)
    temperature = _env_float(os.getenv("RAGAS_LLM_TEMPERATURE"), 0.0)
    api_key_present = bool(
        str(os.getenv("RAGAS_LLM_API_KEY") or "").strip()
        or str(os.getenv("OPENAI_API_KEY") or "").strip()
        or str(os.getenv("OPENAI_ADMIN_KEY") or "").strip()
    )

    print("RAGAS pre-check:", file=sys.stderr)
    print(f"- python_executable={sys.executable}", file=sys.stderr)
    print(f"- RAGAS_LLM_MODEL={model or '<unset>'}", file=sys.stderr)
    print(f"- RAGAS_LLM_BASE_URL={base_url or '<unset>'}", file=sys.stderr)
    print(f"- RAGAS_LLM_PROVIDER={provider}", file=sys.stderr)
    print(f"- RAGAS_LLM_ADAPTER={adapter}", file=sys.stderr)
    print(f"- RAGAS_LLM_MAX_TOKENS={max_tokens}", file=sys.stderr)
    print(f"- RAGAS_LLM_TEMPERATURE={temperature}", file=sys.stderr)
    print(f"- api_key_present={'yes' if api_key_present else 'no'}", file=sys.stderr)
    if base_url and not model:
        print(
            "- warning: RAGAS_LLM_BASE_URL is set but RAGAS_LLM_MODEL is unset",
            file=sys.stderr,
        )


def _load_cases(path: Path) -> list[GoldenCase]:
    """Load and return the golden test cases from a JSON file.

    Args:
        path: Path to the JSON file containing the golden test cases.

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
                evidence_corpora_include=(
                    _normalise_corpora_list(item.get("evidence_corpora_include"))
                    if item.get("evidence_corpora_include") is not None
                    else None
                ),
                evidence_corpora_exclude=(
                    _normalise_corpora_list(item.get("evidence_corpora_exclude"))
                    if item.get("evidence_corpora_exclude") is not None
                    else None
                ),
                expected_scope_mode=(
                    str(item["expected_scope_mode"]).strip().lower()
                    if item.get("expected_scope_mode") not in {None, ""}
                    else None
                ),
                expected_scope_mode_any=(
                    [
                        str(v).strip().lower()
                        for v in _normalise_corpora_list(item.get("expected_scope_mode_any"))
                    ]
                    if item.get("expected_scope_mode_any") is not None
                    else None
                ),
                expected_in_scope_corpora=(
                    _normalise_corpora_list(item.get("expected_in_scope_corpora"))
                    if item.get("expected_in_scope_corpora") is not None
                    else None
                ),
                expected_retrieved_corpora=(
                    _normalise_corpora_list(item.get("expected_retrieved_corpora"))
                    if item.get("expected_retrieved_corpora") is not None
                    else None
                ),
                expected_in_scope_without_retrieval=(
                    _normalise_corpora_list(item.get("expected_in_scope_without_retrieval"))
                    if item.get("expected_in_scope_without_retrieval") is not None
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
    """Resolve per-case thresholds with framework-scoped defaults.

    Explicit framework-scoped cases should not be penalised for low diversity.

    Args:
        case: The GoldenCase instance being evaluated.
        default_min_precision: The default minimum precision@k threshold.
        default_min_diversity: The default minimum framework diversity@k threshold.

    Returns:
        A tuple containing the effective minimum precision and diversity thresholds for the case.
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
    """Extract a framework hint from a retrieved chunk's metadata.

    Args:
        chunk: A dictionary representing a retrieved chunk, expected to contain metadata fields.

    Returns:
        A string representing the inferred framework key based on the chunk's metadata.
        Returns "unknown" if no recognisable framework hint is found.
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


def _call_ask_endpoint(
    *,
    base_url: str,
    auth_token: str | None,
    case: GoldenCase,
    timeout_s: int,
    request_retries: int,
    retry_backoff_s: float,
) -> dict[str, Any]:
    """Call the /api/ask endpoint with the given golden case and return the response.

    Args:
        base_url: The base URL of the query-web service.
        auth_token: Optional bearer token for authentication.
        case: The GoldenCase instance containing the question and retrieval parameters.
        timeout_s: Timeout in seconds for the HTTP request.
        request_retries: Number of retries for transient network/server errors.
        retry_backoff_s: Base retry backoff in seconds.

    Returns:
        A dictionary representing the JSON response from the /api/ask endpoint.
    Raises:
        RuntimeError: If the HTTP request fails or returns a non-200 status code.
    """
    payload: dict[str, Any] = {
        "question": case.question,
        "retrieve_k": case.retrieve_k,
        "temperature": 0.2,
        "controls_semantic": True,
    }
    if case.controls_framework:
        payload["controls_framework"] = case.controls_framework
    if case.evidence_corpora_include is not None:
        payload["evidence_corpora_include"] = case.evidence_corpora_include
    if case.evidence_corpora_exclude is not None:
        payload["evidence_corpora_exclude"] = case.evidence_corpora_exclude

    req = request.Request(
        f"{base_url.rstrip('/')}/api/ask",
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    if auth_token:
        req.add_header("Authorization", f"Bearer {auth_token}")

    attempts = max(1, int(request_retries) + 1)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            # Retry transient server-side HTTP failures.
            if exc.code >= 500 and attempt < attempts:
                last_error = RuntimeError(
                    f"/api/ask failed ({exc.code}) for case={case.id}: {body}"
                )
                time.sleep(retry_backoff_s * attempt)
                continue
            raise RuntimeError(f"/api/ask failed ({exc.code}) for case={case.id}: {body}") from exc
        except (error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(retry_backoff_s * attempt)
                continue
            raise RuntimeError(f"/api/ask unreachable for case={case.id}: {exc}") from exc

    raise RuntimeError(f"/api/ask unreachable for case={case.id}: {last_error or 'unknown error'}")


def _error_case_result(
    *,
    case: GoldenCase,
    error_message: str,
    default_min_precision: float,
    default_min_diversity: int,
) -> CaseResult:
    """Create a failed case result when retrieval request itself fails.

    Args:
        case: The GoldenCase instance being evaluated.
        error_message: The error message describing the failure.
        default_min_precision: The default minimum precision@k threshold.
        default_min_diversity: The default minimum framework diversity@k threshold.

    Returns:
        A CaseResult instance representing the failed case.
    """
    effective_min_precision, effective_min_diversity = _effective_thresholds(
        case=case,
        default_min_precision=default_min_precision,
        default_min_diversity=default_min_diversity,
    )
    return CaseResult(
        case_id=case.id,
        retrieve_k=case.retrieve_k,
        precision_at_k=0.0,
        dominant_framework_share=0.0,
        framework_diversity_at_k=0,
        expected_framework_coverage=0.0,
        effective_min_precision=round(effective_min_precision, 4),
        effective_min_diversity=effective_min_diversity,
        effective_max_dominant_framework_share=(
            round(case.max_dominant_framework_share, 4)
            if case.max_dominant_framework_share is not None
            else None
        ),
        pass_precision=False,
        pass_diversity=False,
        pass_dominance=False,
        pass_scope=False,
        expected_scope_mode=case.expected_scope_mode,
        observed_scope_mode=None,
        failure_reasons=[f"request_error: {error_message}"],
    )


def _score_case(
    *,
    case: GoldenCase,
    response: dict[str, Any],
    chunks: list[dict[str, Any]],
    default_min_precision: float,
    default_min_diversity: int,
) -> CaseResult:
    """Score a single golden case based on retrieved chunks and evaluation metrics.

    Args:
        case: The GoldenCase instance being evaluated.
        chunks: List of retrieved chunks from the /api/ask response.
        default_min_precision: The default minimum precision@k threshold.
        default_min_diversity: The default minimum framework diversity@k threshold.

    Returns:
        A CaseResult instance containing the evaluation metrics and pass/fail status.
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

    audit = response.get("audit") if isinstance(response, dict) else {}
    audit = audit if isinstance(audit, dict) else {}
    scope_profile_raw = audit.get("scope_profile")
    scope_profile: dict[str, Any] = scope_profile_raw if isinstance(scope_profile_raw, dict) else {}
    observed_scope_mode = str(audit.get("scope_mode") or "").strip().lower() or None
    observed_in_scope = _normalise_corpora_list(scope_profile.get("in_scope_corpora"))
    observed_retrieved = _normalise_corpora_list(scope_profile.get("retrieved_corpora"))
    observed_gaps = _normalise_corpora_list(scope_profile.get("in_scope_without_retrieval"))

    if case.expected_scope_mode_any is not None:
        pass_scope_mode = observed_scope_mode in set(case.expected_scope_mode_any)
    elif case.expected_scope_mode is None:
        pass_scope_mode = True
    else:
        pass_scope_mode = observed_scope_mode == case.expected_scope_mode
    if not pass_scope_mode:
        expected_mode_desc = (
            f"one_of={case.expected_scope_mode_any}"
            if case.expected_scope_mode_any is not None
            else f"expected={case.expected_scope_mode}"
        )
        failure_reasons.append(
            "scope_mode_mismatch"
            f" ({expected_mode_desc}, actual={observed_scope_mode or 'missing'})"
        )

    pass_in_scope = (
        True
        if case.expected_in_scope_corpora is None
        else observed_in_scope == case.expected_in_scope_corpora
    )
    if not pass_in_scope:
        failure_reasons.append(
            "in_scope_corpora_mismatch"
            f" (expected={case.expected_in_scope_corpora}, actual={observed_in_scope})"
        )

    pass_retrieved_scope = (
        True
        if case.expected_retrieved_corpora is None
        else observed_retrieved == case.expected_retrieved_corpora
    )
    if not pass_retrieved_scope:
        failure_reasons.append(
            "retrieved_corpora_mismatch"
            f" (expected={case.expected_retrieved_corpora}, actual={observed_retrieved})"
        )

    pass_scope_gaps = (
        True
        if case.expected_in_scope_without_retrieval is None
        else observed_gaps == case.expected_in_scope_without_retrieval
    )
    if not pass_scope_gaps:
        failure_reasons.append(
            "in_scope_without_retrieval_mismatch"
            f" (expected={case.expected_in_scope_without_retrieval}, actual={observed_gaps})"
        )

    pass_scope = pass_scope_mode and pass_in_scope and pass_retrieved_scope and pass_scope_gaps

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
        pass_scope=pass_scope,
        expected_scope_mode=case.expected_scope_mode,
        observed_scope_mode=observed_scope_mode,
        failure_reasons=failure_reasons,
    )


def _maybe_run_ragas(
    *,
    enabled: bool,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run RAGAS answer quality metrics if enabled and return the summary.

    Args:
        enabled: Boolean indicating whether to run RAGAS metrics.
        results: List of dictionaries containing the detailed results for each case.
    Returns:
        A dictionary summarising the RAGAS evaluation results, including metrics and sample count.
    """
    if not enabled:
        return {"enabled": False, "reason": "disabled"}

    # Ragas<=0.4 imports ChatVertexAI from a path removed in newer
    # langchain-community releases. Provide a shim so optional RAGAS
    # evaluation remains usable without forcing broad dependency downgrades.
    has_vertexai_shim_target = True
    try:
        has_vertexai_shim_target = (
            importlib.util.find_spec("langchain_community.chat_models.vertexai") is not None
        )
    except ModuleNotFoundError:
        has_vertexai_shim_target = False

    if not has_vertexai_shim_target:
        try:
            from langchain_google_vertexai import ChatVertexAI  # type: ignore
        except ImportError:

            class ChatVertexAI:  # type: ignore
                """Fallback placeholder for optional VertexAI integrations."""

                pass

        shim = types.ModuleType("langchain_community.chat_models.vertexai")
        setattr(shim, "ChatVertexAI", ChatVertexAI)
        sys.modules["langchain_community.chat_models.vertexai"] = shim

    try:
        from ragas import evaluate
        from ragas.dataset_schema import EvaluationDataset, EvaluationResult, SingleTurnSample
        from ragas.llms import llm_factory
        from ragas.metrics._faithfulness import Faithfulness as LegacyFaithfulness
    except ImportError as exc:
        return {"enabled": False, "reason": "ragas_import_error", "detail": str(exc)}

    ragas_llm = None
    ragas_llm_model = str(os.getenv("RAGAS_LLM_MODEL") or "").strip()
    ragas_llm_provider = str(os.getenv("RAGAS_LLM_PROVIDER") or "openai").strip().lower()
    ragas_llm_adapter = str(os.getenv("RAGAS_LLM_ADAPTER") or "auto").strip().lower()
    ragas_llm_base_url = str(os.getenv("RAGAS_LLM_BASE_URL") or "").strip()
    ragas_llm_model = _normalise_ollama_model_name(
        model=ragas_llm_model,
        base_url=ragas_llm_base_url,
    )
    ragas_llm_temperature = os.getenv("RAGAS_LLM_TEMPERATURE")
    ragas_llm_max_tokens = os.getenv("RAGAS_LLM_MAX_TOKENS")
    ragas_llm_api_key = (
        str(os.getenv("RAGAS_LLM_API_KEY") or "").strip()
        or str(os.getenv("OPENAI_API_KEY") or "").strip()
        or str(os.getenv("OPENAI_ADMIN_KEY") or "").strip()
    )
    if not ragas_llm_api_key and ragas_llm_base_url:
        # Most local OpenAI-compatible gateways accept any non-empty key.
        ragas_llm_api_key = "local"
    if not ragas_llm_api_key:
        return {
            "enabled": False,
            "reason": "ragas_credentials_missing",
            "detail": (
                "Set RAGAS_LLM_API_KEY (or OPENAI_API_KEY/OPENAI_ADMIN_KEY). "
                "For local OpenAI-compatible endpoints, set RAGAS_LLM_BASE_URL and "
                "RAGAS_LLM_MODEL as well."
            ),
        }

    if not ragas_llm_model:
        if ragas_llm_base_url:
            return {
                "enabled": False,
                "reason": "ragas_llm_config_missing",
                "detail": (
                    "RAGAS_LLM_MODEL is required when RAGAS_LLM_BASE_URL is set "
                    "(local/custom evaluator endpoint)."
                ),
            }
        # Sensible hosted-default when using OPENAI_API_KEY/OPENAI_ADMIN_KEY.
        ragas_llm_model = "gpt-4o-mini"

    try:
        from openai import OpenAI

        client_kwargs: dict[str, Any] = {"api_key": ragas_llm_api_key}
        if ragas_llm_base_url:
            client_kwargs["base_url"] = ragas_llm_base_url
        llm_kwargs: dict[str, Any] = {}
        llm_kwargs["temperature"] = _env_float(ragas_llm_temperature, 0.0)
        llm_kwargs["max_tokens"] = _env_int(ragas_llm_max_tokens, 4096)
        ragas_llm = llm_factory(
            model=ragas_llm_model,
            provider=ragas_llm_provider,
            client=OpenAI(**client_kwargs),
            adapter=ragas_llm_adapter,
            **llm_kwargs,
        )
    except Exception as exc:  # pragma: no cover - external provider/runtime variability
        return {
            "enabled": False,
            "reason": "ragas_llm_init_error",
            "detail": str(exc),
        }

    samples: list[SingleTurnSample] = []
    for item in results:
        response = item.get("response") or {}
        answer = str(response.get("answer") or "").strip()
        contexts = [str(c.get("content") or "") for c in (response.get("results") or []) if c]
        if not answer or not contexts:
            continue
        samples.append(
            SingleTurnSample(
                user_input=str(item["question"]),
                response=answer,
                retrieved_contexts=contexts,
            )
        )

    if not samples:
        return {"enabled": True, "reason": "no_samples"}

    dataset = EvaluationDataset.from_list([sample.to_dict() for sample in samples])
    ragas_llm_for_eval = cast(Any, ragas_llm)
    metrics_for_eval = [cast(Any, LegacyFaithfulness())]

    try:
        ragas_result = cast(
            EvaluationResult,
            evaluate(
                dataset=dataset,
                metrics=cast(Any, metrics_for_eval),
                llm=ragas_llm_for_eval,
                return_executor=False,
            ),
        )
        scores = ragas_result.to_pandas().mean(numeric_only=True).to_dict()
        return {
            "enabled": True,
            "metrics": {k: round(float(v), 4) for k, v in scores.items()},
            "sample_count": len(samples),
        }
    except Exception as exc:  # pragma: no cover - external provider/runtime variability
        return {"enabled": False, "reason": "ragas_eval_error", "detail": str(exc)}


def main() -> int:
    """Main entry point for the golden-set RAG retrieval evaluation runner.

    Returns:
        Exit code indicating success (0) or failure (non-zero).
    """
    parser = argparse.ArgumentParser(description="Run golden-set RAG retrieval evaluations")
    parser.add_argument(
        "--golden-set",
        default="tests/evals/rag_golden_set.json",
        help="Path to the golden eval suite JSON file",
    )
    parser.add_argument(
        "--output",
        default="local_state/evals/rag_eval_latest.json",
        help="Path to write eval report JSON",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("QUERY_WEB_BASE_URL", ""),
        help="Base URL for live query-web endpoint (e.g. https://query-web.example)",
    )
    parser.add_argument(
        "--auth-token",
        default=os.getenv("QUERY_WEB_AUTH_TOKEN", ""),
        help="Optional bearer token for query-web auth",
    )
    parser.add_argument(
        "--timeout-s",
        type=int,
        default=int(os.getenv("QUERY_WEB_TIMEOUT_S", "45")),
        help="HTTP timeout in seconds",
    )
    parser.add_argument(
        "--request-retries",
        type=int,
        default=int(os.getenv("QUERY_WEB_REQUEST_RETRIES", "2")),
        help="Retries for transient /api/ask failures (timeouts, connection errors, HTTP 5xx)",
    )
    parser.add_argument(
        "--retry-backoff-s",
        type=float,
        default=float(os.getenv("QUERY_WEB_RETRY_BACKOFF_S", "1.5")),
        help="Base backoff in seconds between /api/ask retries",
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
    parser.add_argument(
        "--enable-ragas",
        action="store_true",
        help="Run optional RAGAS answer quality metrics",
    )

    args = parser.parse_args()
    _print_ragas_precheck(enabled=args.enable_ragas)

    if not args.base_url:
        print("QUERY_WEB_BASE_URL is required (or pass --base-url)", file=sys.stderr)
        return 2

    cases = _load_cases(Path(args.golden_set))

    detailed_rows: list[dict[str, Any]] = []
    case_scores: list[CaseResult] = []
    for case in cases:
        try:
            response = _call_ask_endpoint(
                base_url=args.base_url,
                auth_token=args.auth_token or None,
                case=case,
                timeout_s=args.timeout_s,
                request_retries=args.request_retries,
                retry_backoff_s=args.retry_backoff_s,
            )
            chunks = [c for c in (response.get("results") or []) if isinstance(c, dict)]
            scored = _score_case(
                case=case,
                response=response,
                chunks=chunks,
                default_min_precision=args.min_precision,
                default_min_diversity=args.min_diversity,
            )
        except RuntimeError as exc:
            response = {"error": str(exc), "results": [], "answer": ""}
            scored = _error_case_result(
                case=case,
                error_message=str(exc),
                default_min_precision=args.min_precision,
                default_min_diversity=args.min_diversity,
            )

        case_scores.append(scored)
        detailed_rows.append(
            {
                "case_id": case.id,
                "question": case.question,
                "response": response,
                "score": scored.__dict__,
            }
        )

    failed = [
        s
        for s in case_scores
        if not (s.pass_precision and s.pass_diversity and s.pass_dominance and s.pass_scope)
    ]

    ragas_summary = _maybe_run_ragas(enabled=args.enable_ragas, results=detailed_rows)

    summary = {
        "suite": args.golden_set,
        "base_url": args.base_url,
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
        },
        "ragas": ragas_summary,
        "results": detailed_rows,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary["totals"], indent=2))
    if ragas_summary.get("enabled") is False:
        ragas_reason = str(ragas_summary.get("reason") or "unknown")
        ragas_detail = str(ragas_summary.get("detail") or "")
        if ragas_reason == "ragas_import_error":
            print(
                "RAGAS disabled due to import error:",
                ragas_detail or "unknown import error",
                file=sys.stderr,
            )
        elif ragas_reason == "ragas_eval_error":
            print(
                "RAGAS disabled due to evaluation runtime error:",
                ragas_detail or "unknown evaluation error",
                file=sys.stderr,
            )
        elif ragas_reason == "ragas_credentials_missing":
            print(
                "RAGAS disabled due to missing evaluator credentials:",
                ragas_detail or "missing OPENAI_API_KEY/OPENAI_ADMIN_KEY",
                file=sys.stderr,
            )
        elif ragas_reason == "ragas_llm_init_error":
            print(
                "RAGAS disabled due to evaluator LLM init error:",
                ragas_detail or "unknown llm_factory init error",
                file=sys.stderr,
            )
    if failed:
        print("Failed cases:", ", ".join(s.case_id for s in failed), file=sys.stderr)
        for failed_case in failed:
            details = "; ".join(failed_case.failure_reasons) or "no failure reason captured"
            print(f"- {failed_case.case_id}: {details}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
