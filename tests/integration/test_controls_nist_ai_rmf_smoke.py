from __future__ import annotations

import os

import pytest

from runtime.ingestion.parsers.nist_ai_rmf import NistAiRmfParser

pytestmark = [
    pytest.mark.integration,
]


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@pytest.fixture(scope="session")
def smoke_enabled() -> None:
    if not _bool_env("NIST_AI_RMF_SMOKE_RUN"):
        pytest.skip("Set NIST_AI_RMF_SMOKE_RUN=1 to run NIST AI RMF smoke test")


@pytest.fixture(scope="session")
def records(smoke_enabled: None):
    parser = NistAiRmfParser(fetch_guidance=True)
    return parser.parse()


def test_record_count(records):
    assert len(records) >= 72, f"Expected >= 72 controls, got {len(records)}"


def test_govern_1_1_source_uri(records):
    by_id = {r.requirement_id: r for r in records}
    gov11 = by_id.get("NIST-AI-RMF-GOVERN-1-1")
    assert gov11 is not None, "Govern 1.1 record missing"
    assert (
        "govern/#govern-1-1" in gov11.source_uri
    ), f"Unexpected source_uri for Govern 1.1: {gov11.source_uri}"


def test_govern_1_1_guidance_text(records):
    by_id = {r.requirement_id: r for r in records}
    gov11 = by_id["NIST-AI-RMF-GOVERN-1-1"]
    assert gov11.guidance_text, "Govern 1.1 guidance_text should not be empty"
    assert len(gov11.guidance_text) > 20, "Govern 1.1 guidance_text unexpectedly short"


@pytest.mark.parametrize(
    "req_id,uri_fragment",
    [
        ("NIST-AI-RMF-GOVERN-6-2", "govern/#govern-6-2"),
        ("NIST-AI-RMF-MAP-1-1", "map/#map-1-1"),
        ("NIST-AI-RMF-MEASURE-1-1", "measure/#measure-1-1"),
        ("NIST-AI-RMF-MANAGE-4-3", "manage/#manage-4-3"),
    ],
)
def test_spot_check_source_uris(records, req_id: str, uri_fragment: str):
    by_id = {r.requirement_id: r for r in records}
    record = by_id.get(req_id)
    assert record is not None, f"Missing record: {req_id}"
    assert (
        uri_fragment in record.source_uri
    ), f"{req_id} source_uri {record.source_uri!r} does not contain {uri_fragment!r}"


def test_all_functions_represented(records):
    functions = {r.source_section.split()[0].upper() for r in records}
    for expected in ("GOVERN", "MAP", "MEASURE", "MANAGE"):
        assert expected in functions, f"No records found for function {expected}"


def test_framework_metadata(records):
    for r in records:
        assert r.framework == "NIST AI RMF"
        assert r.framework_version == "1.0"
