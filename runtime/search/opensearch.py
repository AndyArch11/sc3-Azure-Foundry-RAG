"""AWS OpenSearch search adapter."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

from runtime.outbound_instrumentation import InstrumentedRequestsSession

logger = logging.getLogger(__name__)


class _SearchResults(list[dict[str, Any]]):
    """List-like search results with optional total count metadata."""

    def __init__(self, items: list[dict[str, Any]], total_count: int | None = None) -> None:
        super().__init__(items)
        self._total_count = total_count

    def get_count(self) -> int | None:
        return self._total_count


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
        self._http = InstrumentedRequestsSession(logger=logger, system="aws-opensearch")

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
        normalised_query = (query_text or "").strip()
        has_text_query = bool(normalised_query and normalised_query != "*")

        if vector_query:
            vector_clause: dict[str, Any] = {
                "knn": {
                    "embedding": {
                        "vector": vector_query,
                        "k": top,
                    }
                }
            }
            if has_text_query:
                query: dict[str, Any] = {
                    "bool": {
                        "must": [
                            {
                                "multi_match": {
                                    "query": normalised_query,
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
        elif has_text_query:
            query = {
                "multi_match": {
                    "query": normalised_query,
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

    @staticmethod
    def _escape_query_value(value: str) -> str:
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'

    @classmethod
    def _translate_filter_expression(cls, filters: str | None) -> str | None:
        """Translate simple OData filters into OpenSearch query_string syntax.

        Supported forms:
        - field eq 'value'
        - field ne 'value'
        - field ne ''  -> _exists_:field
        - conjunctions via "and"

        If parsing fails, the original filter string is returned unchanged.
        """
        if not filters:
            return None

        text = str(filters).strip()
        if not text:
            return None

        # If caller already provided query_string syntax, pass through as-is.
        if " eq " not in text and " ne " not in text:
            return text

        parts = re.split(r"\s+and\s+", text, flags=re.IGNORECASE)
        translated: list[str] = []
        pattern = re.compile(
            r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+(eq|ne)\s+'((?:[^']|'')*)'\s*$",
            flags=re.IGNORECASE,
        )

        for part in parts:
            match = pattern.match(part)
            if not match:
                return text
            field, operator, raw_value = match.groups()
            value = raw_value.replace("''", "'")
            op = operator.lower()

            if op == "eq":
                if value == "":
                    # OpenSearch cannot reliably query empty strings; keep broad fallback.
                    translated.append(f"NOT _exists_:{field}")
                else:
                    translated.append(f"{field}:{cls._escape_query_value(value)}")
                continue

            # ne
            if value == "":
                translated.append(f"_exists_:{field}")
            else:
                translated.append(f"NOT {field}:{cls._escape_query_value(value)}")

        return " AND ".join(translated) if translated else text

    @property
    def index_name(self) -> str:
        return self._index

    def search(
        self,
        *,
        query_text: str | None = None,
        top: int,
        vector_query: list[float] | None = None,
        filters: str | None = None,
        select: list[str] | None = None,
        **extra_kwargs: Any,
    ) -> list[dict[str, Any]]:
        # Backward compatibility with older call sites that pass Azure SDK-style
        # kwargs (search_text/filter/include_total_count).
        if query_text is None:
            legacy_query = extra_kwargs.pop("search_text", None)
            if legacy_query is None:
                raise TypeError(
                    "AWSOpenSearchClient.search() missing required keyword argument: "
                    "'query_text' (or legacy 'search_text')"
                )
            query_text = str(legacy_query)

        if filters is None and "filter" in extra_kwargs:
            filters = extra_kwargs.pop("filter")

        # Provider hint is accepted for parity but not required by OpenSearch.
        extra_kwargs.pop("include_total_count", None)

        filters = self._translate_filter_expression(filters)

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
            operation="search_documents",
        )
        response.raise_for_status()

        payload = response.json()
        hits = payload.get("hits", {}).get("hits", [])
        total_raw = payload.get("hits", {}).get("total")
        total_count: int | None = None
        if isinstance(total_raw, dict):
            value = total_raw.get("value")
            if isinstance(value, (int, float)):
                total_count = int(value)
        elif isinstance(total_raw, (int, float)):
            total_count = int(total_raw)

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

        return _SearchResults(items=results, total_count=total_count)

    def load_documents(self, docs: list[dict[str, Any]]) -> None:
        """Unsupported for cloud backends; retained for protocol compatibility."""
        raise NotImplementedError("AWSOpenSearchClient does not support load_documents")
