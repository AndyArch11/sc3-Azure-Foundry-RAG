"""Unit tests for query_web/endpoints/graph.py."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from query_web.endpoints.graph import GraphBuildRequest, register_graph_endpoints


class _DummyObjectStorageClient:
    def __init__(
        self, *, objects: dict[str, bytes], metadata: dict[str, dict[str, object]]
    ) -> None:
        self._objects = objects
        self._metadata = metadata
        self.put_calls: list[tuple[str, str, bytes, dict[str, str]]] = []

    def put_object(
        self,
        bucket_or_container: str,
        key: str,
        data: bytes,
        metadata: dict[str, str] | None = None,
    ) -> None:
        resolved_metadata = dict(metadata or {})
        self.put_calls.append((bucket_or_container, key, data, resolved_metadata))
        self._objects[f"{bucket_or_container}/{key}"] = data
        self._metadata[f"{bucket_or_container}/{key}"] = {
            "content_length": len(data),
            "last_modified": "2026-07-06T12:00:02+00:00",
            **resolved_metadata,
        }

    def get_object_metadata(self, bucket_or_container: str, key: str) -> dict[str, object]:
        return dict(self._metadata[f"{bucket_or_container}/{key}"])

    def get_object(self, bucket_or_container: str, key: str) -> bytes:
        return self._objects[f"{bucket_or_container}/{key}"]


class _DummyControlsSearchClient:
    def __init__(self, count: int) -> None:
        self._count = count

    def search(self, **kwargs: object):  # pragma: no cover - invoked by endpoint only
        count = self._count

        class _Pager(list):
            def get_count(self) -> int:
                return count

        return _Pager([])


class _DummyPresenceSearchClient:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self._items = list(items)

    def search(self, **kwargs: object):  # pragma: no cover - invoked by endpoint only
        del kwargs
        return list(self._items)


def _config(
    *,
    enabled: bool = True,
    backend: str = "local",
    azure_emulation_enabled: bool = False,
    azure_artifacts_dir: str = "",
    graph_azure_artifacts_container: str = "",
    graph_azure_artifacts_prefix: str = "",
    graph_azure_publish_enabled: bool = False,
    graph_aws_artifacts_bucket: str = "",
    graph_aws_artifacts_prefix: str = "",
    graph_aws_publish_enabled: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        graph_enabled=enabled,
        graph_backend=backend,
        graph_azure_emulation_enabled=azure_emulation_enabled,
        graph_azure_artifacts_dir=azure_artifacts_dir,
        graph_azure_artifacts_container=graph_azure_artifacts_container,
        graph_azure_artifacts_prefix=graph_azure_artifacts_prefix,
        graph_azure_publish_enabled=graph_azure_publish_enabled,
        graph_aws_artifacts_bucket=graph_aws_artifacts_bucket,
        graph_aws_artifacts_prefix=graph_aws_artifacts_prefix,
        graph_aws_publish_enabled=graph_aws_publish_enabled,
        graph_size_small_edges=50000,
        graph_size_large_edges=250000,
        graph_guidance_threshold_small=0.25,
        graph_guidance_threshold_medium=0.65,
        graph_guidance_threshold_large=0.75,
        graph_traversal_max_edges=10000,
        graph_traversal_max_payload_bytes=2 * 1024 * 1024,
    )


def _controls() -> list[dict[str, object]]:
    return [
        {
            "requirement_id": "NIST-CSF-GV-GV-OC-01",
            "framework": "NIST CSF",
            "framework_version": "2.0",
            "control_family": "GV.OC",
            "requirement_text": "Mission and stakeholders are understood.",
            "guidance_text": "Guidance for mission and stakeholders.",
            "source_uri": "https://example.com/nist-csf",
        }
    ]


def _chunks() -> list[dict[str, object]]:
    return [
        {
            "source_name": "guidance-1.md",
            "source_path": "/tmp/guidance-1.md",
            "normalised_text_sha256": "abc123",
            "content_sha256": "",
            "original_filename": "guidance-1.md",
            "corpus": "b",
            "corpus_role": "narrative_guidance",
            "content": "Mission and stakeholder context for cybersecurity risk management.",
            "score": 0.8,
        }
    ]


def _build_app(
    tmp_path,
    *,
    graph_enabled: bool = True,
    graph_backend: str = "local",
    graph_azure_emulation_enabled: bool = False,
    graph_azure_artifacts_dir: str = "",
    graph_azure_artifacts_container: str = "",
    graph_azure_artifacts_prefix: str = "",
    graph_azure_publish_enabled: bool = False,
    graph_azure_storage_client: object | None = None,
    graph_aws_artifacts_bucket: str = "",
    graph_aws_artifacts_prefix: str = "",
    graph_aws_publish_enabled: bool = False,
    graph_aws_storage_client: object | None = None,
) -> FastAPI:
    app = FastAPI()
    register_graph_endpoints(
        app,
        deps={
            "config": lambda: _config(
                enabled=graph_enabled,
                backend=graph_backend,
                azure_emulation_enabled=graph_azure_emulation_enabled,
                azure_artifacts_dir=graph_azure_artifacts_dir,
                graph_azure_artifacts_container=graph_azure_artifacts_container,
                graph_azure_artifacts_prefix=graph_azure_artifacts_prefix,
                graph_azure_publish_enabled=graph_azure_publish_enabled,
                graph_aws_artifacts_bucket=graph_aws_artifacts_bucket,
                graph_aws_artifacts_prefix=graph_aws_artifacts_prefix,
                graph_aws_publish_enabled=graph_aws_publish_enabled,
            ),
            "_is_authorised_request": lambda: (lambda auth_token, request: auth_token == "ok"),
            "_unauthorised_message": lambda: (lambda request: "Unauthorised."),
            "graph_azure_storage_client": lambda: graph_azure_storage_client,
            "graph_aws_storage_client": lambda: graph_aws_storage_client,
        },
    )
    return app


def test_graph_endpoints_build_query_and_export(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(tmp_path / "graph.sqlite"))
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    app = _build_app(tmp_path, graph_enabled=True)
    client = TestClient(app)

    build_response = client.post(
        "/api/graph/build",
        json={
            "auth_token": "ok",
            "controls": _controls(),
            "chunks": _chunks(),
            "persist_store": True,
        },
    )
    assert build_response.status_code == 200
    payload = build_response.json()
    assert payload["status"] == "ok"
    assert payload["report"]["output"]["nodes_total"] > 0
    assert payload["report"]["community_settings"]["min_community_size"] == 3
    assert payload["audit"]["operation"] == "build"
    assert payload["audit"]["outcome"] == "success"

    node_response = client.get(
        "/api/graph/nodes/a:nist-csf-gv-gv-oc-01", params={"auth_token": "ok"}
    )
    assert node_response.status_code == 200
    assert node_response.json()["node_id"] == "a:nist-csf-gv-gv-oc-01"
    assert node_response.json()["audit"]["operation"] == "node_get"

    related_response = client.get(
        "/api/graph/related",
        params={"auth_token": "ok", "node_id": "a:nist-csf-gv-gv-oc-01", "depth": 2},
    )
    assert related_response.status_code == 200
    assert related_response.json()["nodes"]
    assert related_response.json()["audit"]["operation"] == "related"

    export_response = client.get("/api/graph/export", params={"auth_token": "ok", "format": "json"})
    assert export_response.status_code == 200
    export_payload = export_response.json()
    assert "nodes" in export_payload and "edges" in export_payload
    assert "counts" in export_payload
    assert export_payload["audit"]["operation"] == "export"


def test_graph_query_supports_bounded_agent_filters(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(tmp_path / "graph.sqlite"))
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    app = _build_app(tmp_path, graph_enabled=True)
    client = TestClient(app)
    build_response = client.post(
        "/api/graph/build",
        json={"auth_token": "ok", "controls": _controls(), "chunks": _chunks()},
    )
    assert build_response.status_code == 200

    response = client.post(
        "/api/graph/query",
        json={
            "auth_token": "ok",
            "seed_node_id": "a:nist-csf-gv-gv-oc-01",
            "depth": 2,
            "max_nodes": 20,
            "max_edges": 20,
            "framework": "NIST CSF",
            "community": "Community 1",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["nodes"]) <= 20
    assert len(payload["edges"]) <= 20
    assert payload["limits"] == {"max_nodes": 20, "max_edges": 20}
    assert "truncated" in payload
    assert payload["audit"]["operation"] == "query"


def test_graph_related_returns_404_for_missing_seed_node(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(tmp_path / "graph.sqlite"))
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    app = _build_app(tmp_path, graph_enabled=True)
    client = TestClient(app)

    build_response = client.post(
        "/api/graph/build",
        json={
            "auth_token": "ok",
            "controls": _controls(),
            "chunks": _chunks(),
            "persist_store": True,
        },
    )
    assert build_response.status_code == 200

    related_response = client.get(
        "/api/graph/related",
        params={"auth_token": "ok", "node_id": "missing-node-id", "depth": 2},
    )
    assert related_response.status_code == 404
    payload = related_response.json()
    assert payload["title"] == "Not Found"
    assert "Seed node not found" in payload["detail"]
    assert payload["requested_node_id"] == "missing-node-id"


def test_graph_related_resolves_seed_node_case_insensitively(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(tmp_path / "graph.sqlite"))
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    app = _build_app(tmp_path, graph_enabled=True)
    client = TestClient(app)

    build_response = client.post(
        "/api/graph/build",
        json={
            "auth_token": "ok",
            "controls": _controls(),
            "chunks": _chunks(),
            "persist_store": True,
        },
    )
    assert build_response.status_code == 200

    related_response = client.get(
        "/api/graph/related",
        params={
            "auth_token": "ok",
            "node_id": "A:NIST-CSF-GV-GV-OC-01",
            "depth": 2,
        },
    )
    assert related_response.status_code == 200
    payload = related_response.json()
    assert payload.get("nodes")
    assert (
        payload.get("audit", {}).get("details", {}).get("resolved_node_id")
        == "a:nist-csf-gv-gv-oc-01"
    )


def test_graph_related_resolves_unprefixed_requirement_id(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(tmp_path / "graph.sqlite"))
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    app = _build_app(tmp_path, graph_enabled=True)
    client = TestClient(app)

    build_response = client.post(
        "/api/graph/build",
        json={
            "auth_token": "ok",
            "controls": _controls(),
            "chunks": _chunks(),
            "persist_store": True,
        },
    )
    assert build_response.status_code == 200

    related_response = client.get(
        "/api/graph/related",
        params={
            "auth_token": "ok",
            "node_id": "NIST-CSF-GV-GV-OC-01",
            "depth": 2,
        },
    )
    assert related_response.status_code == 200
    payload = related_response.json()
    assert payload.get("nodes")
    assert (
        payload.get("audit", {}).get("details", {}).get("resolved_node_id")
        == "a:nist-csf-gv-gv-oc-01"
    )


def test_graph_status_includes_last_successful_build_timestamp(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(tmp_path / "graph.sqlite"))
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    app = _build_app(tmp_path, graph_enabled=True)
    client = TestClient(app)

    build_response = client.post(
        "/api/graph/build",
        json={
            "auth_token": "ok",
            "controls": _controls(),
            "chunks": _chunks(),
            "persist_store": True,
        },
    )
    assert build_response.status_code == 200
    built_at = str(build_response.json()["report"]["built_at"])
    assert built_at

    status_response = client.get("/api/graph/status", params={"auth_token": "ok"})
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert str(status_payload.get("last_successful_build_at") or "") == built_at


def test_graph_build_hyper_connected_indicator_ignores_structural_hubs(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(tmp_path / "graph.sqlite"))
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    app = _build_app(tmp_path, graph_enabled=True)
    client = TestClient(app)

    controls = []
    for idx in range(60):
        controls.append(
            {
                "requirement_id": f"NIST-CSF-GV-GV-OC-{idx:02d}",
                "framework": "NIST CSF",
                "framework_version": "2.0",
                "control_family": "GV.OC",
                "requirement_text": "Mission and stakeholders are understood.",
                "guidance_text": "Governance requirements are documented.",
                "source_uri": "https://example.com/nist-csf",
            }
        )

    build_response = client.post(
        "/api/graph/build",
        json={
            "auth_token": "ok",
            "controls": controls,
            "chunks": [],
            "persist_store": True,
            "min_community_size": 3,
        },
    )
    assert build_response.status_code == 200
    payload = build_response.json()
    indicator = payload.get("hyper_connected_indicator") or {}

    assert indicator.get("ignored_structural_edges", 0) > 0
    assert indicator.get("detected") is False
    warning = str(indicator.get("warning") or "")
    assert warning.startswith(
        "No hyper-connected non-structural node concentration detected"
    ) or warning.startswith("No connected non-structural nodes detected in edge set")


def test_graph_build_hyper_connected_indicator_requires_threshold_hit(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(tmp_path / "graph.sqlite"))
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    app = _build_app(tmp_path, graph_enabled=True)
    client = TestClient(app)

    controls = [
        {
            "requirement_id": "NIST-CSF-PR-AT-01",
            "framework": "NIST CSF",
            "framework_version": "2.0",
            "control_family": "PR.AT",
            "requirement_text": "Security awareness training is performed.",
            "guidance_text": "Training is documented and reviewed.",
            "source_uri": "https://example.com/nist-csf",
        }
    ]
    chunks = []
    for idx in range(13):
        chunks.append(
            {
                "source_name": f"guidance-{idx}.md",
                "source_path": f"/tmp/guidance-{idx}.md",
                "normalised_text_sha256": f"sha-{idx}",
                "content_sha256": "",
                "original_filename": f"guidance-{idx}.md",
                "corpus": "b",
                "corpus_role": "narrative_guidance",
                "content": "Security awareness training is documented and reviewed regularly.",
                "score": 0.9,
            }
        )

    build_response = client.post(
        "/api/graph/build",
        json={
            "auth_token": "ok",
            "controls": controls,
            "chunks": chunks,
            "persist_store": True,
            "min_community_size": 3,
        },
    )
    assert build_response.status_code == 200
    indicator = build_response.json().get("hyper_connected_indicator") or {}

    assert indicator.get("hyper_connected_node_count") == 0
    threshold_degree = indicator.get("threshold_degree")
    assert isinstance(threshold_degree, int)
    assert threshold_degree >= 25
    assert indicator.get("top_node_degree", 0) > 0
    assert indicator.get("detected") is False
    assert str(indicator.get("warning") or "").startswith(
        "No hyper-connected non-structural node concentration detected"
    )


def test_graph_communities_are_not_collapsed_by_framework_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(tmp_path / "graph.sqlite"))
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    app = _build_app(tmp_path, graph_enabled=True)
    client = TestClient(app)

    controls = [
        {
            "requirement_id": "NIST-CSF-BK-BK-01",
            "framework": "NIST CSF",
            "framework_version": "2.0",
            "control_family": "BK",
            "requirement_text": "Backups are encrypted and tested regularly.",
            "guidance_text": "Validate restore rehearsal outcomes for backup workloads.",
            "source_uri": "https://example.com/nist-csf",
        },
        {
            "requirement_id": "NIST-CSF-PT-PT-01",
            "framework": "NIST CSF",
            "framework_version": "2.0",
            "control_family": "PT",
            "requirement_text": "Patch cadence is defined for server operating systems.",
            "guidance_text": "Track patch latency and remediation exception approvals.",
            "source_uri": "https://example.com/nist-csf",
        },
    ]

    build_response = client.post(
        "/api/graph/build",
        json={
            "auth_token": "ok",
            "controls": controls,
            "chunks": [],
            "persist_store": True,
            "min_community_size": 2,
        },
    )
    assert build_response.status_code == 200

    export_response = client.get(
        "/api/graph/export",
        params={"auth_token": "ok", "format": "json"},
    )
    assert export_response.status_code == 200
    payload = export_response.json()
    control_nodes = [
        node
        for node in payload.get("nodes", [])
        if str(node.get("node_type") or "") == "CorpusAControl"
    ]
    assert len(control_nodes) == 2

    communities = {
        str(value).strip()
        for node in control_nodes
        for value in ((node.get("attributes") or {}).get("__communities") or [])
        if str(value).strip()
    }
    assert len(communities) != 1


def test_graph_build_min_community_size_default_hides_two_node_components(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(tmp_path / "graph.sqlite"))
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    app = _build_app(tmp_path, graph_enabled=True)
    client = TestClient(app)

    controls = [
        {
            "requirement_id": "NIST-CSF-BK-BK-01",
            "framework": "NIST CSF",
            "framework_version": "2.0",
            "control_family": "BK",
            "requirement_text": "Backups are encrypted and tested regularly.",
            "guidance_text": "Validate restore rehearsal outcomes for backup workloads.",
            "source_uri": "https://example.com/nist-csf",
        },
        {
            "requirement_id": "NIST-CSF-PT-PT-01",
            "framework": "NIST CSF",
            "framework_version": "2.0",
            "control_family": "PT",
            "requirement_text": "Patch cadence is defined for server operating systems.",
            "guidance_text": "Track patch latency and remediation exception approvals.",
            "source_uri": "https://example.com/nist-csf",
        },
    ]

    build_response = client.post(
        "/api/graph/build",
        json={
            "auth_token": "ok",
            "controls": controls,
            "chunks": [],
            "persist_store": True,
        },
    )
    assert build_response.status_code == 200

    export_response = client.get(
        "/api/graph/export",
        params={"auth_token": "ok", "format": "json"},
    )
    assert export_response.status_code == 200
    payload = export_response.json()
    assert payload["community_settings"]["min_community_size"] == 3

    control_nodes = [
        node
        for node in payload.get("nodes", [])
        if str(node.get("node_type") or "") == "CorpusAControl"
    ]
    assert len(control_nodes) == 2
    communities = {
        str(value).strip()
        for node in control_nodes
        for value in ((node.get("attributes") or {}).get("__communities") or [])
        if str(value).strip()
    }
    assert not communities


def test_graph_communities_are_thematic_not_source_filenames(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(tmp_path / "graph.sqlite"))
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    app = _build_app(tmp_path, graph_enabled=True)
    client = TestClient(app)

    chunks = [
        {
            "source_name": "draft.md",
            "source_path": "/tmp/draft.md",
            "normalised_text_sha256": "aaa111",
            "content_sha256": "",
            "original_filename": "draft.md",
            "corpus": "b",
            "corpus_role": "narrative_guidance",
            "content": "Backup recovery procedures require tested restore drills and backup validation.",
            "score": 0.9,
        },
        {
            "source_name": "xlsx.xlsx",
            "source_path": "/tmp/xlsx.xlsx",
            "normalised_text_sha256": "bbb222",
            "content_sha256": "",
            "original_filename": "xlsx.xlsx",
            "corpus": "b",
            "corpus_role": "narrative_guidance",
            "content": "Backup retention and backup encryption should be validated during restore testing.",
            "score": 0.9,
        },
        {
            "source_name": "newco_security_guideline.md",
            "source_path": "/tmp/newco_security_guideline.md",
            "normalised_text_sha256": "ccc333",
            "content_sha256": "",
            "original_filename": "newco_security_guideline.md",
            "corpus": "b",
            "corpus_role": "narrative_guidance",
            "content": "Backup controls should be rehearsed and backup owners should verify recoverability.",
            "score": 0.9,
        },
    ]

    build_response = client.post(
        "/api/graph/build",
        json={
            "auth_token": "ok",
            "controls": [],
            "chunks": chunks,
            "persist_store": True,
        },
    )
    assert build_response.status_code == 200

    export_response = client.get(
        "/api/graph/export",
        params={"auth_token": "ok", "format": "json"},
    )
    assert export_response.status_code == 200
    community_ids = [
        str(key).strip().lower() for key in export_response.json().get("community_summaries", {})
    ]

    assert any("backup" in community_id for community_id in community_ids)
    assert not any(
        "draft" in community_id or "xlsx" in community_id or "newco" in community_id
        for community_id in community_ids
    )


def test_graph_topic_communities_do_not_fallback_to_framework_names(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(tmp_path / "graph.sqlite"))
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    app = _build_app(tmp_path, graph_enabled=True)
    client = TestClient(app)

    controls = [
        {
            "requirement_id": "AESCSF-BK-01",
            "framework": "AESCSF",
            "framework_version": "v2",
            "control_family": "Backup",
            "requirement_text": "Backup retention is tested monthly.",
            "guidance_text": "Backup recovery validation evidence is retained.",
            "source_uri": "https://example.com/aescsf",
        },
        {
            "requirement_id": "AESCSF-BK-02",
            "framework": "AESCSF",
            "framework_version": "v2",
            "control_family": "Backup",
            "requirement_text": "Backup encryption is enforced and restore tests are tracked.",
            "guidance_text": "Backup runbooks include restore steps and owners.",
            "source_uri": "https://example.com/aescsf",
        },
    ]
    chunks = [
        {
            "source_name": "security-guideline.md",
            "source_path": "/tmp/security-guideline.md",
            "normalised_text_sha256": "ddd444",
            "content_sha256": "",
            "original_filename": "security-guideline.md",
            "corpus": "b",
            "corpus_role": "narrative_guidance",
            "content": "Backup restore exercises and backup encryption checks are mandatory.",
            "score": 0.9,
        }
    ]

    build_response = client.post(
        "/api/graph/build",
        json={
            "auth_token": "ok",
            "controls": controls,
            "chunks": chunks,
            "persist_store": True,
            "min_community_size": 2,
        },
    )
    assert build_response.status_code == 200

    export_response = client.get(
        "/api/graph/export",
        params={"auth_token": "ok", "format": "json"},
    )
    assert export_response.status_code == 200
    community_ids = [
        str(key).strip().lower() for key in export_response.json().get("community_summaries", {})
    ]

    assert any(community_id.startswith("topic ") for community_id in community_ids)
    assert any("backup" in community_id for community_id in community_ids)


def test_graph_topic_tokens_prioritise_semantic_text_over_id_labels(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(tmp_path / "graph.sqlite"))
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    app = _build_app(tmp_path, graph_enabled=True)
    client = TestClient(app)

    controls = [
        {
            "requirement_id": "AESCSF-ACCESS-1A-UNIQUE-SUFFIX-ALPHA",
            "framework": "AESCSF",
            "framework_version": "v2",
            "control_family": "Access Management",
            "requirement_text": "Backup validation is required for privileged account recovery.",
            "guidance_text": "Backup recovery exercises are logged and verified.",
            "source_uri": "https://example.com/aescsf",
        },
        {
            "requirement_id": "ISM-1543-UNIQUE-SUFFIX-BETA",
            "framework": "ISM",
            "framework_version": "2025",
            "control_family": "Identity and Access",
            "requirement_text": "Backup encryption and backup restore tests are mandatory.",
            "guidance_text": "Backup rehearsal evidence is retained for audit.",
            "source_uri": "https://example.com/ism",
        },
        {
            "requirement_id": "PSPF-GOV-12-UNIQUE-SUFFIX-GAMMA",
            "framework": "PSPF",
            "framework_version": "2025",
            "control_family": "Governance",
            "requirement_text": "Backup retention and recovery playbooks are documented.",
            "guidance_text": "Backup recovery outcomes are tracked to closure.",
            "source_uri": "https://example.com/pspf",
        },
    ]

    build_response = client.post(
        "/api/graph/build",
        json={
            "auth_token": "ok",
            "controls": controls,
            "chunks": [],
            "persist_store": True,
            "min_community_size": 3,
        },
    )
    assert build_response.status_code == 200

    export_response = client.get(
        "/api/graph/export",
        params={"auth_token": "ok", "format": "json"},
    )
    assert export_response.status_code == 200
    community_ids = [
        str(key).strip().lower() for key in export_response.json().get("community_summaries", {})
    ]
    assert any(community_id.startswith("topic backup") for community_id in community_ids)


def test_graph_topic_communities_suppress_noisy_generic_tokens(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(tmp_path / "graph.sqlite"))
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    app = _build_app(tmp_path, graph_enabled=True)
    client = TestClient(app)

    chunks = [
        {
            "source_name": "policy-a.md",
            "source_path": "/tmp/policy-a.md",
            "normalised_text_sha256": "e1",
            "content_sha256": "",
            "original_filename": "policy-a.md",
            "corpus": "b",
            "corpus_role": "narrative_guidance",
            "content": "After review each office should verify the backup restore process.",
            "score": 0.9,
        },
        {
            "source_name": "policy-b.md",
            "source_path": "/tmp/policy-b.md",
            "normalised_text_sha256": "e2",
            "content_sha256": "",
            "original_filename": "policy-b.md",
            "corpus": "b",
            "corpus_role": "narrative_guidance",
            "content": "After review each office should verify backup encryption controls.",
            "score": 0.9,
        },
        {
            "source_name": "policy-c.md",
            "source_path": "/tmp/policy-c.md",
            "normalised_text_sha256": "e3",
            "content_sha256": "",
            "original_filename": "policy-c.md",
            "corpus": "b",
            "corpus_role": "narrative_guidance",
            "content": "After review each office should verify backup retention and recovery evidence.",
            "score": 0.9,
        },
    ]

    build_response = client.post(
        "/api/graph/build",
        json={
            "auth_token": "ok",
            "controls": [],
            "chunks": chunks,
            "persist_store": True,
            "min_community_size": 3,
        },
    )
    assert build_response.status_code == 200

    export_response = client.get(
        "/api/graph/export",
        params={"auth_token": "ok", "format": "json"},
    )
    assert export_response.status_code == 200
    community_ids = [
        str(key).strip().lower() for key in export_response.json().get("community_summaries", {})
    ]

    assert any("topic backup" in community_id for community_id in community_ids)
    assert not any(
        community_id.startswith("topic after")
        or community_id.startswith("topic each")
        or community_id.startswith("topic office")
        or community_id.startswith("topic verify")
        or community_id.startswith("topic been")
        or community_id.startswith("topic impact")
        or community_id.startswith("topic level")
        or community_id.startswith("topic need")
        for community_id in community_ids
    )


def test_graph_topic_communities_collapse_spelling_and_plural_variants(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(tmp_path / "graph.sqlite"))
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    app = _build_app(tmp_path, graph_enabled=True)
    client = TestClient(app)

    chunks = [
        {
            "source_name": "auth-a.md",
            "source_path": "/tmp/auth-a.md",
            "normalised_text_sha256": "a1",
            "content_sha256": "",
            "original_filename": "auth-a.md",
            "corpus": "b",
            "corpus_role": "narrative_guidance",
            "content": "Authorisation controls require authorised access and credential hygiene.",
            "score": 0.9,
        },
        {
            "source_name": "auth-b.md",
            "source_path": "/tmp/auth-b.md",
            "normalised_text_sha256": "a2",
            "content_sha256": "",
            "original_filename": "auth-b.md",
            "corpus": "b",
            "corpus_role": "narrative_guidance",
            "content": "Authorization checks enforce authorized identity use and credentials lifecycle.",
            "score": 0.9,
        },
        {
            "source_name": "auth-c.md",
            "source_path": "/tmp/auth-c.md",
            "normalised_text_sha256": "a3",
            "content_sha256": "",
            "original_filename": "auth-c.md",
            "corpus": "b",
            "corpus_role": "narrative_guidance",
            "content": "Authorised sessions must use strong credential management and authorization boundaries.",
            "score": 0.9,
        },
    ]

    build_response = client.post(
        "/api/graph/build",
        json={
            "auth_token": "ok",
            "controls": [],
            "chunks": chunks,
            "persist_store": True,
            "min_community_size": 3,
        },
    )
    assert build_response.status_code == 200

    export_response = client.get(
        "/api/graph/export",
        params={"auth_token": "ok", "format": "json"},
    )
    assert export_response.status_code == 200
    payload = export_response.json()
    community_ids = [str(key).strip().lower() for key in payload.get("community_summaries", {})]

    assert "topic authorization" in community_ids
    assert "topic credential" in community_ids
    assert "topic authorisation" not in community_ids
    assert "topic authorised" not in community_ids
    assert "topic authorized" not in community_ids
    assert "topic credentials" not in community_ids

    membership_values: list[str] = []
    for node in payload.get("nodes", []):
        attrs = node.get("attributes") if isinstance(node.get("attributes"), dict) else {}
        if not isinstance(attrs, dict):
            continue
        raw_memberships = attrs.get("__communities")
        if isinstance(raw_memberships, list):
            membership_values.extend(
                str(v).strip().lower() for v in raw_memberships if str(v).strip()
            )

    assert "topic authorization" in membership_values
    assert "topic credential" in membership_values


def test_graph_smoke_sample_payload_matches_build_contract(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    sample_path = (
        Path(__file__).resolve().parents[2]
        / "ops"
        / "scripts"
        / "azure"
        / "graph-smoke-payload.sample.json"
    )
    payload = json.loads(sample_path.read_text(encoding="utf-8"))
    payload["auth_token"] = "ok"

    request_model = GraphBuildRequest.model_validate(payload)
    assert request_model.persist_store is False
    assert len(request_model.controls) == 1
    assert len(request_model.chunks) == 1

    app = _build_app(tmp_path, graph_enabled=True)
    client = TestClient(app)
    response = client.post("/api/graph/build", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_graph_build_rejects_client_supplied_output_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    app = _build_app(tmp_path, graph_enabled=True)
    client = TestClient(app)

    response = client.post(
        "/api/graph/build",
        json={
            "auth_token": "ok",
            "controls": [],
            "chunks": [],
            "output_dir": "../../outside-artifacts",
            "persist_store": False,
        },
    )

    assert response.status_code == 400
    assert "GRAPH_ARTIFACTS_DIR" in response.json()["detail"]


def test_aws_graph_smoke_sample_payload_matches_build_contract(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    sample_path = (
        Path(__file__).resolve().parents[2]
        / "ops"
        / "scripts"
        / "aws"
        / "graph-smoke-payload.sample.json"
    )
    payload = json.loads(sample_path.read_text(encoding="utf-8"))
    payload["auth_token"] = "ok"

    request_model = GraphBuildRequest.model_validate(payload)
    assert request_model.persist_store is False
    assert len(request_model.controls) == 1
    assert len(request_model.chunks) == 1

    app = _build_app(tmp_path, graph_enabled=True)
    client = TestClient(app)
    response = client.post("/api/graph/build", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_graph_endpoints_return_401_when_unauthorised(tmp_path) -> None:
    app = _build_app(tmp_path, graph_enabled=True)
    client = TestClient(app)
    response = client.get("/api/graph/export", params={"auth_token": "bad", "format": "json"})
    assert response.status_code == 401


def test_graph_endpoints_return_503_when_backend_unavailable_even_if_flag_disabled(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(tmp_path / "graph.sqlite"))
    app = _build_app(tmp_path, graph_enabled=False, graph_backend="azure")
    client = TestClient(app)
    response = client.get("/api/graph/export", params={"auth_token": "ok", "format": "json"})
    assert response.status_code == 503


def test_graph_endpoints_return_503_when_backend_unavailable(tmp_path) -> None:
    app = _build_app(tmp_path, graph_enabled=True, graph_backend="azure")
    client = TestClient(app)
    response = client.get("/api/graph/export", params={"auth_token": "ok", "format": "json"})
    assert response.status_code == 503
    body = response.json()
    assert body["backend"] == "azure"


def test_graph_endpoints_azure_emulation_parity_with_local(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(tmp_path / "graph_local.sqlite"))
    local_app = _build_app(tmp_path, graph_enabled=True, graph_backend="local")
    local_client = TestClient(local_app)

    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(tmp_path / "graph_azure_emu.sqlite"))
    azure_app = _build_app(
        tmp_path,
        graph_enabled=True,
        graph_backend="azure",
        graph_azure_emulation_enabled=True,
    )
    azure_client = TestClient(azure_app)

    request_payload = {
        "auth_token": "ok",
        "controls": _controls(),
        "chunks": _chunks(),
        "persist_store": True,
    }

    local_build = local_client.post("/api/graph/build", json=request_payload)
    azure_build = azure_client.post("/api/graph/build", json=request_payload)
    assert local_build.status_code == 200
    assert azure_build.status_code == 200

    local_node = local_client.get(
        "/api/graph/nodes/a:nist-csf-gv-gv-oc-01",
        params={"auth_token": "ok"},
    )
    azure_node = azure_client.get(
        "/api/graph/nodes/a:nist-csf-gv-gv-oc-01",
        params={"auth_token": "ok"},
    )
    assert local_node.status_code == 200
    assert azure_node.status_code == 200
    assert local_node.json()["node_id"] == azure_node.json()["node_id"]
    assert local_node.json()["node_type"] == azure_node.json()["node_type"]

    local_related = local_client.get(
        "/api/graph/related",
        params={"auth_token": "ok", "node_id": "a:nist-csf-gv-gv-oc-01", "depth": 2},
    )
    azure_related = azure_client.get(
        "/api/graph/related",
        params={"auth_token": "ok", "node_id": "a:nist-csf-gv-gv-oc-01", "depth": 2},
    )
    assert local_related.status_code == 200
    assert azure_related.status_code == 200
    assert len(local_related.json().get("nodes", [])) == len(azure_related.json().get("nodes", []))
    assert len(local_related.json().get("edges", [])) == len(azure_related.json().get("edges", []))

    local_export = local_client.get(
        "/api/graph/export", params={"auth_token": "ok", "format": "json"}
    )
    azure_export = azure_client.get(
        "/api/graph/export", params={"auth_token": "ok", "format": "json"}
    )
    assert local_export.status_code == 200
    assert azure_export.status_code == 200
    assert local_export.json().get("counts") == azure_export.json().get("counts")
    assert len(local_export.json().get("nodes", [])) == len(azure_export.json().get("nodes", []))
    assert len(local_export.json().get("edges", [])) == len(azure_export.json().get("edges", []))


def test_graph_endpoints_azure_artifact_snapshot_mode_reads_local_artifacts(
    monkeypatch,
    tmp_path,
) -> None:
    artifacts_dir = tmp_path / "artifacts"
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(artifacts_dir))

    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(tmp_path / "graph_local.sqlite"))
    local_app = _build_app(tmp_path, graph_enabled=True, graph_backend="local")
    local_client = TestClient(local_app)

    build_response = local_client.post(
        "/api/graph/build",
        json={
            "auth_token": "ok",
            "controls": _controls(),
            "chunks": _chunks(),
            "persist_store": True,
        },
    )
    assert build_response.status_code == 200

    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(tmp_path / "graph_azure_snapshot.sqlite"))
    azure_app = _build_app(
        tmp_path,
        graph_enabled=True,
        graph_backend="azure",
        graph_azure_artifacts_dir=str(artifacts_dir),
    )
    azure_client = TestClient(azure_app)

    node_response = azure_client.get(
        "/api/graph/nodes/a:nist-csf-gv-gv-oc-01",
        params={"auth_token": "ok"},
    )
    assert node_response.status_code == 200
    assert node_response.json()["node_id"] == "a:nist-csf-gv-gv-oc-01"

    related_response = azure_client.get(
        "/api/graph/related",
        params={"auth_token": "ok", "node_id": "a:nist-csf-gv-gv-oc-01", "depth": 2},
    )
    assert related_response.status_code == 200
    assert related_response.json()["nodes"]
    assert related_response.json()["audit"]["operation"] == "related"

    export_response = azure_client.get(
        "/api/graph/export",
        params={"auth_token": "ok", "format": "json"},
    )
    assert export_response.status_code == 200
    export_payload = export_response.json()
    assert export_payload["counts"]["nodes"] > 0
    assert export_payload["counts"]["edges"] > 0
    assert export_payload["audit"]["operation"] == "export"


def test_graph_endpoints_azure_object_storage_snapshot_mode_reads_graph(tmp_path) -> None:
    node = {
        "node_id": "a:nist-csf-gv-gv-oc-01",
        "node_type": "CorpusAControl",
        "label": "NIST-CSF-GV-GV-OC-01",
        "graph_schema_version": "v1",
        "attributes": {},
    }
    edge = {
        "edge_id": "e:1",
        "from_id": "a:nist-csf-gv-gv-oc-01",
        "to_id": "b:doc#chunk",
        "edge_type": "GUIDANCE_SUPPORTS_CONTROL",
        "confidence": 0.8,
        "evidence_key": "ev:1",
        "graph_schema_version": "v1",
        "attributes": {},
    }
    storage_client = _DummyObjectStorageClient(
        objects={
            "graph-artifacts/env/dev/graph/nodes.jsonl": (json.dumps(node) + "\n").encode("utf-8"),
            "graph-artifacts/env/dev/graph/edges.jsonl": (json.dumps(edge) + "\n").encode("utf-8"),
        },
        metadata={
            "graph-artifacts/env/dev/graph/nodes.jsonl": {
                "content_length": len(json.dumps(node) + "\n"),
                "last_modified": "2026-07-06T12:00:00+00:00",
            },
            "graph-artifacts/env/dev/graph/edges.jsonl": {
                "content_length": len(json.dumps(edge) + "\n"),
                "last_modified": "2026-07-06T12:00:01+00:00",
            },
        },
    )

    app = _build_app(
        tmp_path,
        graph_enabled=True,
        graph_backend="azure",
        graph_azure_artifacts_container="graph-artifacts",
        graph_azure_artifacts_prefix="env/dev/graph",
        graph_azure_storage_client=storage_client,
    )
    client = TestClient(app)

    node_response = client.get(
        "/api/graph/nodes/a:nist-csf-gv-gv-oc-01",
        params={"auth_token": "ok"},
    )
    assert node_response.status_code == 200
    assert node_response.json()["node_id"] == "a:nist-csf-gv-gv-oc-01"

    export_response = client.get(
        "/api/graph/export",
        params={"auth_token": "ok", "format": "json"},
    )
    assert export_response.status_code == 200
    export_payload = export_response.json()
    assert export_payload["counts"] == {"nodes": 1, "edges": 1}


def test_graph_build_publishes_artifacts_to_azure_object_storage(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    storage_client = _DummyObjectStorageClient(objects={}, metadata={})

    app = _build_app(
        tmp_path,
        graph_enabled=True,
        graph_backend="azure",
        graph_azure_artifacts_container="graph-artifacts",
        graph_azure_artifacts_prefix="env/dev/graph",
        graph_azure_publish_enabled=True,
        graph_azure_storage_client=storage_client,
    )
    client = TestClient(app)

    build_response = client.post(
        "/api/graph/build",
        json={
            "auth_token": "ok",
            "controls": _controls(),
            "chunks": _chunks(),
            "persist_store": False,
        },
    )
    assert build_response.status_code == 200
    payload = build_response.json()
    published_snapshot = payload["report"].get("published_snapshot")
    assert published_snapshot is not None
    assert published_snapshot["bucket_or_container"] == "graph-artifacts"
    assert published_snapshot["backend"] == "azure"
    assert published_snapshot["prefix"] == "env/dev/graph"
    assert len(storage_client.put_calls) == 3
    published_keys = {call[1] for call in storage_client.put_calls}
    assert published_keys == {
        "env/dev/graph/nodes.jsonl",
        "env/dev/graph/edges.jsonl",
        "env/dev/graph/graph_build_report.json",
    }


def test_graph_endpoints_aws_object_storage_snapshot_mode_reads_graph(tmp_path) -> None:
    node = {
        "node_id": "a:nist-csf-gv-gv-oc-01",
        "node_type": "CorpusAControl",
        "label": "NIST-CSF-GV-GV-OC-01",
        "graph_schema_version": "v1",
        "attributes": {},
    }
    edge = {
        "edge_id": "e:1",
        "from_id": "a:nist-csf-gv-gv-oc-01",
        "to_id": "b:doc#chunk",
        "edge_type": "GUIDANCE_SUPPORTS_CONTROL",
        "confidence": 0.8,
        "evidence_key": "ev:1",
        "graph_schema_version": "v1",
        "attributes": {},
    }
    storage_client = _DummyObjectStorageClient(
        objects={
            "graph-artifacts-aws/env/dev/graph/nodes.jsonl": (json.dumps(node) + "\n").encode(
                "utf-8"
            ),
            "graph-artifacts-aws/env/dev/graph/edges.jsonl": (json.dumps(edge) + "\n").encode(
                "utf-8"
            ),
        },
        metadata={
            "graph-artifacts-aws/env/dev/graph/nodes.jsonl": {
                "content_length": len(json.dumps(node) + "\n"),
                "last_modified": "2026-07-06T12:00:00+00:00",
            },
            "graph-artifacts-aws/env/dev/graph/edges.jsonl": {
                "content_length": len(json.dumps(edge) + "\n"),
                "last_modified": "2026-07-06T12:00:01+00:00",
            },
        },
    )

    app = _build_app(
        tmp_path,
        graph_enabled=True,
        graph_backend="aws",
        graph_aws_artifacts_bucket="graph-artifacts-aws",
        graph_aws_artifacts_prefix="env/dev/graph",
        graph_aws_storage_client=storage_client,
    )
    client = TestClient(app)

    node_response = client.get(
        "/api/graph/nodes/a:nist-csf-gv-gv-oc-01",
        params={"auth_token": "ok"},
    )
    assert node_response.status_code == 200
    assert node_response.json()["node_id"] == "a:nist-csf-gv-gv-oc-01"

    export_response = client.get(
        "/api/graph/export",
        params={"auth_token": "ok", "format": "json"},
    )
    assert export_response.status_code == 200
    export_payload = export_response.json()
    assert export_payload["counts"] == {"nodes": 1, "edges": 1}


def test_graph_build_publishes_artifacts_to_aws_object_storage(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GRAPH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    storage_client = _DummyObjectStorageClient(objects={}, metadata={})

    app = _build_app(
        tmp_path,
        graph_enabled=True,
        graph_backend="aws",
        graph_aws_artifacts_bucket="graph-artifacts-aws",
        graph_aws_artifacts_prefix="env/dev/graph",
        graph_aws_publish_enabled=True,
        graph_aws_storage_client=storage_client,
    )
    client = TestClient(app)

    build_response = client.post(
        "/api/graph/build",
        json={
            "auth_token": "ok",
            "controls": _controls(),
            "chunks": _chunks(),
            "persist_store": False,
        },
    )
    assert build_response.status_code == 200
    payload = build_response.json()
    published_snapshot = payload["report"].get("published_snapshot")
    assert published_snapshot is not None
    assert published_snapshot["bucket_or_container"] == "graph-artifacts-aws"
    assert published_snapshot["backend"] == "aws"
    assert len(storage_client.put_calls) == 3


def test_graph_status_reports_controls_loaded_and_build_required(monkeypatch, tmp_path) -> None:
    controls_client = _DummyControlsSearchClient(count=3)
    config = _config(enabled=True, backend="local")
    app = FastAPI()
    register_graph_endpoints(
        app,
        deps={
            "config": lambda: config,
            "_is_authorised_request": lambda: (lambda auth_token, request: auth_token == "ok"),
            "_unauthorised_message": lambda: (lambda request: "Unauthorised."),
            "_count_search_documents_total_by_filter": lambda: (
                lambda client, filter_expr: controls_client._count
            ),
            "controls_search_client": lambda: controls_client,
        },
    )
    client = TestClient(app)

    response = client.get("/api/graph/status", params={"auth_token": "ok"})

    assert response.status_code == 200
    body = response.json()
    assert body["controls_loaded"] is True
    assert body["allow_build"] is True
    assert body["graph_ready"] in {True, False}


def test_graph_status_allows_build_when_only_corpus_a_is_loaded() -> None:
    controls_client = _DummyControlsSearchClient(count=3)
    corpus_b_client = _DummyControlsSearchClient(count=0)
    config = _config(enabled=False, backend="azure")
    app = FastAPI()

    def _count_total(client: object, filter_expr: str) -> int:
        if client is controls_client:
            return controls_client._count
        if client is corpus_b_client and filter_expr == "corpus eq 'b'":
            return corpus_b_client._count
        return 0

    register_graph_endpoints(
        app,
        deps={
            "config": lambda: config,
            "_is_authorised_request": lambda: (lambda auth_token, request: auth_token == "ok"),
            "_unauthorised_message": lambda: (lambda request: "Unauthorised."),
            "_count_search_documents_total_by_filter": lambda: _count_total,
            "controls_search_client": lambda: controls_client,
            "search_client": lambda: corpus_b_client,
        },
    )
    client = TestClient(app)

    response = client.get("/api/graph/status", params={"auth_token": "ok"})

    assert response.status_code == 200
    body = response.json()
    assert body["controls_loaded"] is True
    assert body["corpus_b_loaded"] is False
    assert body["index_loaded"] is True
    assert body["allow_build"] is True


def test_graph_status_allows_build_when_only_corpus_b_is_loaded() -> None:
    controls_client = _DummyControlsSearchClient(count=0)
    corpus_b_client = _DummyControlsSearchClient(count=2)
    config = _config(enabled=False, backend="aws")
    app = FastAPI()

    def _count_total(client: object, filter_expr: str) -> int:
        if client is controls_client:
            return controls_client._count
        if client is corpus_b_client and filter_expr == "corpus eq 'b'":
            return corpus_b_client._count
        return 0

    register_graph_endpoints(
        app,
        deps={
            "config": lambda: config,
            "_is_authorised_request": lambda: (lambda auth_token, request: auth_token == "ok"),
            "_unauthorised_message": lambda: (lambda request: "Unauthorised."),
            "_count_search_documents_total_by_filter": lambda: _count_total,
            "controls_search_client": lambda: controls_client,
            "search_client": lambda: corpus_b_client,
        },
    )
    client = TestClient(app)

    response = client.get("/api/graph/status", params={"auth_token": "ok"})

    assert response.status_code == 200
    body = response.json()
    assert body["controls_loaded"] is False
    assert body["corpus_b_loaded"] is True
    assert body["index_loaded"] is True
    assert body["allow_build"] is True


def test_graph_status_uses_presence_probe_when_total_counts_are_zero() -> None:
    controls_client = _DummyPresenceSearchClient(
        items=[
            {
                "id": "ctrl-1",
                "framework": "NIST CSF",
            }
        ]
    )
    corpus_b_client = _DummyPresenceSearchClient(items=[])
    config = _config(enabled=False, backend="local")
    app = FastAPI()

    register_graph_endpoints(
        app,
        deps={
            "config": lambda: config,
            "_is_authorised_request": lambda: (lambda auth_token, request: auth_token == "ok"),
            "_unauthorised_message": lambda: (lambda request: "Unauthorised."),
            # Simulate unsupported total-count response path.
            "_count_search_documents_total_by_filter": lambda: (lambda client, filter_expr: 0),
            "controls_search_client": lambda: controls_client,
            "search_client": lambda: corpus_b_client,
        },
    )
    client = TestClient(app)

    response = client.get("/api/graph/status", params={"auth_token": "ok"})

    assert response.status_code == 200
    body = response.json()
    assert body["controls_loaded"] is True
    assert body["controls_count"] >= 1
    assert body["index_loaded"] is True
    assert body["allow_build"] is True
