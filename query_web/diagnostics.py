"""Search, storage, and infrastructure diagnostics endpoints for dev/troubleshooting."""

import logging
import os
from typing import Any, cast
from urllib.parse import quote

import requests
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import IndexerStatus
from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
_INTERNAL_ERROR_MESSAGE = "Internal server error; check logs for details."


def register_diagnostics_endpoints(
    app,
    credential,
    config,
    search_client: SearchClient,
    _check_diagnostics_access,
    _target_env_name,
    _is_corpus_upload_enabled,
    _is_ingestion_job_trigger_enabled,
    _latest_ingestion_job_execution,
    _count_blob_prefix,
    _count_search_documents_total_by_filter,
    _utc_now_iso,
    _REQUIRED_INGESTION_METADATA_KEYS,
) -> None:
    """Register all diagnostics endpoints with the FastAPI app."""
    from azure.search.documents.indexes import SearchIndexerClient
    from azure.storage.blob import BlobServiceClient

    def _resolve_acr_registry_name(explicit_registry_name: str = "") -> str:
        """Resolve ACR registry name from environment or explicit param."""
        candidates = [
            explicit_registry_name,
            os.getenv("ACR_NAME", ""),
            os.getenv("AZURE_CONTAINER_REGISTRY_NAME", ""),
            os.getenv("CONTAINER_REGISTRY_NAME", ""),
        ]

        login_server_candidates = [
            os.getenv("ACR_LOGIN_SERVER", ""),
            os.getenv("AZURE_CONTAINER_REGISTRY_LOGIN_SERVER", ""),
            os.getenv("CONTAINER_REGISTRY_LOGIN_SERVER", ""),
        ]
        for login_server in login_server_candidates:
            value = (login_server or "").strip().lower()
            if value.endswith(".azurecr.io"):
                candidates.append(value.split(".", 1)[0])

        for candidate in candidates:
            value = (candidate or "").strip()
            if value:
                return value

        return ""

    def _list_acr_tags_via_management_api(
        *,
        subscription_id: str,
        resource_group: str,
        registry_name: str,
        repository: str,
        limit: int,
    ) -> dict[str, Any]:
        """Fetch ACR repository tags via Management API."""
        token = credential.get_token("https://management.azure.com/.default").token
        encoded_repo = quote(repository, safe="")
        base_url = (
            f"https://management.azure.com/subscriptions/{subscription_id}"
            f"/resourceGroups/{resource_group}"
            f"/providers/Microsoft.ContainerRegistry/registries/{registry_name}"
            f"/repositories/{encoded_repo}/tags"
        )
        url = f"{base_url}?api-version=2023-07-01&orderby=time_desc&n={limit}"

        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )

        if response.status_code >= 400:
            raise RuntimeError(
                "Failed to list ACR tags "
                f"for repository '{repository}': {response.status_code} {response.text}"
            )

        payload = response.json()
        values = payload.get("value", [])
        tags: list[dict[str, Any]] = []
        if isinstance(values, list):
            for item in values:
                if not isinstance(item, dict):
                    continue
                digest = str(item.get("digest") or "").strip() or None
                tags.append(
                    {
                        "name": str(item.get("name") or "").strip(),
                        "digest": digest,
                        "created_time": item.get("createdTime"),
                        "last_update_time": item.get("lastUpdateTime"),
                    }
                )

        return {
            "tags": tags,
            "raw_count": len(values) if isinstance(values, list) else 0,
            "next_link": payload.get("nextLink"),
        }

    def _list_indexer_execution_history(
        indexer_name: str,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Fetch recent indexer execution history with error and warning counts."""
        client = SearchIndexerClient(endpoint=config.search_endpoint, credential=credential)
        try:
            status = client.get_indexer_status(indexer_name)
            execution_history: list[dict[str, Any]] = []

            # Extract execution history from status object if available
            status_dict = status.__dict__ if hasattr(status, "__dict__") else {}
            execution_history_raw = status_dict.get("execution_history", [])

            for idx, execution in enumerate(execution_history_raw):
                if idx >= limit:
                    break
                exec_dict: dict[str, Any] = {}
                if hasattr(execution, "__dict__"):
                    exec_dict = execution.__dict__.copy()
                else:
                    exec_dict = dict(execution) if isinstance(execution, dict) else {}

                # Extract error/warning counts
                errors_count = 0
                warnings_count = 0
                error_items: list[str] = []

                if "errors" in exec_dict:
                    errors_list = exec_dict["errors"]
                    if isinstance(errors_list, list):
                        errors_count = len(errors_list)
                        error_items = [str(e) for e in errors_list[:3]]  # First 3 errors

                if "warnings" in exec_dict:
                    warnings_list = exec_dict["warnings"]
                    if isinstance(warnings_list, list):
                        warnings_count = len(warnings_list)

                execution_history.append(
                    {
                        "start_time": str(exec_dict.get("start_time") or ""),
                        "end_time": str(exec_dict.get("end_time") or ""),
                        "status": str(exec_dict.get("status") or "unknown"),
                        "items_processed": int(exec_dict.get("items_processed") or 0),
                        "items_failed": int(exec_dict.get("items_failed") or 0),
                        "errors_count": errors_count,
                        "warnings_count": warnings_count,
                        "error_samples": error_items,
                    }
                )

            return {
                "execution_history": execution_history,
                "error": None,
            }
        except Exception as exc:
            return {
                "execution_history": [],
                "error": str(exc),
            }

    def _sample_index_documents(
        limit: int = 10,
        include_all_fields: bool = True,
    ) -> dict[str, Any]:
        """Retrieve document samples from the search index with metadata."""
        try:
            select_fields = None
            if not include_all_fields:
                select_fields = [
                    "id",
                    "source_name",
                    "corpus",
                    "corpus_role",
                    "upload_batch",
                    "uploaded_at",
                ]

            results: list[dict[str, Any]] = []
            pager = search_client.search(
                search_text="*",
                top=limit,
                select=select_fields,
            )

            for doc in pager:
                doc_dict: dict[str, Any] = {}
                if hasattr(doc, "__dict__"):
                    doc_dict = doc.__dict__.copy()
                else:
                    doc_dict = dict(doc) if isinstance(doc, dict) else {}

                # Truncate content if present
                if "content" in doc_dict:
                    content = doc_dict["content"]
                    if isinstance(content, str) and len(content) > 500:
                        doc_dict["content"] = content[:500] + "...[truncated]"

                results.append(doc_dict)

            return {
                "documents": results,
                "document_count": len(results),
                "error": None,
            }
        except Exception as exc:
            return {
                "documents": [],
                "document_count": 0,
                "error": str(exc),
            }

    def _validate_blob_metadata_completeness(
        prefix: str = "",
        sample_size: int = 100,
    ) -> dict[str, Any]:
        """Scan blobs and validate required ingestion metadata."""
        if not _is_corpus_upload_enabled():
            return {
                "configured": False,
                "error": "Storage not configured",
            }

        try:
            account_url = f"https://{config.storage_account_name}.blob.core.windows.net"
            client = BlobServiceClient(account_url=account_url, credential=credential)
            container = client.get_container_client(config.storage_container_name)

            prefix_value = prefix.strip() or None
            total_scanned = 0
            missing_metadata_keys: dict[str, int] = {}
            blobs_with_complete_metadata = 0
            sample_blobs: list[dict[str, Any]] = []

            for blob in container.list_blobs(name_starts_with=prefix_value):
                total_scanned += 1
                if total_scanned > sample_size:
                    break

                metadata = getattr(blob, "metadata", None) or {}
                has_all_required = all(
                    str(metadata.get(key) or "").strip()
                    for key in _REQUIRED_INGESTION_METADATA_KEYS
                )

                if has_all_required:
                    blobs_with_complete_metadata += 1
                else:
                    # Track which keys are missing
                    for key in _REQUIRED_INGESTION_METADATA_KEYS:
                        if not str(metadata.get(key) or "").strip():
                            missing_metadata_keys[key] = missing_metadata_keys.get(key, 0) + 1

                if len(sample_blobs) < 5:
                    sample_blobs.append(
                        {
                            "name": str(getattr(blob, "name", "")),
                            "has_complete_metadata": has_all_required,
                            "metadata_keys_present": list(metadata.keys()),
                            "missing_keys": [
                                k
                                for k in _REQUIRED_INGESTION_METADATA_KEYS
                                if not str(metadata.get(k) or "").strip()
                            ],
                        }
                    )

            completeness_pct = (
                (blobs_with_complete_metadata / total_scanned * 100)
                if total_scanned > 0
                else 0.0
            )

            return {
                "configured": True,
                "prefix": prefix_value,
                "total_scanned": total_scanned,
                "blobs_with_complete_metadata": blobs_with_complete_metadata,
                "completeness_percent": round(completeness_pct, 1),
                "missing_metadata_distribution": missing_metadata_keys,
                "sample_blobs": sample_blobs,
                "error": None,
            }
        except Exception as exc:
            return {
                "configured": True,
                "error": str(exc),
            }

    def _test_datasource_connectivity(
        datasource_name: str = "",
    ) -> dict[str, Any]:
        """Test data source connection and enumerate blobs."""
        try:
            idxr_client = SearchIndexerClient(
                endpoint=config.search_endpoint, credential=credential
            )

            ds_name = datasource_name.strip() or os.getenv(
                "AZURE_SEARCH_DATASOURCE_NAME",
                f"{config.search_index_name}-datasource",
            ).strip()

            if not ds_name:
                return {
                    "configured": False,
                    "error": "Data source name not specified or configured",
                }

            ds = idxr_client.get_data_source_connection(ds_name)

            # Extract connection and query details
            connection_str = str(getattr(ds, "connection_string", "") or "")
            container_obj = getattr(ds, "container", None)
            query = str(getattr(container_obj, "query", "") or "") if container_obj else ""

            # Attempt to enumerate blobs from the configured prefix
            container_name = ""
            storage_account = ""
            if connection_str:
                # Parse connection string for account and container
                for part in connection_str.split(";"):
                    if part.startswith("BlobEndpoint="):
                        # Extract account name from URL
                        try:
                            url_part = part.replace("BlobEndpoint=", "").split(".blob")[0]
                            storage_account = url_part.split("://")[-1]
                        except Exception:
                            pass

            blob_enumeration_test = {
                "attempted": False,
                "success": False,
                "blob_count": 0,
                "error": None,
            }

            if _is_corpus_upload_enabled() and query:
                try:
                    blob_enumeration_test["attempted"] = True
                    account_url = (
                        f"https://{config.storage_account_name}.blob.core.windows.net"
                    )
                    client = BlobServiceClient(account_url=account_url, credential=credential)
                    container = client.get_container_client(config.storage_container_name)

                    blob_count = 0
                    for blob in container.list_blobs(name_starts_with=query or None):
                        blob_count += 1
                        if blob_count >= 10:
                            break

                    blob_enumeration_test["success"] = True
                    blob_enumeration_test["blob_count"] = blob_count
                except Exception as exc:
                    blob_enumeration_test["error"] = str(exc)

            return {
                "configured": True,
                "datasource_name": ds_name,
                "storage_account": storage_account,
                "container_name": container_name,
                "data_source_query": query,
                "blob_enumeration_test": blob_enumeration_test,
                "error": None,
            }
        except Exception as exc:
            return {
                "configured": True,
                "error": str(exc),
            }

    def _validate_indexer_field_mappings(
        indexer_name: str = "",
    ) -> dict[str, Any]:
        """Validate that indexer field mappings exist and match index schema."""
        try:
            idx_client = SearchIndexClient(endpoint=config.search_endpoint, credential=credential)
            idxr_client = SearchIndexerClient(
                endpoint=config.search_endpoint, credential=credential
            )

            indexer_name_resolved = indexer_name.strip() or os.getenv(
                "AZURE_SEARCH_INDEXER_NAME",
                f"{config.search_index_name}-indexer",
            ).strip()

            if not indexer_name_resolved:
                return {
                    "configured": False,
                    "error": "Indexer name not specified or configured",
                }

            indexer = idxr_client.get_indexer(indexer_name_resolved)
            index = idx_client.get_index(config.search_index_name)

            # Extract field mappings from indexer
            field_mappings: dict[str, str] = {}
            if hasattr(indexer, "field_mappings") and indexer.field_mappings:
                for mapping in indexer.field_mappings:
                    source = getattr(mapping, "source_field_name", "")
                    target = getattr(mapping, "target_field_name", "")
                    if source and target:
                        field_mappings[source] = target

            # Extract index fields
            index_fields: dict[str, dict[str, Any]] = {}
            if hasattr(index, "fields") and index.fields:
                for field in index.fields:
                    field_name = getattr(field, "name", "")
                    field_type = getattr(field, "type", "")
                    if field_name:
                        index_fields[field_name] = {
                            "type": str(field_type),
                            "searchable": getattr(field, "searchable", False),
                            "filterable": getattr(field, "filterable", False),
                        }

            # Validate mappings
            missing_target_fields = []
            for source, target in field_mappings.items():
                if target not in index_fields:
                    missing_target_fields.append({"source": source, "target": target})

            return {
                "configured": True,
                "indexer_name": indexer_name_resolved,
                "index_name": config.search_index_name,
                "total_mappings": len(field_mappings),
                "valid_mappings": len(field_mappings) - len(missing_target_fields),
                "field_mappings_sample": dict(list(field_mappings.items())[:10]),
                "index_fields_count": len(index_fields),
                "missing_target_fields": missing_target_fields,
                "validation_passed": len(missing_target_fields) == 0,
                "error": None,
            }
        except Exception as exc:
            return {
                "configured": True,
                "error": str(exc),
            }

    # Register endpoints
    @app.get("/api/diagnostics/search/indexer-history")
    def indexer_execution_history_diagnostics(
        request: Request,
        auth_token: str = "",
        limit: int = 10,
    ) -> JSONResponse:
        """Dev-only diagnostics for recent indexer execution history with error counts."""
        denied = _check_diagnostics_access(request, auth_token)
        if denied is not None:
            return denied

        indexer_name = os.getenv(
            "AZURE_SEARCH_INDEXER_NAME",
            f"{config.search_index_name}-indexer",
        ).strip()

        capped_limit = max(1, min(limit, 50))

        try:
            result = _list_indexer_execution_history(indexer_name, limit=capped_limit)

            return JSONResponse(
                {
                    "mode": "search-indexer-history-diagnostics",
                    "target_env": _target_env_name(),
                    "indexer_name": indexer_name,
                    "execution_history": result.get("execution_history", []),
                    "history_error": result.get("error"),
                    "quick_flags": {
                        "recent_errors": bool(
                            any(
                                int(e.get("items_failed", 0)) > 0
                                for e in result.get("execution_history", [])[:3]
                            )
                        ),
                        "execution_history_available": bool(
                            result.get("execution_history")
                        ),
                    },
                }
            )
        except Exception as exc:
            logger.exception(
                "Failed /api/diagnostics/search/indexer-history request: %s", exc
            )
            return JSONResponse({"error": _INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.get("/api/diagnostics/search/index-samples")
    def index_samples_diagnostics(
        request: Request,
        auth_token: str = "",
        limit: int = 10,
        include_all_fields: bool = True,
    ) -> JSONResponse:
        """Dev-only diagnostics to retrieve document samples from the search index."""
        denied = _check_diagnostics_access(request, auth_token)
        if denied is not None:
            return denied

        capped_limit = max(1, min(limit, 50))

        try:
            result = _sample_index_documents(limit=capped_limit, include_all_fields=include_all_fields)

            return JSONResponse(
                {
                    "mode": "search-index-samples-diagnostics",
                    "target_env": _target_env_name(),
                    "index_name": config.search_index_name,
                    "sample_limit": capped_limit,
                    "documents_retrieved": result.get("document_count", 0),
                    "documents": result.get("documents", []),
                    "documents_error": result.get("error"),
                    "quick_flags": {
                        "has_documents": bool(result.get("document_count", 0) > 0),
                        "metadata_fields_present": bool(
                            result.get("document_count", 0) > 0
                            and any("metadata" in doc for doc in result.get("documents", []))
                        ),
                    },
                }
            )
        except Exception as exc:
            logger.exception("Failed /api/diagnostics/search/index-samples request: %s", exc)
            return JSONResponse({"error": _INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.get("/api/diagnostics/storage/metadata-validation")
    def storage_metadata_validation_diagnostics(
        request: Request,
        auth_token: str = "",
        prefix: str = "",
        sample_size: int = 100,
    ) -> JSONResponse:
        """Dev-only diagnostics to validate blob metadata completeness."""
        denied = _check_diagnostics_access(request, auth_token)
        if denied is not None:
            return denied

        capped_sample_size = max(1, min(sample_size, 500))
        prefix_value = prefix.strip() or "corpus-b/by-dedupe/"

        try:
            result = _validate_blob_metadata_completeness(
                prefix=prefix_value, sample_size=capped_sample_size
            )

            return JSONResponse(
                {
                    "mode": "storage-metadata-validation-diagnostics",
                    "target_env": _target_env_name(),
                    "prefix": prefix_value,
                    "sample_size": capped_sample_size,
                    "configured": result.get("configured", False),
                    "total_scanned": result.get("total_scanned", 0),
                    "blobs_with_complete_metadata": result.get(
                        "blobs_with_complete_metadata", 0
                    ),
                    "completeness_percent": result.get("completeness_percent", 0.0),
                    "missing_metadata_distribution": result.get(
                        "missing_metadata_distribution", {}
                    ),
                    "sample_blobs": result.get("sample_blobs", []),
                    "metadata_error": result.get("error"),
                    "quick_flags": {
                        "all_blobs_valid": (
                            result.get("completeness_percent", 0) == 100.0
                            if result.get("total_scanned", 0) > 0
                            else None
                        ),
                        "critical_metadata_missing": bool(
                            result.get("missing_metadata_distribution", {})
                        ),
                    },
                }
            )
        except Exception as exc:
            logger.exception(
                "Failed /api/diagnostics/storage/metadata-validation request: %s", exc
            )
            return JSONResponse({"error": _INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.get("/api/diagnostics/search/datasource-connectivity")
    def datasource_connectivity_diagnostics(
        request: Request,
        auth_token: str = "",
        datasource_name: str = "",
    ) -> JSONResponse:
        """Dev-only diagnostics to test data source connectivity and blob enumeration."""
        denied = _check_diagnostics_access(request, auth_token)
        if denied is not None:
            return denied

        try:
            result = _test_datasource_connectivity(datasource_name=datasource_name)

            return JSONResponse(
                {
                    "mode": "search-datasource-connectivity-diagnostics",
                    "target_env": _target_env_name(),
                    "datasource_name": result.get("datasource_name", ""),
                    "storage_account": result.get("storage_account", ""),
                    "data_source_query": result.get("data_source_query", ""),
                    "blob_enumeration_test": result.get("blob_enumeration_test", {}),
                    "connectivity_error": result.get("error"),
                    "quick_flags": {
                        "datasource_accessible": result.get("error") is None,
                        "blobs_enumerable": result.get("blob_enumeration_test", {}).get(
                            "success", False
                        ),
                    },
                }
            )
        except Exception as exc:
            logger.exception(
                "Failed /api/diagnostics/search/datasource-connectivity request: %s", exc
            )
            return JSONResponse({"error": _INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.get("/api/diagnostics/search/field-mappings")
    def field_mappings_validation_diagnostics(
        request: Request,
        auth_token: str = "",
        indexer_name: str = "",
    ) -> JSONResponse:
        """Dev-only diagnostics to validate indexer field mappings against index schema."""
        denied = _check_diagnostics_access(request, auth_token)
        if denied is not None:
            return denied

        try:
            result = _validate_indexer_field_mappings(indexer_name=indexer_name)

            return JSONResponse(
                {
                    "mode": "search-field-mappings-diagnostics",
                    "target_env": _target_env_name(),
                    "indexer_name": result.get("indexer_name", ""),
                    "index_name": result.get("index_name", ""),
                    "total_mappings": result.get("total_mappings", 0),
                    "valid_mappings": result.get("valid_mappings", 0),
                    "field_mappings_sample": result.get("field_mappings_sample", {}),
                    "index_fields_count": result.get("index_fields_count", 0),
                    "missing_target_fields": result.get("missing_target_fields", []),
                    "validation_passed": result.get("validation_passed", False),
                    "mappings_error": result.get("error"),
                    "quick_flags": {
                        "all_mappings_valid": result.get("validation_passed", False),
                        "missing_fields": bool(result.get("missing_target_fields", [])),
                    },
                }
            )
        except Exception as exc:
            logger.exception("Failed /api/diagnostics/search/field-mappings request: %s", exc)
            return JSONResponse({"error": _INTERNAL_ERROR_MESSAGE}, status_code=500)
