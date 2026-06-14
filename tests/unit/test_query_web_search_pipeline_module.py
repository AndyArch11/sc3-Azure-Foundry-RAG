from __future__ import annotations

import io
import json
from types import SimpleNamespace

from query_web.pipeline.search import _client_search, _embed_query, _hybrid_search


class _CaptureClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def search(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return [{"ok": True}]


def test_client_search_azure_mapping_includes_total_count() -> None:
    client = _CaptureClient()

    result = _client_search(
        client,
        query_text="security",
        filter_expr="framework eq 'NIST CSF'",
        top=5,
        select=["id", "title"],
        include_total_count=True,
        cloud_provider="azure",
    )

    assert result == [{"ok": True}]
    assert client.calls == [
        {
            "search_text": "security",
            "filter": "framework eq 'NIST CSF'",
            "top": 5,
            "include_total_count": True,
            "select": ["id", "title"],
        }
    ]


def test_client_search_aws_mapping_uses_query_text_and_filters() -> None:
    client = _CaptureClient()

    _client_search(
        client,
        query_text="mfa",
        filter_expr="corpus eq 'a'",
        top=3,
        select=["id"],
        include_total_count=True,
        cloud_provider="aws",
    )

    assert client.calls == [
        {
            "query_text": "mfa",
            "filters": "corpus eq 'a'",
            "top": 3,
            "select": ["id"],
        }
    ]


def test_client_search_unknown_provider_falls_back_to_azure_mapping() -> None:
    client = _CaptureClient()

    _client_search(
        client,
        query_text="*",
        filter_expr="",
        top=1,
        include_total_count=False,
        cloud_provider="gcp",
    )

    assert client.calls == [{"search_text": "*", "filter": "", "top": 1}]


def _build_hybrid_svc(provider: str):
    svc = SimpleNamespace()
    svc.config = SimpleNamespace(cloud_provider=provider)
    svc.logger = SimpleNamespace(warning=lambda *a, **k: None)
    svc._embed_calls = 0

    def _embed_query(question: str):
        del question
        svc._embed_calls += 1
        return [0.1, 0.2]

    svc._embed_query = _embed_query

    class _SearchClient:
        def search(self, **kwargs):  # type: ignore[no-untyped-def]
            return [
                {
                    "content": "chunk",
                    "source_name": "doc",
                    "source_path": "p",
                    "corpus": "b",
                    "corpus_role": "narrative_guidance",
                    "@search.score": 1.0,
                }
            ]

    svc.search_client = _SearchClient()
    return svc


def test_hybrid_search_embeds_for_azure_provider() -> None:
    svc = _build_hybrid_svc("azure")

    items, timings = _hybrid_search("q", 3, "corpus eq 'b'", svc=svc)

    assert len(items) == 1
    assert svc._embed_calls == 1
    assert timings["embedding_s"] >= 0.0


def test_hybrid_search_embeds_for_aws_provider() -> None:
    svc = _build_hybrid_svc("aws")

    items, timings = _hybrid_search("q", 3, "corpus eq 'b'", svc=svc)

    assert len(items) == 1
    assert svc._embed_calls == 1
    assert timings["embedding_s"] >= 0.0


def test_hybrid_search_skips_embedding_for_local_provider() -> None:
    svc = _build_hybrid_svc("local")

    items, timings = _hybrid_search("q", 3, "corpus eq 'b'", svc=svc)

    assert len(items) == 1
    assert svc._embed_calls == 0
    assert timings["embedding_s"] == 0.0


def test_hybrid_search_unknown_provider_falls_back_to_azure_embedding() -> None:
    svc = _build_hybrid_svc("gcp")

    items, timings = _hybrid_search("q", 3, "corpus eq 'b'", svc=svc)

    assert len(items) == 1
    assert svc._embed_calls == 1
    assert timings["embedding_s"] >= 0.0


def test_embed_query_aws_uses_bedrock_runtime(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    svc = SimpleNamespace(
        config=SimpleNamespace(
            cloud_provider="aws", embedding_deployment="amazon.titan-embed-text-v2:0"
        ),
        logger=SimpleNamespace(warning=lambda *a, **k: None),
    )

    class _Client:
        def invoke_model(self, **kwargs):  # type: ignore[no-untyped-def]
            assert kwargs["modelId"] == "amazon.titan-embed-text-v2:0"
            payload = json.loads(kwargs["body"])
            assert payload["inputText"] == "what is mfa"
            return {"body": io.BytesIO(b'{"embedding": [0.11, 0.22]}')}

    class _Session:
        def __init__(self, region_name=None):  # type: ignore[no-untyped-def]
            assert region_name == "ap-southeast-2"

        def client(self, name: str):
            assert name == "bedrock-runtime"
            return _Client()

    monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
    monkeypatch.setitem(__import__("sys").modules, "boto3", SimpleNamespace(Session=_Session))

    vector = _embed_query("what is mfa", svc=svc)
    assert vector == [0.11, 0.22]
