"""Unit tests for runtime/search/local_qdrant.py.

All external I/O (qdrant_client, requests) is fully mocked so these tests
run without Qdrant or Ollama installed.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

# ---------------------------------------------------------------------------
# Helpers — build a LocalQdrantSearchClient with all I/O mocked
# ---------------------------------------------------------------------------


def _make_client(docs: list[dict] | None = None, index: str = "test-index"):
    """Return a LocalQdrantSearchClient with QdrantClient stubbed out."""
    mock_qdrant = MagicMock()

    with patch("runtime.search.local_qdrant.LocalQdrantSearchClient.__init__") as _init:
        _init.return_value = None
        from runtime.search.local_qdrant import LocalQdrantSearchClient

        client = LocalQdrantSearchClient.__new__(LocalQdrantSearchClient)
        client._index = index
        client._qdrant_url = "http://localhost:6333"
        client._ollama_base_url = "http://localhost:11434"
        client._embedding_model = "nomic-embed-text"
        client._docs = list(docs or [])
        client._client = mock_qdrant

    return client, mock_qdrant


# ---------------------------------------------------------------------------
# _SearchResults
# ---------------------------------------------------------------------------


def test_search_results_get_count_returns_total():
    from runtime.search.local_qdrant import _SearchResults

    r = _SearchResults([{"a": 1}, {"b": 2}], total_count=42)
    assert r.get_count() == 42


def test_search_results_get_count_none_when_not_set():
    from runtime.search.local_qdrant import _SearchResults

    r = _SearchResults([])
    assert r.get_count() is None


def test_search_results_behaves_as_list():
    from runtime.search.local_qdrant import _SearchResults

    items = [{"x": 1}, {"x": 2}]
    r = _SearchResults(items)
    assert len(r) == 2
    assert r[0] == {"x": 1}


# ---------------------------------------------------------------------------
# index_name property
# ---------------------------------------------------------------------------


def test_index_name_returns_index():
    client, _ = _make_client(index="my-index")
    assert client.index_name == "my-index"


# ---------------------------------------------------------------------------
# _text_for_embedding
# ---------------------------------------------------------------------------


def test_text_for_embedding_uses_content():
    client, _ = _make_client()
    assert client._text_for_embedding({"content": "hello"}) == "hello"


def test_text_for_embedding_falls_back_to_requirement_text():
    client, _ = _make_client()
    assert client._text_for_embedding({"requirement_text": "req"}) == "req"


def test_text_for_embedding_falls_back_to_guidance_text():
    client, _ = _make_client()
    assert client._text_for_embedding({"guidance_text": "guide"}) == "guide"


def test_text_for_embedding_returns_empty_string_for_empty_doc():
    client, _ = _make_client()
    assert client._text_for_embedding({}) == ""


# ---------------------------------------------------------------------------
# _point_id
# ---------------------------------------------------------------------------


def test_point_id_is_deterministic():
    client, _ = _make_client()
    doc = {"id": "doc-1"}
    assert client._point_id(doc, 0) == client._point_id(doc, 0)


def test_point_id_uses_id_field():
    client, _ = _make_client()
    doc = {"id": "abc"}
    expected = int(hashlib.sha256(b"abc").hexdigest()[:15], 16)
    assert client._point_id(doc, 0) == expected


def test_point_id_uses_chunk_id_when_no_id():
    client, _ = _make_client()
    doc = {"chunk_id": "cid-99"}
    expected = int(hashlib.sha256(b"cid-99").hexdigest()[:15], 16)
    assert client._point_id(doc, 0) == expected


def test_point_id_falls_back_to_index_ordinal():
    client, _ = _make_client(index="idx")
    doc = {}
    expected = int(hashlib.sha256(b"idx:7").hexdigest()[:15], 16)
    assert client._point_id(doc, 7) == expected


# ---------------------------------------------------------------------------
# _embed_text
# ---------------------------------------------------------------------------


def test_embed_text_parses_embedding_key():
    client, _ = _make_client()
    mock_response = MagicMock()
    mock_response.json.return_value = {"embedding": [0.1, 0.2, 0.3]}

    with patch("runtime.search.local_qdrant.requests.post", return_value=mock_response):
        result = client._embed_text("hello")

    assert result == [0.1, 0.2, 0.3]


def test_embed_text_parses_embeddings_nested_key():
    client, _ = _make_client()
    mock_response = MagicMock()
    mock_response.json.return_value = {"embeddings": [[0.4, 0.5]]}

    with patch("runtime.search.local_qdrant.requests.post", return_value=mock_response):
        result = client._embed_text("hello")

    assert result == [0.4, 0.5]


def test_embed_text_raises_on_missing_vectors():
    client, _ = _make_client()
    mock_response = MagicMock()
    mock_response.json.return_value = {"unexpected": "payload"}

    with patch("runtime.search.local_qdrant.requests.post", return_value=mock_response):
        with pytest.raises(RuntimeError, match="embedding response"):
            client._embed_text("hello")


def test_embed_text_retries_with_shorter_prompt_on_context_length_error():
    client, _ = _make_client()

    first = MagicMock()
    first.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
    first.text = "llm embedding error: the input length exceeds the context length"

    second = MagicMock()
    second.raise_for_status.return_value = None
    second.json.return_value = {"embedding": [0.9, 0.8]}

    long_text = "x" * 12000
    with patch(
        "runtime.search.local_qdrant.requests.post",
        side_effect=[first, second],
    ) as post_mock:
        result = client._embed_text(long_text)

    assert result == [0.9, 0.8]
    assert post_mock.call_count == 2

    first_payload = post_mock.call_args_list[0].kwargs["json"]
    second_payload = post_mock.call_args_list[1].kwargs["json"]
    assert len(first_payload["prompt"]) <= 6000
    assert len(second_payload["prompt"]) < len(first_payload["prompt"])


# ---------------------------------------------------------------------------
# _build_filter
# ---------------------------------------------------------------------------


def test_build_filter_none_for_empty_string():
    client, _ = _make_client()
    assert client._build_filter("") is None


def test_build_filter_none_for_none():
    client, _ = _make_client()
    assert client._build_filter(None) is None


def test_build_filter_none_for_unsupported_syntax():
    client, _ = _make_client()
    # Only simple "field eq 'value'" is supported
    assert client._build_filter("framework ne 'ISM'") is None


def test_build_filter_returns_filter_for_eq_expression():
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    client, _ = _make_client()
    result = client._build_filter("framework eq 'ISM'")
    assert result is not None
    # Structural check: it's a Filter with one must FieldCondition
    assert isinstance(result, Filter)
    assert len(result.must) == 1
    cond = result.must[0]
    assert isinstance(cond, FieldCondition)
    assert cond.key == "framework"
    assert cond.match.value == "ISM"


# ---------------------------------------------------------------------------
# _fallback_text_search
# ---------------------------------------------------------------------------


def test_fallback_text_search_matches_substring():
    docs = [
        {"content": "essential eight maturity model"},
        {"content": "unrelated document"},
    ]
    client, _ = _make_client(docs=docs)

    results = client._fallback_text_search(
        query_text="essential", top=10, filters=None, select=None, include_total_count=False
    )
    assert len(results) == 1
    assert results[0]["content"] == "essential eight maturity model"


def test_fallback_text_search_respects_top():
    docs = [{"content": f"match {i}"} for i in range(10)]
    client, _ = _make_client(docs=docs)

    results = client._fallback_text_search(
        query_text="match", top=3, filters=None, select=None, include_total_count=False
    )
    assert len(results) == 3


def test_fallback_text_search_applies_odata_filter():
    docs = [
        {"content": "text", "framework": "ISM"},
        {"content": "text", "framework": "NIST"},
    ]
    client, _ = _make_client(docs=docs)

    results = client._fallback_text_search(
        query_text="text",
        top=10,
        filters="framework eq 'ISM'",
        select=None,
        include_total_count=False,
    )
    assert len(results) == 1
    assert results[0]["framework"] == "ISM"


def test_fallback_text_search_applies_select():
    docs = [{"content": "text", "corpus": "a", "extra": "data"}]
    client, _ = _make_client(docs=docs)

    results = client._fallback_text_search(
        query_text="text",
        top=10,
        filters=None,
        select=["content", "corpus"],
        include_total_count=False,
    )
    assert set(results[0].keys()) == {"content", "corpus"}


def test_fallback_returns_total_count_when_requested():
    docs = [{"content": f"item {i}"} for i in range(5)]
    client, _ = _make_client(docs=docs)

    results = client._fallback_text_search(
        query_text="item", top=2, filters=None, select=None, include_total_count=True
    )
    assert results.get_count() == 5


def test_fallback_returns_none_count_when_not_requested():
    docs = [{"content": "item"}]
    client, _ = _make_client(docs=docs)

    results = client._fallback_text_search(
        query_text="item", top=10, filters=None, select=None, include_total_count=False
    )
    assert results.get_count() is None


# ---------------------------------------------------------------------------
# search — qdrant path (successful)
# ---------------------------------------------------------------------------


def test_search_uses_qdrant_when_available():
    client, mock_qdrant = _make_client()

    fake_point = SimpleNamespace(payload={"content": "result doc"}, score=0.9)
    mock_qdrant.search.return_value = [fake_point]

    with patch.object(client, "_embed_text", return_value=[0.1, 0.2]):
        results = client.search(query_text="query", top=5)

    assert len(results) == 1
    assert results[0]["content"] == "result doc"
    assert results[0]["@search.score"] == pytest.approx(0.9)


def test_search_passes_vector_query_directly():
    client, mock_qdrant = _make_client()
    mock_qdrant.search.return_value = []

    client.search(query_text="q", top=5, vector_query=[0.1, 0.2, 0.3])

    call_kwargs = mock_qdrant.search.call_args[1]
    assert call_kwargs["query_vector"] == [0.1, 0.2, 0.3]


def test_search_falls_back_on_qdrant_exception():
    docs = [{"content": "fallback doc"}]
    client, mock_qdrant = _make_client(docs=docs)
    mock_qdrant.search.side_effect = RuntimeError("qdrant unavailable")

    with patch.object(client, "_embed_text", side_effect=RuntimeError("ollama down")):
        results = client.search(query_text="fallback", top=10)

    # _fallback_text_search is triggered; "fallback" matches "fallback doc"
    assert any("fallback doc" in str(r) for r in results)


def test_search_star_query_uses_empty_string():
    client, mock_qdrant = _make_client()
    mock_qdrant.search.return_value = []

    with patch.object(client, "_embed_text", return_value=[0.0]) as mock_embed:
        client.search(query_text="*", top=5)

    mock_embed.assert_called_once_with("")


def test_search_accepts_search_text_kwarg():
    client, mock_qdrant = _make_client()
    mock_qdrant.search.return_value = []

    with patch.object(client, "_embed_text", return_value=[0.0]):
        client.search(search_text="alt query", top=5)

    # Should not raise; extra_kwargs path handled


def test_search_accepts_filter_kwarg():
    """Covers the `filter in extra_kwargs` branch (line 168)."""
    client, mock_qdrant = _make_client()
    mock_qdrant.search.return_value = []

    with patch.object(client, "_embed_text", return_value=[0.0]):
        client.search(query_text="q", top=5, filter="framework eq 'ISM'")


def test_search_applies_select_on_qdrant_path():
    """Covers the `if select:` payload filtering branch inside the qdrant success path."""
    client, mock_qdrant = _make_client()
    fake_point = SimpleNamespace(
        payload={"content": "result", "corpus": "a", "extra": "drop"},
        score=0.7,
    )
    mock_qdrant.search.return_value = [fake_point]

    with patch.object(client, "_embed_text", return_value=[0.1]):
        results = client.search(query_text="q", top=5, select=["content", "corpus"])

    assert set(results[0].keys()) == {"content", "corpus"}


def test_search_include_total_count():
    """Covers include_total_count=True branch (line 189)."""
    client, mock_qdrant = _make_client()
    fake_point = SimpleNamespace(payload={"content": "result"}, score=0.8)
    mock_qdrant.search.return_value = [fake_point]

    with patch.object(client, "_embed_text", return_value=[0.1]):
        results = client.search(query_text="q", top=5, include_total_count=True)

    assert results.get_count() == 1


# ---------------------------------------------------------------------------
# __init__ constructor
# ---------------------------------------------------------------------------


def test_init_reads_env_defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434")
    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")

    mock_qdrant_client = MagicMock()
    with patch(
        "runtime.search.local_qdrant.LocalQdrantSearchClient.__init__.__module__", create=True
    ):
        pass

    with patch("qdrant_client.QdrantClient", return_value=mock_qdrant_client):
        from runtime.search.local_qdrant import LocalQdrantSearchClient

        client = LocalQdrantSearchClient.__new__(LocalQdrantSearchClient)
        # Call real __init__ with mocked QdrantClient
        import importlib

        import runtime.search.local_qdrant as mod

        real_init = mod.LocalQdrantSearchClient.__init__

        with patch("qdrant_client.QdrantClient", return_value=mock_qdrant_client):
            real_init(client, "my-index")

    assert client._index == "my-index"
    assert client._qdrant_url == "http://qdrant:6333"
    assert client._ollama_base_url == "http://ollama:11434"
    assert client._embedding_model == "nomic-embed-text"


# ---------------------------------------------------------------------------
# load_documents
# ---------------------------------------------------------------------------


def test_load_documents_empty_does_nothing():
    client, mock_qdrant = _make_client()
    client.load_documents([])
    mock_qdrant.create_collection.assert_not_called()


def test_load_documents_skips_docs_with_no_text():
    client, mock_qdrant = _make_client()
    docs = [{"id": "empty"}]  # no content/requirement_text
    client.load_documents(docs)
    # No vectors produced → create_collection not called
    mock_qdrant.create_collection.assert_not_called()


def test_load_documents_creates_collection_and_upserts():
    from qdrant_client.models import Distance, VectorParams

    client, mock_qdrant = _make_client()
    mock_qdrant.collection_exists.return_value = False
    docs = [{"content": "hello world", "id": "doc-1"}]

    with patch.object(client, "_embed_text", return_value=[0.1, 0.2, 0.3]):
        client.load_documents(docs)

    mock_qdrant.create_collection.assert_called_once()
    mock_qdrant.upsert.assert_called_once()
    call_kwargs = mock_qdrant.create_collection.call_args[1]
    assert call_kwargs["collection_name"] == "test-index"


def test_load_documents_deletes_existing_collection():
    client, mock_qdrant = _make_client()
    mock_qdrant.collection_exists.return_value = True
    docs = [{"content": "data", "id": "doc-2"}]

    with patch.object(client, "_embed_text", return_value=[0.5, 0.6]):
        client.load_documents(docs)

    mock_qdrant.delete_collection.assert_called_once_with("test-index")
    mock_qdrant.create_collection.assert_called_once()


# ---------------------------------------------------------------------------
# delete_documents
# ---------------------------------------------------------------------------


def test_delete_documents_removes_matching_docs_from_local_cache():
    docs = [
        {"id": "a", "content": "alpha", "corpus": "b"},
        {"id": "b", "content": "beta", "corpus": "b"},
    ]
    client, _ = _make_client(docs=docs)

    client.delete_documents(documents=[{"id": "a"}])

    assert len(client._docs) == 1
    assert client._docs[0]["id"] == "b"


def test_delete_documents_noop_when_selector_empty():
    docs = [{"id": "a", "content": "alpha"}]
    client, _ = _make_client(docs=docs)

    client.delete_documents(documents=[{}])

    assert len(client._docs) == 1
    assert client._docs[0]["id"] == "a"
