"""Search, storage, and infrastructure diagnostics endpoints for dev/troubleshooting."""

import logging
import os
from typing import Any, cast
from urllib.parse import quote

import requests
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import IndexerStatus
from fastapi import Request
from fastapi.responses import JSONResponse

from runtime.search import SearchClient

logger = logging.getLogger(__name__)
_INTERNAL_ERROR_MESSAGE = "Internal server error; check logs for details."


def _target_env_name() -> str:
    # TARGET_ENV is the canonical flag in this repo; ENV is accepted as fallback.
    return (
        os.getenv("TARGET_ENV", "").strip().lower() or os.getenv("ENV", "").strip().lower() or "dev"
    )


def _diagnostics_enabled() -> bool:
    return _target_env_name() != "prod"


def check_diagnostics_access(
    request: Request,
    auth_token: str,
    *,
    is_authorised_request: Any,
    unauthorised_message: Any,
) -> JSONResponse | None:
    if is_authorised_request is None or not is_authorised_request(auth_token, request):
        msg = unauthorised_message(request) if callable(unauthorised_message) else "Unauthorised."
        return JSONResponse({"error": msg}, status_code=401)
    if not _diagnostics_enabled():
        return JSONResponse(
            {
                "error": "Diagnostics endpoints are disabled when TARGET_ENV is 'prod'.",
                "target_env": _target_env_name(),
            },
            status_code=403,
        )
    return None


def resolve_acr_registry_name(explicit_registry_name: str = "") -> str:
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


