"""Unit tests for search abstraction: factory dispatch and local adapter."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from runtime.search import get_search_client
from runtime.search.local_inmemory import LocalInMemorySearchClient

# ---------------------------------------------------------------------------
# Factory dispatch
# ---------------------------------------------------------------------------


class TestSearchFactory:
    def test_local_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "local")
        client = get_search_client()
        assert isinstance(client, LocalInMemorySearchClient)

    def test_dev_alias(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "dev")
        client = get_search_client()
        assert isinstance(client, LocalInMemorySearchClient)

    def test_argument_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "azure")
        client = get_search_client(cloud_provider="local")
        assert isinstance(client, LocalInMemorySearchClient)

    def test_azure_requires_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AZURE_SEARCH_ENDPOINT", raising=False)
        with pytest.raises(ValueError, match="endpoint"):
            get_search_client(cloud_provider="azure", index_name="idx")

    def test_azure_requires_index_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AZURE_SEARCH_INDEX", raising=False)
        with pytest.raises(ValueError, match="index_name"):
            get_search_client(
                cloud_provider="azure",
                endpoint="https://search.search.windows.net",
            )

    def test_azure_factory_returns_azure_client(self) -> None:
        from runtime.search.azure_search import AzureSearchClient

        mock_sdk = MagicMock()
        with patch("runtime.search.azure_search._AzureSDKSearchClient", return_value=mock_sdk):
            client = get_search_client(
                cloud_provider="azure",
                credential=MagicMock(),
                endpoint="https://search.search.windows.net",
                index_name="grounding-index",
            )
        assert isinstance(client, AzureSearchClient)

    def test_aws_factory_returns_opensearch_client(self) -> None:
        from runtime.search.opensearch import AWSOpenSearchClient

        client = get_search_client(
            cloud_provider="aws",
            endpoint="https://search.us-east-1.es.amazonaws.com",
            index_name="grounding-index",
        )
        assert isinstance(client, AWSOpenSearchClient)

    def test_invalid_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported cloud provider"):
            get_search_client(cloud_provider="gcp")

    def test_local_with_seed_documents(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "local")
        docs = [{"content": "access control"}]
        client = get_search_client(documents=docs)
        assert isinstance(client, LocalInMemorySearchClient)
        results = client.search(query_text="access control", top=5)
        assert len(results) == 1


# ---------------------------------------------------------------------------
# LocalInMemorySearchClient – contract tests
# ---------------------------------------------------------------------------


class TestLocalInMemorySearchClient:
    def _client(self, docs: list[dict[str, Any]] | None = None) -> LocalInMemorySearchClient:
        return LocalInMemorySearchClient(index="test-index", documents=docs)

    def test_index_name(self) -> None:
        assert self._client().index_name == "test-index"

    def test_empty_search_returns_nothing(self) -> None:
        client = self._client()
        results = client.search(query_text="anything", top=10)
        assert results == []

    def test_matching_doc_returned(self) -> None:
        client = self._client([{"content": "network segmentation policy"}])
        results = client.search(query_text="network", top=5)
        assert len(results) == 1
        assert "content" in results[0]

    def test_no_match_returns_empty(self) -> None:
        client = self._client([{"content": "network segmentation policy"}])
        results = client.search(query_text="something completely different", top=5)
        assert results == []

    def test_top_limits_results(self) -> None:
        docs = [{"content": f"network doc {i}"} for i in range(10)]
        client = self._client(docs)
        results = client.search(query_text="network", top=3)
        assert len(results) == 3

    def test_select_filters_fields(self) -> None:
        docs = [{"content": "network", "title": "Security", "score": 0.9}]
        client = self._client(docs)
        results = client.search(query_text="network", top=5, select=["content"])
        assert "content" in results[0]
        assert "title" not in results[0]
        assert "score" not in results[0]

    def test_load_documents_replaces_set(self) -> None:
        client = self._client([{"content": "old doc"}])
        client.load_documents([{"content": "new doc"}])
        old = client.search(query_text="old", top=5)
        assert old == []
        new = client.search(query_text="new", top=5)
        assert len(new) == 1

    def test_vector_query_ignored_no_error(self) -> None:
        """Vector queries are accepted but not applied for local adapter."""
        client = self._client([{"content": "security baseline"}])
        results = client.search(query_text="security", top=5, vector_query=[0.1, 0.2, 0.3])
        assert len(results) == 1

    def test_case_insensitive_match(self) -> None:
        client = self._client([{"content": "Patch Management Controls"}])
        results = client.search(query_text="patch management", top=5)
        assert len(results) == 1


# ---------------------------------------------------------------------------
# AzureSearchClient – mocked interactions
# ---------------------------------------------------------------------------


class TestAzureSearchClient:
    def test_index_name(self) -> None:
        from runtime.search.azure_search import AzureSearchClient

        mock_sdk = MagicMock()
        with patch("runtime.search.azure_search._AzureSDKSearchClient", return_value=mock_sdk):
            client = AzureSearchClient(
                endpoint="https://endpoint.search.windows.net",
                index="my-index",
                credential=MagicMock(),
            )
        assert client.index_name == "my-index"

    def test_search_delegates_to_sdk(self) -> None:
        from runtime.search.azure_search import AzureSearchClient

        mock_sdk_client = MagicMock()
        mock_sdk_client.search.return_value = [{"content": "result1"}]
        with patch(
            "runtime.search.azure_search._AzureSDKSearchClient", return_value=mock_sdk_client
        ):
            client = AzureSearchClient(
                endpoint="https://endpoint.search.windows.net",
                index="my-index",
                credential=MagicMock(),
            )
        results = client.search(query_text="malware protection", top=5)
        mock_sdk_client.search.assert_called_once()
        assert results == [{"content": "result1"}]

    def test_search_with_vector_includes_vectorized_query(self) -> None:
        from runtime.search.azure_search import AzureSearchClient

        mock_sdk_client = MagicMock()
        mock_sdk_client.search.return_value = []
        with patch(
            "runtime.search.azure_search._AzureSDKSearchClient", return_value=mock_sdk_client
        ):
            client = AzureSearchClient(
                endpoint="https://endpoint.search.windows.net",
                index="my-index",
                credential=MagicMock(),
            )
        client.search(query_text="risk", top=5, vector_query=[0.1] * 8)
        call_kwargs = mock_sdk_client.search.call_args.kwargs
        assert "vector_queries" in call_kwargs
        assert len(call_kwargs["vector_queries"]) == 1


# ---------------------------------------------------------------------------
# AWSOpenSearchClient – mocked interactions
# ---------------------------------------------------------------------------


class TestAWSOpenSearchClient:
    def test_requires_endpoint_and_index(self) -> None:
        from runtime.search.opensearch import AWSOpenSearchClient

        with pytest.raises(ValueError, match="endpoint"):
            AWSOpenSearchClient(endpoint="", index="idx")
        with pytest.raises(ValueError, match="index"):
            AWSOpenSearchClient(endpoint="https://example", index="")

    def test_search_posts_signed_request_and_maps_hits(self) -> None:
        from runtime.search.opensearch import AWSOpenSearchClient

        response = MagicMock()
        response.json.return_value = {
            "hits": {
                "hits": [
                    {"_score": 1.25, "_source": {"title": "Doc A", "content": "alpha"}},
                    {"_score": 0.8, "_source": {"title": "Doc B", "content": "beta"}},
                ]
            }
        }

        client = AWSOpenSearchClient(endpoint="https://search.example", index="controls")
        client._signed_headers = MagicMock(return_value={"Authorization": "sig"})
        client._http.post = MagicMock(return_value=response)

        results = client.search(query_text="alpha", top=2)

        assert len(results) == 2
        assert results[0]["title"] == "Doc A"
        assert results[0]["@search.score"] == 1.25
        client._http.post.assert_called_once()

    def test_search_with_select_filters_fields(self) -> None:
        from runtime.search.opensearch import AWSOpenSearchClient

        response = MagicMock()
        response.json.return_value = {
            "hits": {
                "hits": [
                    {"_score": 0.4, "_source": {"title": "Doc A", "content": "alpha", "id": "1"}}
                ]
            }
        }

        client = AWSOpenSearchClient(endpoint="https://search.example", index="controls")
        client._signed_headers = MagicMock(return_value={"Authorization": "sig"})
        client._http.post = MagicMock(return_value=response)

        results = client.search(query_text="alpha", top=2, select=["title"])

        assert results == [{"title": "Doc A"}]

    def test_build_query_body_with_filter_and_vector(self) -> None:
        from runtime.search.opensearch import AWSOpenSearchClient

        client = AWSOpenSearchClient(endpoint="https://search.example", index="controls")
        body = client._build_query_body(
            query_text="secure design",
            top=5,
            vector_query=[0.1, 0.2],
            filters='framework:"NIST CSF"',
        )

        assert body["size"] == 5
        assert "bool" in body["query"]
        assert "filter" in body["query"]["bool"]
