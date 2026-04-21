"""AWS OpenSearch search adapter."""

from __future__ import annotations

import json
from typing import Any

import requests


class AWSOpenSearchClient:
    """SearchClient backed by AWS OpenSearch Service."""

    def __init__(
        self,
        endpoint: str,
        index: str,
        session: Any = None,
        region_name: str | None = None,
        service_name: str = "es",
        timeout_seconds: int = 30,
    ) -> None:
        if not endpoint:
            raise ValueError("endpoint must be set for AWS OpenSearch")
        if not index:
            raise ValueError("index must be set for AWS OpenSearch")
        self._endpoint = endpoint.rstrip("/")
        self._index = index
        self._session = session
        self._region_name = region_name
        self._service_name = service_name
        self._timeout_seconds = timeout_seconds
        self._http = requests.Session()

    def _get_session(self) -> Any:
        if self._session is not None:
            return self._session

        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError(
                "boto3 is required for AWS search provider but is not installed"
            ) from exc

        self._session = boto3.Session(region_name=self._region_name)
        return self._session

    def _signed_headers(self, method: str, url: str, body: str) -> dict[str, str]:
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest

        session = self._get_session()
        credentials = session.get_credentials()
        if credentials is None:
            raise RuntimeError("Unable to resolve AWS credentials for OpenSearch request signing")

        frozen_credentials = credentials.get_frozen_credentials()
        request = AWSRequest(
            method=method,
            url=url,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        SigV4Auth(
            frozen_credentials,
            self._service_name,
            session.region_name or self._region_name or "us-east-1",
        ).add_auth(request)
        return dict(request.headers.items())

    def _build_query_body(
        self,
        *,
        query_text: str,
        top: int,
        vector_query: list[float] | None,
        filters: str | None,
    ) -> dict[str, Any]:
        if vector_query:
            vector_clause: dict[str, Any] = {
                "knn": {
                    "embedding": {
                        "vector": vector_query,
                        "k": top,
                    }
                }
            }
            if query_text.strip():
                query: dict[str, Any] = {
                    "bool": {
                        "must": [
                            {
                                "multi_match": {
                                    "query": query_text,
                                    "fields": ["content^3", "title^2", "chunk", "text", "*"],
                                    "type": "best_fields",
                                }
                            }
                        ],
                        "should": [vector_clause],
                    }
                }
            else:
                query = vector_clause
        elif query_text.strip():
            query = {
                "multi_match": {
                    "query": query_text,
                    "fields": ["content^3", "title^2", "chunk", "text", "*"],
                    "type": "best_fields",
                }
            }
        else:
            query = {"match_all": {}}

        if filters:
            query = {
                "bool": {
                    "must": [query],
                    "filter": [{"query_string": {"query": filters}}],
                }
            }

        return {
            "size": max(1, top),
            "query": query,
        }

    @property
    def index_name(self) -> str:
        return self._index

    def search(
        self,
        *,
        query_text: str,
        top: int,
        vector_query: list[float] | None = None,
        filters: str | None = None,
        select: list[str] | None = None,
        **extra_kwargs: Any,  # noqa: ARG002 – provider hints ignored by OpenSearch
    ) -> list[dict[str, Any]]:
        body_payload = self._build_query_body(
            query_text=query_text,
            top=top,
            vector_query=vector_query,
            filters=filters,
        )
        body = json.dumps(body_payload)

        url = f"{self._endpoint}/{self._index}/_search"
        headers = self._signed_headers("POST", url, body)
        response = self._http.post(
            url,
            data=body,
            headers=headers,
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()

        payload = response.json()
        hits = payload.get("hits", {}).get("hits", [])

        results: list[dict[str, Any]] = []
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            source = hit.get("_source")
            if not isinstance(source, dict):
                source = {}
            doc = dict(source)
            if "_score" in hit:
                doc["@search.score"] = hit.get("_score")
            if select:
                doc = {field: doc[field] for field in select if field in doc}
            results.append(doc)

        return results

    def load_documents(self, docs: list[dict[str, Any]]) -> None:
        """Unsupported for cloud backends; retained for protocol compatibility."""
        raise NotImplementedError("AWSOpenSearchClient does not support load_documents")