def list_acr_tags_via_management_api(
    *,
    credential: Any,
    requests_module: Any,
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

    response = requests_module.get(
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


def register_diagnostics_endpoints(
    app,
    credential,
    config,
    search_client: SearchClient,
    _is_corpus_upload_enabled,
    _is_ingestion_job_trigger_enabled,
    _latest_ingestion_job_execution,
    _count_blob_prefix,
    _count_search_documents_total_by_filter,
    _utc_now_iso,
    _REQUIRED_INGESTION_METADATA_KEYS,
    svc=None,
    *,
    deps: dict | None = None,
) -> None:
    """Register all diagnostics endpoints with the FastAPI app."""
    from azure.search.documents.indexes import SearchIndexerClient
    from azure.storage.blob import BlobServiceClient

    def _svc_attr(name: str, default: Any) -> Any:
        if isinstance(deps, dict) and name in deps:
            candidate = deps[name]
            return candidate() if callable(candidate) else candidate
        if svc is None:
            return default
        return getattr(svc, name, default)

    def _check_diagnostics_access(request: Request, auth_token: str) -> JSONResponse | None:
        return check_diagnostics_access(
            request,
            auth_token,
            is_authorised_request=_svc_attr("_is_authorised_request", None),
            unauthorised_message=_svc_attr(
                "_unauthorised_message", lambda req=None: "Unauthorised."
            ),
        )

    def _resolve_acr_registry_name(explicit_registry_name: str = "") -> str:
        return resolve_acr_registry_name(explicit_registry_name)

    def _list_acr_tags_via_management_api(
        *,
        subscription_id: str,
        resource_group: str,
        registry_name: str,
        repository: str,
        limit: int,
    ) -> dict[str, Any]:
        return list_acr_tags_via_management_api(
            credential=_svc_attr("credential", credential),
            requests_module=_svc_attr("requests", requests),
            subscription_id=subscription_id,
            resource_group=resource_group,
            registry_name=registry_name,
            repository=repository,
            limit=limit,
        )

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
                warning_items: list[str] = []
                warnings_list: list[Any] = []

                if "errors" in exec_dict:
                    errors_list = exec_dict["errors"]
                    if isinstance(errors_list, list):
                        errors_count = len(errors_list)
                        error_items = [str(e) for e in errors_list[:3]]  # First 3 errors

                if "warnings" in exec_dict:
                    warnings_list = exec_dict["warnings"]
                    if isinstance(warnings_list, list):
                        warnings_count = len(warnings_list)
                        warning_items = [str(w) for w in warnings_list[:3]]

                known_optional_warning_count = sum(
                    1
                    for warning in (warnings_list if isinstance(warnings_list, list) else [])
                    if str(warning).find("Enrichment.ConditionalSkill.default-") >= 0
                    and str(warning).find("Optional skill input is missing or empty") >= 0
                )
                actionable_warning_count = max(0, warnings_count - known_optional_warning_count)

                rate_limit_detected = any(
                    marker in item.lower()
                    for item in [*error_items, *warning_items]
                    for marker in ("ratelimitreached", "toomanyrequests", "retry after")
                )

                execution_history.append(
                    {
                        "start_time": str(exec_dict.get("start_time") or ""),
                        "end_time": str(exec_dict.get("end_time") or ""),
                        "status": str(exec_dict.get("status") or "unknown"),
                        # SDK uses item_count / failed_item_count (not items_processed / items_failed)
                        "items_processed": int(
                            exec_dict.get("item_count") or exec_dict.get("items_processed") or 0
                        ),
                        "items_failed": int(
                            exec_dict.get("failed_item_count") or exec_dict.get("items_failed") or 0
                        ),
                        "errors_count": errors_count,
                        "warnings_count": warnings_count,
                        "known_optional_warnings_count": known_optional_warning_count,
                        "actionable_warnings_count": actionable_warning_count,
                        "error_samples": error_items,
                        "warning_samples": warning_items,
                        "rate_limit_detected": rate_limit_detected,
                    }
                )

            return {
                "execution_history": execution_history,
                "error": None,
            }
        except Exception as exc:
            logger.exception(
                "Indexer execution history fetch failed",
                extra={"event": "indexer_history_fetch_failed", "exc_type": type(exc).__name__},
            )
            return {
                "execution_history": [],
                "error": _INTERNAL_ERROR_MESSAGE,
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
                query_text="*",
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
            logger.exception(
                "Index document sampling failed",
                extra={"event": "index_document_sampling_failed", "exc_type": type(exc).__name__},
            )
            return {
                "documents": [],
                "document_count": 0,
                "error": _INTERNAL_ERROR_MESSAGE,
            }

    def _validate_blob_metadata_completeness(
        prefix: str = "",
        sample_size: int = 100,
        include_values: bool = False,
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

            try:
                blobs_iter = container.list_blobs(
                    name_starts_with=prefix_value,
                    include=["metadata"],
                )
            except TypeError:
                # Support test doubles / older client signatures that do not accept `include`.
                blobs_iter = container.list_blobs(name_starts_with=prefix_value)

            for blob in blobs_iter:
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
                    sample_entry: dict[str, Any] = {
                        "name": str(getattr(blob, "name", "")),
                        "has_complete_metadata": has_all_required,
                        "metadata_keys_present": list(metadata.keys()),
                        "missing_keys": [
                            k
                            for k in _REQUIRED_INGESTION_METADATA_KEYS
                            if not str(metadata.get(k) or "").strip()
                        ],
                    }
                    if include_values:
                        sample_entry["metadata_values"] = {
                            key: str(metadata.get(key) or "") for key in sorted(metadata.keys())
                        }

                    sample_blobs.append(sample_entry)

            completeness_pct = (
                (blobs_with_complete_metadata / total_scanned * 100) if total_scanned > 0 else 0.0
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
            logger.exception(
                "Blob metadata completeness validation failed",
                extra={"event": "blob_metadata_validation_failed", "exc_type": type(exc).__name__},
            )
            return {
                "configured": True,
                "error": _INTERNAL_ERROR_MESSAGE,
            }

    def _test_datasource_connectivity(
        datasource_name: str = "",
    ) -> dict[str, Any]:
        """Test data source connection and enumerate blobs."""
        try:
            idxr_client = SearchIndexerClient(
                endpoint=config.search_endpoint, credential=credential
            )

            ds_name = (
                datasource_name.strip()
                or os.getenv(
                    "AZURE_SEARCH_DATASOURCE_NAME",
                    f"{config.search_index_name}-datasource",
                ).strip()
            )

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

            blob_enumeration_test: dict[str, Any] = {
                "attempted": False,
                "success": False,
                "blob_count": 0,
                "error": None,
            }

            if _is_corpus_upload_enabled() and query:
                try:
                    blob_enumeration_test["attempted"] = True
                    account_url = f"https://{config.storage_account_name}.blob.core.windows.net"
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
                    logger.exception(
                        "Blob enumeration test failed",
                        extra={
                            "event": "blob_enumeration_test_failed",
                            "exc_type": type(exc).__name__,
                        },
                    )
                    blob_enumeration_test["error"] = _INTERNAL_ERROR_MESSAGE

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
            logger.exception(
                "Data source connectivity test failed",
                extra={"event": "data_source_connectivity_failed", "exc_type": type(exc).__name__},
            )
            return {
                "configured": True,
                "error": _INTERNAL_ERROR_MESSAGE,
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

            indexer_name_resolved = (
                indexer_name.strip()
                or os.getenv(
                    "AZURE_SEARCH_INDEXER_NAME",
                    f"{config.search_index_name}-indexer",
                ).strip()
            )

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
            logger.exception(
                "Indexer field mapping validation failed",
                extra={"event": "indexer_field_mapping_failed", "exc_type": type(exc).__name__},
            )
            return {
                "configured": True,
                "error": _INTERNAL_ERROR_MESSAGE,
            }

    # Register endpoints
    @app.get("/api/diagnostics/search/resources")
    def search_resources_diagnostics(request: Request, auth_token: str = "") -> JSONResponse:
        """Dev-only diagnostics for Search resources and current indexer state."""
        denied = _check_diagnostics_access(request, auth_token)
        if denied is not None:
            return denied

        resolved_config = _svc_attr("config", config)
        resolved_credential = _svc_attr("credential", credential)
        search_index_client_cls = _svc_attr("SearchIndexClient", SearchIndexClient)
        search_indexer_client_cls = _svc_attr("SearchIndexerClient", SearchIndexerClient)

        index_client = search_index_client_cls(
            endpoint=resolved_config.search_endpoint,
            credential=resolved_credential,
        )
        indexer_client = search_indexer_client_cls(
            endpoint=resolved_config.search_endpoint,
            credential=resolved_credential,
        )
        configured_names = {
            "index": resolved_config.search_index_name,
            "indexer": os.getenv(
                "AZURE_SEARCH_INDEXER_NAME", f"{resolved_config.search_index_name}-indexer"
            ).strip(),
            "skillset": os.getenv(
                "AZURE_SEARCH_SKILLSET_NAME", f"{resolved_config.search_index_name}-skillset"
            ).strip(),
            "data_source": os.getenv(
                "AZURE_SEARCH_DATASOURCE_NAME", f"{resolved_config.search_index_name}-datasource"
            ).strip(),
        }

        indexes: list[dict[str, Any]] = []
        data_sources: list[dict[str, Any]] = []
        skillsets: list[dict[str, Any]] = []
        indexers: list[dict[str, Any]] = []
        errors: dict[str, str] = {}

        try:
            for index in index_client.list_indexes():
                indexes.append({"name": str(getattr(index, "name", ""))})
        except Exception as exc:
            logger.exception(
                "Failed to list search indexes",
                extra={"event": "search_indexes_list_failed", "exc_type": type(exc).__name__},
            )
            errors["indexes"] = _INTERNAL_ERROR_MESSAGE

        try:
            get_data_source = getattr(indexer_client, "get_data_source_connection", None)
            if callable(get_data_source):
                data_source = get_data_source(configured_names["data_source"])
                container = getattr(data_source, "container", None)
                data_sources.append(
                    {
                        "name": str(getattr(data_source, "name", "")),
                        "type": str(getattr(data_source, "type", "")),
                        "container": {
                            "name": str(getattr(container, "name", "")) if container else "",
                            "query": str(getattr(container, "query", "")) if container else "",
                        },
                    }
                )
            else:
                list_data_sources = getattr(indexer_client, "list_data_source_connections", None)
                if not callable(list_data_sources):
                    list_data_sources = getattr(indexer_client, "list_data_sources", None)
                if not callable(list_data_sources):
                    raise RuntimeError(
                        "SearchIndexerClient data source listing API is unavailable."
                    )
                for data_source in cast(Any, list_data_sources()):
                    container = getattr(data_source, "container", None)
                    data_sources.append(
                        {
                            "name": str(getattr(data_source, "name", "")),
                            "type": str(getattr(data_source, "type", "")),
                            "container": {
                                "name": str(getattr(container, "name", "")) if container else "",
                                "query": str(getattr(container, "query", "")) if container else "",
                            },
                        }
                    )
        except Exception as exc:
            logger.exception(
                "Failed to retrieve data sources",
                extra={"event": "search_data_sources_fetch_failed", "exc_type": type(exc).__name__},
            )
            errors["data_sources"] = _INTERNAL_ERROR_MESSAGE

        try:
            get_skillset = getattr(indexer_client, "get_skillset", None)
            if callable(get_skillset):
                skillset = get_skillset(configured_names["skillset"])
                skills = getattr(skillset, "skills", None) or []
                skillsets.append(
                    {
                        "name": str(getattr(skillset, "name", "")),
                        "skill_count": len(skills),
                    }
                )
            else:
                list_skillsets = getattr(indexer_client, "list_skillsets", None)
                if not callable(list_skillsets):
                    raise RuntimeError("SearchIndexerClient skillset listing API is unavailable.")
                for skillset in cast(Any, list_skillsets()):
                    skills = getattr(skillset, "skills", None) or []
                    skillsets.append(
                        {
                            "name": str(getattr(skillset, "name", "")),
                            "skill_count": len(skills),
                        }
                    )
        except Exception as exc:
            logger.exception(
                "Failed to retrieve skillsets",
                extra={"event": "search_skillsets_fetch_failed", "exc_type": type(exc).__name__},
            )
            errors["skillsets"] = _INTERNAL_ERROR_MESSAGE

        try:
            get_indexer = getattr(indexer_client, "get_indexer", None)
            if callable(get_indexer):
                indexer_iterable: list[Any] = [get_indexer(configured_names["indexer"])]
            else:
                list_indexers = getattr(indexer_client, "list_indexers", None)
                if not callable(list_indexers):
                    raise RuntimeError("SearchIndexerClient indexer listing API is unavailable.")
                indexer_iterable = list(cast(Any, list_indexers()))

            for indexer in indexer_iterable:
                indexer_name = str(getattr(indexer, "name", ""))
                status_summary: dict[str, Any] = {
                    "status": None,
                    "items_processed": None,
                    "items_failed": None,
                    "error_message": None,
                }
                try:
                    status = indexer_client.get_indexer_status(indexer_name)
                    run = getattr(status, "last_result", None)
                    if run is not None:
                        status_summary = {
                            "status": str(getattr(run, "status", "") or ""),
                            "items_processed": getattr(run, "item_count", None),
                            "items_failed": getattr(run, "failed_item_count", None),
                            "error_message": getattr(run, "error_message", None),
                        }
                except Exception as exc:
                    logger.exception(
                        "Failed to get indexer status",
                        extra={
                            "event": "indexer_status_fetch_failed",
                            "indexer_name": indexer_name,
                            "exc_type": type(exc).__name__,
                        },
                    )
                    status_summary["error_message"] = _INTERNAL_ERROR_MESSAGE

                indexers.append(
                    {
                        "name": indexer_name,
                        "data_source_name": str(getattr(indexer, "data_source_name", "")),
                        "target_index_name": str(getattr(indexer, "target_index_name", "")),
                        "skillset_name": str(getattr(indexer, "skillset_name", "")),
                        "last_result": status_summary,
                    }
                )
        except Exception as exc:
            logger.exception(
                "Failed to retrieve indexers",
                extra={"event": "search_indexers_fetch_failed", "exc_type": type(exc).__name__},
            )
            errors["indexers"] = _INTERNAL_ERROR_MESSAGE

        payload: dict[str, Any] = {
            "mode": "search-resources-diagnostics",
            "target_env": _target_env_name(),
            "search_endpoint": resolved_config.search_endpoint,
            "configured_names": configured_names,
            "indexes": indexes,
            "data_sources": data_sources,
            "skillsets": skillsets,
            "indexers": indexers,
        }
        if errors:
            payload["errors"] = errors
            payload["partial"] = True

        return JSONResponse(payload)

    @app.get("/api/diagnostics/storage/blobs")
    def storage_blobs_diagnostics(
        request: Request,
        auth_token: str = "",
        prefix: str = "",
        limit: int = 200,
        include_metadata: bool = False,
    ) -> JSONResponse:
        """Dev-only diagnostics for blob inventory in the grounding-data container."""
        denied = _check_diagnostics_access(request, auth_token)
        if denied is not None:
            return denied

        resolved_config = _svc_attr("config", config)
        resolved_credential = _svc_attr("credential", credential)
        blob_service_client_cls = _svc_attr("BlobServiceClient", BlobServiceClient)
        is_corpus_upload_enabled = _svc_attr("_is_corpus_upload_enabled", _is_corpus_upload_enabled)

        if not is_corpus_upload_enabled():
            return JSONResponse(
                {
                    "configured": False,
                    "message": "Corpus upload/storage is not configured for this query-web instance.",
                    "target_env": _target_env_name(),
                }
            )

        try:
            capped_limit = max(1, min(limit, 1000))
            prefix_value = prefix.strip()

            account_url = f"https://{resolved_config.storage_account_name}.blob.core.windows.net"
            client = blob_service_client_cls(
                account_url=account_url, credential=resolved_credential
            )
            container = client.get_container_client(resolved_config.storage_container_name)

            blob_items: list[dict[str, Any]] = []
            scanned = 0
            list_kwargs: dict[str, Any] = {"name_starts_with": prefix_value or None}
            if include_metadata:
                list_kwargs["include"] = ["metadata"]

            try:
                blobs_iter = container.list_blobs(**list_kwargs)
            except TypeError:
                # Support test doubles / older client signatures that do not accept `include`.
                blobs_iter = container.list_blobs(name_starts_with=prefix_value or None)

            for blob in blobs_iter:
                scanned += 1
                if len(blob_items) >= capped_limit:
                    continue

                item: dict[str, Any] = {
                    "name": str(getattr(blob, "name", "")),
                    "size": int(getattr(blob, "size", 0) or 0),
                    "content_type": str(
                        getattr(getattr(blob, "content_settings", None), "content_type", "") or ""
                    ),
                    "last_modified": str(getattr(blob, "last_modified", "") or ""),
                    "etag": str(getattr(blob, "etag", "") or ""),
                }
                if include_metadata:
                    item["metadata"] = dict(getattr(blob, "metadata", None) or {})
                blob_items.append(item)

            return JSONResponse(
                {
                    "mode": "storage-blobs-diagnostics",
                    "configured": True,
                    "target_env": _target_env_name(),
                    "storage_account_name": resolved_config.storage_account_name,
                    "storage_container_name": resolved_config.storage_container_name,
                    "prefix": prefix_value or None,
                    "limit": capped_limit,
                    "include_metadata": include_metadata,
                    "returned": len(blob_items),
                    "scanned": scanned,
                    "truncated": scanned > len(blob_items),
                    "blobs": blob_items,
                }
            )
        except Exception as exc:
            logger.exception(
                "Failed storage blobs diagnostics request",
                extra={"event": "diagnostics_storage_blobs_failed", "exc_type": type(exc).__name__},
            )
            return JSONResponse({"error": _INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.get("/api/diagnostics/ingestion/overview")
    def ingestion_overview_diagnostics(
        request: Request,
        auth_token: str = "",
        sample_limit: int = 30,
        include_blob_samples: bool = True,
    ) -> JSONResponse:
        """Dev-only aggregate diagnostics to troubleshoot ingestion mismatches quickly."""
        denied = _check_diagnostics_access(request, auth_token)
        if denied is not None:
            return denied

        resolved_config = _svc_attr("config", config)
        resolved_credential = _svc_attr("credential", credential)
        resolved_search_client = _svc_attr("search_client", search_client)
        blob_service_client_cls = _svc_attr("BlobServiceClient", BlobServiceClient)
        search_indexer_client_cls = _svc_attr("SearchIndexerClient", SearchIndexerClient)
        is_corpus_upload_enabled = _svc_attr("_is_corpus_upload_enabled", _is_corpus_upload_enabled)
        is_ingestion_job_trigger_enabled = _svc_attr(
            "_is_ingestion_job_trigger_enabled", _is_ingestion_job_trigger_enabled
        )
        latest_ingestion_job_execution = _svc_attr(
            "_latest_ingestion_job_execution", _latest_ingestion_job_execution
        )
        count_blob_prefix = _svc_attr("_count_blob_prefix", _count_blob_prefix)
        count_search_documents_total_by_filter = _svc_attr(
            "_count_search_documents_total_by_filter", _count_search_documents_total_by_filter
        )
        utc_now_iso = _svc_attr("_utc_now_iso", _utc_now_iso)

        capped_sample_limit = max(0, min(sample_limit, 120))

        grounding_total = 0
        try:
            pager = resolved_search_client.search(
                search_text="*",
                top=1,
                include_total_count=True,
                select=["id"],
            )
            for _ in pager:
                break
            grounding_total = int(pager.get_count() or 0)
        except Exception as exc:
            logger.warning(
                "Failed to count grounding documents",
                extra={"event": "grounding_doc_count_failed", "exc_type": type(exc).__name__},
            )

        search_counts = {
            "grounding_total": grounding_total,
            "corpus_b": count_search_documents_total_by_filter(
                resolved_search_client,
                filter_expr="corpus eq 'b'",
            ),
            "corpus_c": count_search_documents_total_by_filter(
                resolved_search_client,
                filter_expr="corpus eq 'c'",
            ),
            "corpus_legacy": count_search_documents_total_by_filter(
                resolved_search_client,
                filter_expr="corpus eq 'legacy'",
            ),
        }

        storage_counts: dict[str, int] = {}
        storage_samples: dict[str, list[dict[str, Any]]] = {}
        storage_error: str | None = None

        prefixes = {
            "corpus_a_source": "corpus-a/source/",
            "corpus_b_dedupe": "corpus-b/by-dedupe/",
            "corpus_c_dedupe": "corpus-c/by-dedupe/",
        }

        if is_corpus_upload_enabled():
            for label, prefix in prefixes.items():
                storage_counts[label] = int(count_blob_prefix(prefix).get("would_delete", 0))

            if include_blob_samples and capped_sample_limit > 0:
                per_prefix_limit = max(1, capped_sample_limit // max(1, len(prefixes)))
                try:
                    account_url = (
                        f"https://{resolved_config.storage_account_name}.blob.core.windows.net"
                    )
                    blob_client = blob_service_client_cls(
                        account_url=account_url,
                        credential=resolved_credential,
                    )
                    container = blob_client.get_container_client(
                        resolved_config.storage_container_name
                    )

                    for label, prefix in prefixes.items():
                        rows: list[dict[str, Any]] = []
                        for blob in container.list_blobs(
                            name_starts_with=prefix,
                            include=["metadata"],
                        ):
                            if len(rows) >= per_prefix_limit:
                                break
                            rows.append(
                                {
                                    "name": str(getattr(blob, "name", "")),
                                    "size": int(getattr(blob, "size", 0) or 0),
                                    "last_modified": str(getattr(blob, "last_modified", "") or ""),
                                    "corpus": str(
                                        (getattr(blob, "metadata", None) or {}).get("corpus", "")
                                    ),
                                    "upload_batch": str(
                                        (getattr(blob, "metadata", None) or {}).get(
                                            "upload_batch", ""
                                        )
                                    ),
                                }
                            )
                        storage_samples[label] = rows
                except Exception as exc:
                    storage_error = str(exc)
        else:
            storage_error = "Storage upload integration is not configured for this instance."

        latest_job: dict[str, Any] | None = None
        latest_job_error: str | None = None
        if is_ingestion_job_trigger_enabled():
            try:
                latest_job = latest_ingestion_job_execution()
            except Exception as exc:
                latest_job_error = str(exc)

        indexer_history_summary: dict[str, Any] | None = None
        indexer_history_error: str | None = None
        try:
            indexer_name = os.getenv(
                "AZURE_SEARCH_INDEXER_NAME", f"{resolved_config.search_index_name}-indexer"
            ).strip()
            idxr_client = search_indexer_client_cls(
                endpoint=resolved_config.search_endpoint,
                credential=resolved_credential,
            )
            status = idxr_client.get_indexer_status(indexer_name)
            status_dict = status.__dict__ if hasattr(status, "__dict__") else {}
            execution_history_raw = list(status_dict.get("execution_history", []) or [])
            recent_entries: list[dict[str, Any]] = []
            for execution in execution_history_raw[:3]:
                if hasattr(execution, "__dict__"):
                    exec_dict = execution.__dict__.copy()
                else:
                    exec_dict = dict(execution) if isinstance(execution, dict) else {}
                raw_errors = exec_dict.get("errors")
                errors_list = raw_errors if isinstance(raw_errors, list) else []
                raw_warnings = exec_dict.get("warnings")
                warnings_list = raw_warnings if isinstance(raw_warnings, list) else []
                known_optional_warning_count = sum(
                    1
                    for warning in warnings_list
                    if str(warning).find("Enrichment.ConditionalSkill.default-") >= 0
                    and str(warning).find("Optional skill input is missing or empty") >= 0
                )
                actionable_warning_count = max(0, len(warnings_list) - known_optional_warning_count)
                samples = [str(item) for item in [*errors_list[:2], *warnings_list[:2]]]
                recent_entries.append(
                    {
                        "status": str(exec_dict.get("status") or "unknown"),
                        "items_failed": int(
                            exec_dict.get("failed_item_count") or exec_dict.get("items_failed") or 0
                        ),
                        "errors_count": len(errors_list),
                        "warnings_count": len(warnings_list),
                        "known_optional_warnings_count": known_optional_warning_count,
                        "actionable_warnings_count": actionable_warning_count,
                        "rate_limit_detected": any(
                            marker in sample.lower()
                            for sample in samples
                            for marker in ("ratelimitreached", "toomanyrequests", "retry after")
                        ),
                        "samples": samples,
                    }
                )
            indexer_history_summary = {
                "indexer_name": indexer_name,
                "recent_entries": recent_entries,
                "recent_rate_limits": any(
                    bool(entry.get("rate_limit_detected")) for entry in recent_entries
                ),
                "recent_warnings": any(
                    int(entry.get("actionable_warnings_count", 0)) > 0 for entry in recent_entries
                ),
                "recent_known_optional_warnings": any(
                    int(entry.get("known_optional_warnings_count", 0)) > 0
                    for entry in recent_entries
                ),
                "recent_non_success": any(
                    str(entry.get("status") or "").strip().lower() not in {"success", "reset"}
                    for entry in recent_entries
                ),
            }
        except Exception as exc:
            indexer_history_error = str(exc)

        quick_flags = {
            "storage_has_corpus_b_but_search_corpus_b_empty": bool(
                storage_counts.get("corpus_b_dedupe", 0) > 0
                and search_counts.get("corpus_b", 0) == 0
            ),
            "storage_has_corpus_c_but_search_corpus_c_empty": bool(
                storage_counts.get("corpus_c_dedupe", 0) > 0
                and search_counts.get("corpus_c", 0) == 0
            ),
            "legacy_docs_present": bool(search_counts.get("corpus_legacy", 0) > 0),
            "recent_indexer_rate_limits": bool(
                (indexer_history_summary or {}).get("recent_rate_limits")
            ),
            "recent_indexer_warnings": bool((indexer_history_summary or {}).get("recent_warnings")),
            "recent_indexer_known_optional_warnings": bool(
                (indexer_history_summary or {}).get("recent_known_optional_warnings")
            ),
        }

        configured_datasource_name = os.getenv(
            "AZURE_SEARCH_DATASOURCE_NAME", f"{resolved_config.search_index_name}-datasource"
        ).strip()
        active_datasource_query: str | None = None
        scope_query_error: str | None = None
        try:
            idxr_client = search_indexer_client_cls(
                endpoint=resolved_config.search_endpoint,
                credential=resolved_credential,
            )
            ds = idxr_client.get_data_source_connection(configured_datasource_name)
            container = getattr(ds, "container", None)
            query_text = str(getattr(container, "query", "") or "").strip()
            active_datasource_query = query_text or None
        except Exception as exc:
            scope_query_error = str(exc)

        risk_level = "unknown"
        risk_reason = "Unable to assess scope bleed risk."
        if scope_query_error:
            risk_level = "unknown"
            risk_reason = "Data source query could not be retrieved."
        elif not active_datasource_query:
            corpus_a_count = storage_counts.get("corpus_a_source", 0)
            corpus_b_count = storage_counts.get("corpus_b_dedupe", 0)
            corpus_c_count = storage_counts.get("corpus_c_dedupe", 0)
            if corpus_a_count > 0 and (corpus_b_count > 0 or corpus_c_count > 0):
                risk_level = "high"
                risk_reason = "Data source query is empty while multiple corpus prefixes exist in the same container."
            elif corpus_a_count > 0 or corpus_b_count > 0 or corpus_c_count > 0:
                risk_level = "medium"
                risk_reason = "Data source query is empty; full container scan is possible."
            else:
                risk_level = "low"
                risk_reason = "No corpus blobs found in storage counts."
        elif active_datasource_query in {"corpus-b/by-dedupe/", "corpus-c/by-dedupe/"}:
            risk_level = "low"
            risk_reason = "Data source query is scoped to a corpus-specific dedupe prefix."
        else:
            risk_level = "medium"
            risk_reason = (
                "Data source query is set but not one of the expected corpus dedupe prefixes."
            )

        scope_query_diagnostics = {
            "configured_data_source_name": configured_datasource_name,
            "active_data_source_query": active_datasource_query,
            "active_data_source_query_error": scope_query_error,
            "scope_bleed_risk_level": risk_level,
            "scope_bleed_risk_reason": risk_reason,
        }

        return JSONResponse(
            {
                "mode": "ingestion-overview-diagnostics",
                "target_env": _target_env_name(),
                "generated_at": _utc_now_iso(),
                "config": {
                    "search_endpoint": resolved_config.search_endpoint,
                    "search_index_name": resolved_config.search_index_name,
                    "storage_account_name": resolved_config.storage_account_name,
                    "storage_container_name": resolved_config.storage_container_name,
                    "ingestion_job_trigger_enabled": is_ingestion_job_trigger_enabled(),
                    "ingestion_job_name": resolved_config.ingestion_job_name,
                },
                "search_counts": search_counts,
                "storage_counts": storage_counts,
                "storage_samples": storage_samples,
                "storage_error": storage_error,
                "latest_ingestion_job": latest_job,
                "latest_ingestion_job_error": latest_job_error,
                "indexer_history_summary": indexer_history_summary,
                "indexer_history_error": indexer_history_error,
                "quick_flags": quick_flags,
                "scope_query_diagnostics": scope_query_diagnostics,
            }
        )

    @app.get("/api/diagnostics/acr/images")
    def acr_images_diagnostics(
        request: Request,
        auth_token: str = "",
        repository: str = "query-web",
        limit: int = 30,
        registry_name: str = "",
        expected_tag: str = "",
    ) -> JSONResponse:
        """Dev-only diagnostics for ACR repository tags used by deployments."""
        denied = _check_diagnostics_access(request, auth_token)
        if denied is not None:
            return denied

        resolved_config = _svc_attr("config", config)
        repo = repository.strip() or "query-web"
        capped_limit = max(1, min(limit, 200))
        resolved_registry_name = _resolve_acr_registry_name(registry_name)
        expected_tag_value = expected_tag.strip()

        if not resolved_registry_name:
            return JSONResponse(
                {
                    "mode": "acr-images-diagnostics",
                    "configured": False,
                    "target_env": _target_env_name(),
                    "message": "ACR registry name is not configured.",
                    "repository": repo,
                    "expected_tag": expected_tag_value or None,
                }
            )

        subscription_id = resolved_config.ingestion_job_subscription_id
        resource_group = resolved_config.ingestion_job_resource_group
        if not subscription_id or not resource_group:
            return JSONResponse(
                {
                    "mode": "acr-images-diagnostics",
                    "configured": False,
                    "target_env": _target_env_name(),
                    "registry_name": resolved_registry_name,
                    "repository": repo,
                    "expected_tag": expected_tag_value or None,
                    "message": (
                        "ACR diagnostics requires INGESTION_JOB_SUBSCRIPTION_ID and "
                        "INGESTION_JOB_RESOURCE_GROUP to be configured."
                    ),
                }
            )

        try:
            result = _list_acr_tags_via_management_api(
                subscription_id=subscription_id,
                resource_group=resource_group,
                registry_name=resolved_registry_name,
                repository=repo,
                limit=capped_limit,
            )
            tags = result.get("tags", [])
            distinct_digests = {
                str(tag.get("digest") or "").strip()
                for tag in tags
                if str(tag.get("digest") or "").strip()
            }
            expected_tag_present = False
            if expected_tag_value:
                expected_tag_present = any(
                    str(tag.get("name") or "").strip() == expected_tag_value for tag in tags
                )

            return JSONResponse(
                {
                    "mode": "acr-images-diagnostics",
                    "configured": True,
                    "target_env": _target_env_name(),
                    "registry_name": resolved_registry_name,
                    "repository": repo,
                    "expected_tag": expected_tag_value or None,
                    "limit": capped_limit,
                    "tag_count": len(tags),
                    "distinct_digest_count": len(distinct_digests),
                    "has_results": bool(tags),
                    "tags": tags,
                    "next_link": result.get("next_link"),
                    "quick_flags": {
                        "repository_empty": not bool(tags),
                        "multiple_tags_share_digest": (
                            len(tags) > len(distinct_digests) if tags else False
                        ),
                        "expected_tag_present": (
                            expected_tag_present if expected_tag_value else None
                        ),
                    },
                }
            )
        except Exception as exc:
            logger.exception(
                "Failed ACR images diagnostics request",
                extra={"event": "diagnostics_acr_images_failed", "exc_type": type(exc).__name__},
            )
            return JSONResponse(
                {
                    "mode": "acr-images-diagnostics",
                    "configured": True,
                    "target_env": _target_env_name(),
                    "registry_name": resolved_registry_name,
                    "repository": repo,
                    "expected_tag": expected_tag_value or None,
                    "error": _INTERNAL_ERROR_MESSAGE,
                },
                status_code=500,
            )

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
                                or int(e.get("errors_count", 0)) > 0
                                for e in result.get("execution_history", [])[:3]
                            )
                        ),
                        "recent_warnings": bool(
                            any(
                                int(e.get("actionable_warnings_count", 0)) > 0
                                for e in result.get("execution_history", [])[:3]
                            )
                        ),
                        "recent_known_optional_warnings": bool(
                            any(
                                int(e.get("known_optional_warnings_count", 0)) > 0
                                for e in result.get("execution_history", [])[:3]
                            )
                        ),
                        "recent_non_success": bool(
                            any(
                                str(e.get("status") or "").strip().lower()
                                not in {"success", "reset"}
                                for e in result.get("execution_history", [])[:3]
                            )
                        ),
                        "recent_rate_limits": bool(
                            any(
                                bool(e.get("rate_limit_detected"))
                                for e in result.get("execution_history", [])[:5]
                            )
                        ),
                        "execution_history_available": bool(result.get("execution_history")),
                    },
                }
            )
        except Exception as exc:
            logger.exception(
                "Failed search indexer history diagnostics request",
                extra={
                    "event": "diagnostics_indexer_history_failed",
                    "exc_type": type(exc).__name__,
                },
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
            result = _sample_index_documents(
                limit=capped_limit, include_all_fields=include_all_fields
            )

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
            logger.exception(
                "Failed search index samples diagnostics request",
                extra={"event": "diagnostics_index_samples_failed", "exc_type": type(exc).__name__},
            )
            return JSONResponse({"error": _INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.get("/api/diagnostics/storage/metadata-validation")
    def storage_metadata_validation_diagnostics(
        request: Request,
        auth_token: str = "",
        prefix: str = "",
        sample_size: int = 100,
        include_values: bool = False,
    ) -> JSONResponse:
        """Dev-only diagnostics to validate blob metadata completeness."""
        denied = _check_diagnostics_access(request, auth_token)
        if denied is not None:
            return denied

        capped_sample_size = max(1, min(sample_size, 500))
        prefix_value = prefix.strip() or "corpus-b/by-dedupe/"

        try:
            result = _validate_blob_metadata_completeness(
                prefix=prefix_value,
                sample_size=capped_sample_size,
                include_values=include_values,
            )

            return JSONResponse(
                {
                    "mode": "storage-metadata-validation-diagnostics",
                    "target_env": _target_env_name(),
                    "prefix": prefix_value,
                    "sample_size": capped_sample_size,
                    "include_values": include_values,
                    "configured": result.get("configured", False),
                    "total_scanned": result.get("total_scanned", 0),
                    "blobs_with_complete_metadata": result.get("blobs_with_complete_metadata", 0),
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
                "Failed storage metadata validation diagnostics request",
                extra={
                    "event": "diagnostics_metadata_validation_failed",
                    "exc_type": type(exc).__name__,
                },
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
                "Failed data source connectivity diagnostics request",
                extra={"event": "diagnostics_data_source_failed", "exc_type": type(exc).__name__},
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
            logger.exception(
                "Failed search field mappings diagnostics request",
                extra={
                    "event": "diagnostics_field_mappings_failed",
                    "exc_type": type(exc).__name__,
                },
            )
            return JSONResponse({"error": _INTERNAL_ERROR_MESSAGE}, status_code=500)
