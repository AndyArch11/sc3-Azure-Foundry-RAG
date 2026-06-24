"""Unit tests for search abstraction: factory dispatch and local adapter."""

from __future__ import annotations

import json
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

    def test_delete_documents_removes_matching_records(self) -> None:
        client = self._client(
            [
                {"id": "a", "content": "old doc"},
                {"id": "b", "content": "new doc"},
            ]
        )

        client.delete_documents(documents=[{"id": "a"}])

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

    def test_natural_language_query_matches_key_terms(self) -> None:
        docs = [
            {"content": "AESCSF includes guidance for backup and recovery controls."},
            {"content": "Unrelated endpoint hardening text."},
        ]
        client = self._client(docs)
        results = client.search(query_text="What are the policies on backups for AESCSF?", top=5)
        assert len(results) == 1
        assert "AESCSF" in results[0]["content"]


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

    def test_search_with_vector_includes_vectorised_query(self) -> None:
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
        assert call_kwargs["vector_queries"][0].as_dict()["k"] == 5

    def test_delete_documents_delegates_to_sdk(self) -> None:
        from runtime.search.azure_search import AzureSearchClient

        mock_sdk_client = MagicMock()
        with patch(
            "runtime.search.azure_search._AzureSDKSearchClient", return_value=mock_sdk_client
        ):
            client = AzureSearchClient(
                endpoint="https://endpoint.search.windows.net",
                index="my-index",
                credential=MagicMock(),
            )

        client.delete_documents(documents=[{"requirement_id": "CTRL-1"}])
        mock_sdk_client.delete_documents.assert_called_once_with(
            documents=[{"requirement_id": "CTRL-1"}]
        )


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

    def test_search_retries_without_vector_on_knn_mapping_error(self) -> None:
        from runtime.search.opensearch import AWSOpenSearchClient

        response_400 = MagicMock()
        response_400.status_code = 400
        response_400.json.return_value = {
            "error": {"root_cause": [{"reason": "Field 'embedding' is not knn_vector type."}]},
            "status": 400,
        }

        response_200 = MagicMock()
        response_200.status_code = 200
        response_200.json.return_value = {
            "hits": {"hits": [{"_score": 0.8, "_source": {"title": "Doc B", "content": "beta"}}]}
        }

        client = AWSOpenSearchClient(endpoint="https://search.example", index="grounding")
        client._signed_headers = MagicMock(return_value={"Authorization": "sig"})
        client._http.post = MagicMock(side_effect=[response_400, response_200])

        results = client.search(query_text="secure design", top=3, vector_query=[0.1, 0.2])

        assert len(results) == 1
        assert results[0]["title"] == "Doc B"
        assert client._http.post.call_count == 2

        first_body = json.loads(client._http.post.call_args_list[0].kwargs["data"])
        second_body = json.loads(client._http.post.call_args_list[1].kwargs["data"])

        first_payload = json.dumps(first_body)
        second_payload = json.dumps(second_body)
        assert '"knn"' in first_payload
        assert '"knn"' not in second_payload

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

    def test_build_query_body_with_wildcard_uses_match_all(self) -> None:
        from runtime.search.opensearch import AWSOpenSearchClient

        client = AWSOpenSearchClient(endpoint="https://search.example", index="controls")
        body = client._build_query_body(
            query_text="*",
            top=5,
            vector_query=None,
            filters=None,
        )

        assert body["query"] == {"match_all": {}}

    def test_search_accepts_legacy_search_text_and_filter_kwargs(self) -> None:
        from runtime.search.opensearch import AWSOpenSearchClient

        response = MagicMock()
        response.json.return_value = {
            "hits": {
                "total": {"value": 3, "relation": "eq"},
                "hits": [
                    {"_score": 1.0, "_source": {"id": "a", "content": "alpha"}},
                    {"_score": 0.9, "_source": {"id": "b", "content": "beta"}},
                ],
            }
        }

        client = AWSOpenSearchClient(endpoint="https://search.example", index="controls")
        client._signed_headers = MagicMock(return_value={"Authorization": "sig"})
        client._http.post = MagicMock(return_value=response)

        results = client.search(
            search_text="*",
            filter="framework:ISM",
            include_total_count=True,
            top=5,
        )

        assert len(results) == 2
        assert getattr(results, "get_count")() == 3

    def test_search_raises_when_query_text_missing(self) -> None:
        from runtime.search.opensearch import AWSOpenSearchClient

        client = AWSOpenSearchClient(endpoint="https://search.example", index="controls")

        with pytest.raises(TypeError, match="query_text"):
            client.search(top=3)

    def test_delete_documents_posts_bulk_delete(self) -> None:
        from runtime.search.opensearch import AWSOpenSearchClient

        response = MagicMock()
        response.json.return_value = {"items": []}

        client = AWSOpenSearchClient(endpoint="https://search.example", index="controls")
        client._signed_headers = MagicMock(return_value={"Authorization": "sig"})
        client._http.post = MagicMock(return_value=response)

        client.delete_documents(documents=[{"requirement_id": "A"}, {"id": "B"}])

        assert client._http.post.call_count == 1
        body = client._http.post.call_args.kwargs["data"]
        assert '"_id": "A"' in body
        assert '"_id": "B"' in body

    def test_translate_filter_expression_eq_and_ne_empty(self) -> None:
        from runtime.search.opensearch import AWSOpenSearchClient

        translated = AWSOpenSearchClient._translate_filter_expression(
            "framework eq 'CIS Controls' and corpus ne ''"
        )

        assert translated == '(framework:"CIS Controls") AND (_exists_:corpus)'

    def test_translate_filter_expression_preserves_query_string_syntax(self) -> None:
        from runtime.search.opensearch import AWSOpenSearchClient

        original = 'framework:"NIST CSF" AND corpus:b'
        translated = AWSOpenSearchClient._translate_filter_expression(original)

        assert translated == original

    def test_translate_filter_expression_with_or_group(self) -> None:
        from runtime.search.opensearch import AWSOpenSearchClient

        translated = AWSOpenSearchClient._translate_filter_expression(
            "(corpus eq 'b' or corpus eq 'c' or corpus eq 'legacy')"
        )

        assert translated == '((corpus:"b") OR (corpus:"c") OR (corpus:"legacy"))'

    def test_translate_filter_expression_with_role_and_empty_legacy(self) -> None:
        from runtime.search.opensearch import AWSOpenSearchClient

        translated = AWSOpenSearchClient._translate_filter_expression(
            "((corpus eq 'b' or corpus_role eq 'narrative_guidance') or (corpus eq 'legacy' or corpus eq ''))"
        )

        assert translated == (
            '(((corpus:"b") OR (corpus_role:"narrative_guidance")) OR '
            '((corpus:"legacy") OR (NOT _exists_:corpus)))'
        )
