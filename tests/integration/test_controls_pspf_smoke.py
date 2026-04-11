from __future__ import annotations

import os
import time

import pytest
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient

from runtime.ingestion.controls_index import ControlsIndexConfig, ensure_controls_index
from runtime.ingestion.parsers.pspf import PspfParser
from runtime.ingestion.publish_controls import upload_controls_records

pytestmark = [
    pytest.mark.integration,
    pytest.mark.private_network,
]


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@pytest.fixture(scope="session")
def smoke_enabled() -> None:
    if not _bool_env("PSPF_CONTROLS_SMOKE_RUN"):
        pytest.skip("Set PSPF_CONTROLS_SMOKE_RUN=1 to execute PSPF controls smoke test")


@pytest.fixture(scope="session")
def controls_config(smoke_enabled: None) -> ControlsIndexConfig:
    try:
        return ControlsIndexConfig.from_env()
    except ValueError as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="session")
def credential(smoke_enabled: None) -> DefaultAzureCredential:
    return DefaultAzureCredential()


def test_pspf_parse_and_publish_smoke(
    smoke_enabled: None,
    controls_config: ControlsIndexConfig,
    credential: DefaultAzureCredential,
):
    records = PspfParser().parse()
    assert len(records) >= 200

    ensure_controls_index(controls_config, credential)

    stamp = int(time.time())
    by_id = {record.requirement_id: record.to_dict() for record in records}
    chosen_ids = ["PSPF-0008", "PSPF-0217"]
    sample = []
    for index, requirement_id in enumerate(chosen_ids, start=1):
        base = dict(by_id[requirement_id])
        base["requirement_id"] = f"{requirement_id}-smoke-{stamp}-{index}"
        base["framework_version"] = f"{base['framework_version']} smoke {stamp}"
        base["source_section"] = f"{base['source_section']} > smoke"
        sample.append(base)

    client = SearchClient(
        endpoint=controls_config.search_endpoint,
        index_name=controls_config.controls_index_name,
        credential=credential,
    )

    try:
        result = upload_controls_records(
            controls_config,
            credential,
            sample,
            batch_size=10,
            replace_existing=False,
            dry_run=False,
        )

        assert result["records_uploaded"] == len(sample)

        for record in sample:
            response = list(
                client.search(
                    search_text="*",
                    filter=f"requirement_id eq '{record['requirement_id']}'",
                    top=5,
                )
            )
            assert response, f"Expected uploaded smoke record {record['requirement_id']}"
            stored = response[0]
            assert stored["framework"] == "PSPF"
            if record["requirement_id"].startswith("PSPF-0008"):
                assert "cso" in stored.get("keywords", [])
            if record["requirement_id"].startswith("PSPF-0217"):
                assert "sogs" in stored.get("keywords", [])
    finally:
        client.delete_documents([{"requirement_id": record["requirement_id"]} for record in sample])
