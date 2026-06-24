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
        - boolean combinations via "and" / "or" with optional parentheses

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

        pattern = re.compile(
            r"([A-Za-z_][A-Za-z0-9_]*)\s+(eq|ne)\s+'((?:[^']|'')*)'",
            flags=re.IGNORECASE,
        )

        replaced_any = False

        def _replacement(match: re.Match[str]) -> str:
            nonlocal replaced_any
            replaced_any = True

            field, operator, raw_value = match.groups()
            value = raw_value.replace("''", "'")
            op = operator.lower()

            if op == "eq":
                if value == "":
                    # OpenSearch cannot reliably query empty strings; keep broad fallback.
                    return f"(NOT _exists_:{field})"
                return f"({field}:{cls._escape_query_value(value)})"

            # ne
            if value == "":
                return f"(_exists_:{field})"
            return f"(NOT {field}:{cls._escape_query_value(value)})"

        translated = pattern.sub(_replacement, text)
        if not replaced_any:
            return text

        translated = re.sub(r"\band\b", "AND", translated, flags=re.IGNORECASE)
        translated = re.sub(r"\bor\b", "OR", translated, flags=re.IGNORECASE)

        # If any OData operators remain, fallback to the original expression.
        if re.search(r"\b(eq|ne)\b", translated, flags=re.IGNORECASE):
            return text

        return translated

    @property
    def index_name(self) -> str:
        return self._index

    @staticmethod
    def _parse_status_code(response: requests.Response) -> int:
        try:
            return int(getattr(response, "status_code", 200) or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _parse_error_body(response: requests.Response) -> Any:
        try:
            return response.json()
        except (ValueError, AttributeError):
            return getattr(response, "text", "")

    @staticmethod
    def _is_knn_vector_mapping_error(error_body: Any) -> bool:
        text = str(error_body).lower()
        return "not knn_vector type" in text or "is not knn_vector type" in text

    def _log_search_error(
        self,
        *,
        status_code: int,
        filters: str | None,
        body_payload: dict[str, Any],
        error_body: Any,
    ) -> None:
        error_body_text = str(error_body)
        if len(error_body_text) > 2000:
            error_body_text = error_body_text[:2000] + "...<truncated>"
        query_payload_text = json.dumps(body_payload, ensure_ascii=True)
        if len(query_payload_text) > 2000:
            query_payload_text = query_payload_text[:2000] + "...<truncated>"
        logger.error(
            (
                "OpenSearch search error: status=%s filter=%r "
                "error=%s query_payload=%s"
            ),
            status_code,
            filters,
            error_body_text,
            query_payload_text,
            extra={
                "status_code": status_code,
                "error_body": error_body,
                "filter_expression": filters,
                "query_body": body_payload,
            },
        )

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
        status_code = self._parse_status_code(response)

        # Log translated filter for debugging
        if filters:
            logger.debug(
                "OpenSearch search filter",
                extra={
                    "filter_expression": filters,
                    "status_code": status_code,
                },
            )

        if status_code >= 400:
            error_body = self._parse_error_body(response)
            self._log_search_error(
                status_code=status_code,
                filters=filters,
                body_payload=body_payload,
                error_body=error_body,
            )

            # Some OpenSearch domains have an 'embedding' field that is not
            # mapped as knn_vector. Fall back to lexical retrieval so evidence
            # retrieval remains available instead of failing hard.
            if vector_query and self._is_knn_vector_mapping_error(error_body):
                logger.warning(
                    "OpenSearch KNN unsupported for index %s; retrying search without vector query",
                    self._index,
                    extra={"status_code": status_code, "filter_expression": filters},
                )
                body_payload = self._build_query_body(
                    query_text=query_text,
                    top=top,
                    vector_query=None,
                    filters=filters,
                )
                body = json.dumps(body_payload)
                headers = self._signed_headers("POST", url, body)
                response = self._http.post(
                    url,
                    data=body,
                    headers=headers,
                    timeout=self._timeout_seconds,
                    operation="search_documents",
                )
                status_code = self._parse_status_code(response)

                if status_code >= 400:
                    error_body = self._parse_error_body(response)
                    self._log_search_error(
                        status_code=status_code,
                        filters=filters,
                        body_payload=body_payload,
                        error_body=error_body,
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

    def delete_documents(self, *, documents: list[dict[str, Any]]) -> None:
        """Delete documents by primary-key style selectors using OpenSearch bulk delete."""

        selectors: list[str] = []
        for doc in documents:
            if not isinstance(doc, dict):
                continue
            doc_id = str(doc.get("id") or doc.get("requirement_id") or "").strip()
            if doc_id:
                selectors.append(doc_id)

        if not selectors:
            return

        bulk_url = f"{self._endpoint}/_bulk?refresh=true"
        lines = [
            json.dumps({"delete": {"_index": self._index, "_id": doc_id}}, ensure_ascii=True)
            for doc_id in selectors
        ]
        body = "\n".join(lines) + "\n"
        headers = self._signed_headers("POST", bulk_url, body)
        response = self._http.post(
            bulk_url,
            data=body,
            headers=headers,
            timeout=self._timeout_seconds,
            operation="delete_documents",
        )
        response.raise_for_status()

    def load_documents(self, docs: list[dict[str, Any]]) -> None:
        """Unsupported for cloud backends; retained for protocol compatibility."""
        raise NotImplementedError("AWSOpenSearchClient does not support load_documents")
