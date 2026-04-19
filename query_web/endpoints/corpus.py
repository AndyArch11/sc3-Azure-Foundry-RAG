"""Corpus management and ingestion endpoints."""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import requests  # type: ignore[import-untyped]
from fastapi import File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------


class CorpusAIngestRequest(BaseModel):
    frameworks: list[str] | None = None
    replace_existing: bool = False
    dry_run: bool = False
    no_guidance: bool = False
    auth_token: str = ""


class CorpusClearRequest(BaseModel):
    clear_blobs: bool = False
    dry_run: bool = False
    auth_token: str = ""


class CorpusAClearRequest(BaseModel):
    frameworks: list[str] | None = None
    dry_run: bool = False
    auth_token: str = ""


# ---------------------------------------------------------------------------
# Endpoint registration
# ---------------------------------------------------------------------------


def register_corpus_endpoints(app: Any, svc: Any) -> None:
    """Register corpus management, ingestion, and Confluence poll-status endpoints.

    Parameters
    ----------
    app : FastAPI
        The application instance.
    svc : module
        Service container (the app module at runtime).  All helpers are accessed
        via ``svc.attribute`` at *call time* so that ``patch.object(svc, ...)``
        patches work correctly in tests.
    """

    @app.post("/api/corpus-b/ingest")
    async def upload_corpus_b_and_trigger(
        request: Request,
        files: list[UploadFile] = File(...),
        trigger_job: bool = Form(True),
        reindex_on_dedupe: bool = Form(False),
        auth_token: str = Form(""),
    ) -> JSONResponse:
        if not svc._is_authorised_request(auth_token, request):
            return JSONResponse({"error": svc._unauthorised_message(request)}, status_code=401)

        if not files:
            return JSONResponse({"error": "No files uploaded."}, status_code=400)
        for file in files:
            if not svc._is_allowed_filetype(file.filename or ""):
                return JSONResponse(
                    {
                        "error": (
                            f"File type not allowed: {file.filename}. "
                            f"Allowed: {', '.join(sorted(svc.ALLOWED_EXTENSIONS))}"
                        )
                    },
                    status_code=400,
                )
            if not svc._extension_matches_mime(file.filename or "", file.content_type or ""):
                return JSONResponse(
                    {
                        "error": (
                            f"File type/content mismatch: {file.filename} "
                            f"(content_type: {file.content_type})"
                        )
                    },
                    status_code=400,
                )

        user_id = svc._get_user_id(auth_token, str(uuid.uuid4()))

        try:
            upload_result = svc._upload_corpus_b_files(files, user_id=user_id)
            trigger_result: dict[str, Any] | None = None
            reindex_touch: dict[str, Any] | None = None
            scope_query = "corpus-b/by-dedupe/"
            effective_scope_query: str | None = None
            should_trigger_for_reindex = (
                reindex_on_dedupe
                and not upload_result["uploaded"]
                and bool(upload_result["skipped"])
            )
            if should_trigger_for_reindex:
                dedupe_hashes = svc._extract_dedupe_hashes(upload_result["skipped"])
                reindex_touch = svc._mark_dedupe_blobs_for_reindex(
                    "b", dedupe_hashes, user_id=user_id
                )
            if trigger_job and (upload_result["uploaded"] or should_trigger_for_reindex):
                try:
                    trigger_result = svc._trigger_ingestion_job_with_args(
                        [
                            "--mode",
                            "azure",
                            "--skip-upload",
                            "--storage-container-query",
                            scope_query,
                        ]
                    )
                    effective_scope_query = scope_query
                except Exception as exc:
                    logger.warning(
                        "Corpus B scoped job start failed; falling back to default job args: %s",
                        exc,
                    )
                    trigger_result = svc._trigger_ingestion_job()
                    effective_scope_query = None

            latest_job: dict[str, Any] | None = None
            if trigger_result:
                try:
                    latest_job = svc._latest_ingestion_job_execution()
                except Exception as exc:
                    logger.warning("Failed to fetch latest ingestion job execution: %s", exc)

            message = ""
            if not upload_result["uploaded"]:
                if should_trigger_for_reindex:
                    message = (
                        "No new Corpus B files were uploaded. Matching Corpus B blobs were marked "
                        "for reindex to re-index existing blobs, and ingestion was triggered in the background."
                    )
                else:
                    message = (
                        "No new Corpus B files were uploaded; all files were skipped or failed, "
                        "so no ingestion job was triggered and no new upload batch was created."
                    )

            status_code = 207 if upload_result["failed"] else 200

            return JSONResponse(
                {
                    "mode": "corpus-b-ingest",
                    "storage_account_name": svc.config.storage_account_name,
                    "storage_container_name": svc.config.storage_container_name,
                    "uploaded_count": len(upload_result["uploaded"]),
                    "skipped_count": len(upload_result["skipped"]),
                    "failed_count": len(upload_result["failed"]),
                    "upload": upload_result,
                    "triggered_job": bool(trigger_result),
                    "job": trigger_result,
                    "job_latest": latest_job,
                    "requested_scope_query": scope_query,
                    "effective_scope_query": effective_scope_query,
                    "scope_query_applied": bool(effective_scope_query),
                    "reindex_on_dedupe": reindex_on_dedupe,
                    "reindex_touch": reindex_touch,
                    "indexer_reset": {
                        "performed": bool(should_trigger_for_reindex),
                        "strategy": "blob_metadata_touch",
                    },
                    "indexing_notice": (
                        "Ingestion runs asynchronously. Indexed counts can remain unchanged "
                        "until the job reaches Succeeded."
                    ),
                    "message": message,
                },
                status_code=status_code,
            )
        except Exception as exc:
            logger.exception("Failed /api/corpus-b/ingest request: %s", exc)
            return JSONResponse({"error": svc._INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.post("/api/corpus-c/ingest")
    async def upload_corpus_c_and_trigger(
        request: Request,
        files: list[UploadFile] = File(...),
        trigger_job: bool = Form(True),
        reindex_on_dedupe: bool = Form(False),
        auth_token: str = Form(""),
    ) -> JSONResponse:
        if not svc._is_authorised_request(auth_token, request):
            return JSONResponse({"error": svc._unauthorised_message(request)}, status_code=401)

        if not files:
            return JSONResponse({"error": "No files uploaded."}, status_code=400)
        for file in files:
            if not svc._is_allowed_filetype(file.filename or ""):
                return JSONResponse(
                    {
                        "error": (
                            f"File type not allowed: {file.filename}. "
                            f"Allowed: {', '.join(sorted(svc.ALLOWED_EXTENSIONS))}"
                        )
                    },
                    status_code=400,
                )
            if not svc._extension_matches_mime(file.filename or "", file.content_type or ""):
                return JSONResponse(
                    {
                        "error": (
                            f"File type/content mismatch: {file.filename} "
                            f"(content_type: {file.content_type})"
                        )
                    },
                    status_code=400,
                )

        user_id = svc._get_user_id(auth_token, str(uuid.uuid4()))

        try:
            upload_result = svc._upload_corpus_c_files(files, user_id=user_id)
            trigger_result = None
            reindex_touch = None
            scope_query = "corpus-c/by-dedupe/"
            effective_scope_query = None
            should_trigger_for_reindex = (
                reindex_on_dedupe
                and not upload_result["uploaded"]
                and bool(upload_result["skipped"])
            )
            if should_trigger_for_reindex:
                dedupe_hashes = svc._extract_dedupe_hashes(upload_result["skipped"])
                reindex_touch = svc._mark_dedupe_blobs_for_reindex(
                    "c", dedupe_hashes, user_id=user_id
                )
            if trigger_job and (upload_result["uploaded"] or should_trigger_for_reindex):
                try:
                    trigger_result = svc._trigger_ingestion_job_with_args(
                        [
                            "--mode",
                            "azure",
                            "--skip-upload",
                            "--storage-container-query",
                            scope_query,
                        ]
                    )
                    effective_scope_query = scope_query
                except Exception as exc:
                    logger.warning(
                        "Corpus C scoped job start failed; falling back to default job args: %s",
                        exc,
                    )
                    trigger_result = svc._trigger_ingestion_job()
                    effective_scope_query = None

            latest_job = None
            if trigger_result:
                try:
                    latest_job = svc._latest_ingestion_job_execution()
                except Exception as exc:
                    logger.warning("Failed to fetch latest ingestion job execution: %s", exc)

            message = ""
            if not upload_result["uploaded"]:
                if should_trigger_for_reindex:
                    message = (
                        "No new Corpus C files were uploaded. Matching Corpus C blobs were marked "
                        "for reindex to re-index existing blobs, and ingestion was triggered in the background."
                    )
                else:
                    message = (
                        "No new Corpus C files were uploaded; all files were skipped or failed, "
                        "so no ingestion job was triggered and no new upload batch was created."
                    )

            status_code = 207 if upload_result["failed"] else 200

            return JSONResponse(
                {
                    "mode": "corpus-c-ingest",
                    "storage_account_name": svc.config.storage_account_name,
                    "storage_container_name": svc.config.storage_container_name,
                    "uploaded_count": len(upload_result["uploaded"]),
                    "skipped_count": len(upload_result["skipped"]),
                    "failed_count": len(upload_result["failed"]),
                    "upload": upload_result,
                    "triggered_job": bool(trigger_result),
                    "job": trigger_result,
                    "job_latest": latest_job,
                    "requested_scope_query": scope_query,
                    "effective_scope_query": effective_scope_query,
                    "scope_query_applied": bool(effective_scope_query),
                    "reindex_on_dedupe": reindex_on_dedupe,
                    "reindex_touch": reindex_touch,
                    "indexer_reset": {
                        "performed": bool(should_trigger_for_reindex),
                        "strategy": "blob_metadata_touch",
                    },
                    "indexing_notice": (
                        "Ingestion runs asynchronously. Indexed counts can remain unchanged "
                        "until the job reaches Succeeded."
                    ),
                    "message": message,
                },
                status_code=status_code,
            )
        except Exception as exc:
            logger.exception("Failed /api/corpus-c/ingest request: %s", exc)
            return JSONResponse({"error": svc._INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.post("/api/corpus-a/clear")
    def clear_corpus_a(request: Request, payload: CorpusAClearRequest) -> JSONResponse:
        if not svc._is_authorised_request(payload.auth_token, request):
            return JSONResponse({"error": svc._unauthorised_message(request)}, status_code=401)

        frameworks = svc._selected_corpus_a_frameworks(payload.frameworks)
        per_framework: dict[str, Any] = {}
        total_deleted = 0
        total_would_delete = 0
        try:
            for key in frameworks:
                framework_name = svc._CORPUS_A_FRAMEWORKS[key]
                escaped = framework_name.replace("'", "''")
                if payload.dry_run:
                    result = svc._count_search_documents_by_filter(
                        svc.controls_search_client,
                        filter_expr=f"framework eq '{escaped}'",
                    )
                    total_would_delete += result["would_delete"]
                else:
                    result = svc._delete_search_documents_by_filter(
                        svc.controls_search_client,
                        filter_expr=f"framework eq '{escaped}'",
                        key_field="requirement_id",
                    )
                    total_deleted += result["deleted"]
                per_framework[key] = {
                    "framework": framework_name,
                    **result,
                }

            return JSONResponse(
                {
                    "mode": "corpus-a-clear",
                    "total_deleted": total_deleted,
                    "total_would_delete": total_would_delete,
                    "dry_run": payload.dry_run,
                    "frameworks": per_framework,
                }
            )
        except Exception as exc:
            logger.exception("Failed /api/corpus-a/clear request: %s", exc)
            return JSONResponse({"error": svc._INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.post("/api/corpus-b/clear")
    def clear_corpus_b(request: Request, payload: CorpusClearRequest) -> JSONResponse:
        if not svc._is_authorised_request(payload.auth_token, request):
            return JSONResponse({"error": svc._unauthorised_message(request)}, status_code=401)

        try:
            if payload.dry_run:
                index_result = svc._count_search_documents_by_filter(
                    svc.search_client,
                    filter_expr="corpus eq 'b'",
                )
            else:
                index_result = svc._delete_search_documents_by_filter(
                    svc.search_client,
                    filter_expr="corpus eq 'b'",
                    key_field="id",
                )

            blob_result: dict[str, int] = (
                {"deleted": 0} if not payload.dry_run else {"would_delete": 0}
            )
            if payload.clear_blobs:
                blob_result = (
                    svc._count_blob_prefix("corpus-b/by-dedupe/")
                    if payload.dry_run
                    else svc._delete_blob_prefix("corpus-b/by-dedupe/")
                )

            return JSONResponse(
                {
                    "mode": "corpus-b-clear",
                    "index": index_result,
                    "blobs": blob_result,
                    "clear_blobs": payload.clear_blobs,
                    "dry_run": payload.dry_run,
                }
            )
        except Exception as exc:
            logger.exception("Failed /api/corpus-b/clear request: %s", exc)
            return JSONResponse({"error": svc._INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.post("/api/corpus-c/clear")
    def clear_corpus_c(request: Request, payload: CorpusClearRequest) -> JSONResponse:
        if not svc._is_authorised_request(payload.auth_token, request):
            return JSONResponse({"error": svc._unauthorised_message(request)}, status_code=401)

        try:
            if payload.dry_run:
                index_result = svc._count_search_documents_by_filter(
                    svc.search_client,
                    filter_expr="corpus eq 'c'",
                )
            else:
                index_result = svc._delete_search_documents_by_filter(
                    svc.search_client,
                    filter_expr="corpus eq 'c'",
                    key_field="id",
                )

            blob_result = (
                {"deleted": 0} if not payload.dry_run else {"would_delete": 0}
            )
            if payload.clear_blobs:
                blob_result = (
                    svc._count_blob_prefix("corpus-c/by-dedupe/")
                    if payload.dry_run
                    else svc._delete_blob_prefix("corpus-c/by-dedupe/")
                )

            return JSONResponse(
                {
                    "mode": "corpus-c-clear",
                    "index": index_result,
                    "blobs": blob_result,
                    "clear_blobs": payload.clear_blobs,
                    "dry_run": payload.dry_run,
                }
            )
        except Exception as exc:
            logger.exception("Failed /api/corpus-c/clear request: %s", exc)
            return JSONResponse({"error": svc._INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.post("/api/corpus-a/upload")
    async def upload_corpus_a_reference_documents(
        request: Request,
        files: list[UploadFile] = File(...),
        framework: str = Form(""),
        trigger_job: bool = Form(True),
        replace_existing: bool = Form(False),
        dry_run: bool = Form(False),
        no_guidance: bool = Form(False),
        auth_token: str = Form(""),
    ) -> JSONResponse:
        if not svc._is_authorised_request(auth_token, request):
            return JSONResponse({"error": svc._unauthorised_message(request)}, status_code=401)

        if not files:
            return JSONResponse({"error": "No files uploaded."}, status_code=400)
        for file in files:
            if not svc._is_allowed_filetype(file.filename or ""):
                return JSONResponse(
                    {
                        "error": (
                            f"File type not allowed: {file.filename}. "
                            f"Allowed: {', '.join(sorted(svc.ALLOWED_EXTENSIONS))}"
                        )
                    },
                    status_code=400,
                )
            if not svc._extension_matches_mime(file.filename or "", file.content_type or ""):
                return JSONResponse(
                    {
                        "error": (
                            f"File type/content mismatch: {file.filename} "
                            f"(content_type: {file.content_type})"
                        )
                    },
                    status_code=400,
                )

        try:
            framework_key = svc._normalise_corpus_a_framework_key(framework)
            raw_framework = (framework or "").strip().lower()
            auto_mode = raw_framework in {"", "auto", "both", "all"}
            if (not auto_mode) and (
                not framework_key
                or framework_key not in svc._CORPUS_A_REFERENCE_UPLOAD_TARGETS
            ):
                return JSONResponse(
                    {
                        "error": (
                            "Corpus A source document upload supports cis_controls, pci_dss, or auto mode."
                        )
                    },
                    status_code=400,
                )

            user_id = svc._get_user_id(auth_token, str(uuid.uuid4()))
            upload_results: list[dict[str, Any]] = []
            if auto_mode:
                classified = svc._classify_corpus_a_auto_uploads(files)
                for key in sorted(classified.keys()):
                    upload_results.append(
                        svc._upload_corpus_a_reference_files(
                            classified[key],
                            user_id=user_id,
                            framework=key,
                        )
                    )
            else:
                selected_framework_key = cast(str, framework_key)
                upload_results.append(
                    svc._upload_corpus_a_reference_files(
                        files,
                        user_id=user_id,
                        framework=selected_framework_key,
                    )
                )

            triggered_jobs: list[dict[str, Any]] = []
            if trigger_job:
                for upload_result in upload_results:
                    if not upload_result["uploaded"] or upload_result["failed"]:
                        continue
                    args_override = [
                        "--mode",
                        "controls",
                        "--controls-framework",
                        str(upload_result["framework"]),
                        "--controls-source-prefix",
                        str(upload_result["source_prefix"]),
                    ]
                    if replace_existing:
                        args_override.append("--replace-existing")
                    if dry_run:
                        args_override.append("--dry-run")
                    if no_guidance:
                        args_override.append("--no-guidance")
                    trigger_result = svc._trigger_ingestion_job_with_args(args_override)
                    triggered_jobs.append(
                        {
                            "framework": upload_result["framework"],
                            "job": trigger_result,
                        }
                    )

            total_uploaded = sum(len(item["uploaded"]) for item in upload_results)
            total_failed = sum(len(item["failed"]) for item in upload_results)

            message = ""
            if total_failed:
                message = (
                    "One or more Corpus A source files failed to upload; "
                    "ingestion job was not started for failed framework uploads."
                )
            elif not trigger_job:
                message = (
                    "Corpus A source files staged successfully. "
                    "Trigger the controls ingestion job separately if needed."
                )
            elif triggered_jobs:
                triggered_frameworks = ", ".join(job["framework"] for job in triggered_jobs)
                message = (
                    f"Corpus A source files uploaded and ingestion job triggered for: {triggered_frameworks}. "
                    "Check job status with the 'Job Diagnostics' button, or Azure Container Apps > Job > Execution History."
                )

            status_code = 200 if total_failed == 0 else 207
            primary = upload_results[0]
            return JSONResponse(
                {
                    "mode": "corpus-a-upload",
                    "framework": (
                        "auto" if auto_mode and len(upload_results) > 1 else primary["framework"]
                    ),
                    "framework_name": (
                        "Multiple"
                        if auto_mode and len(upload_results) > 1
                        else primary["framework_name"]
                    ),
                    "storage_account_name": svc.config.storage_account_name,
                    "storage_container_name": svc.config.storage_container_name,
                    "uploaded_count": total_uploaded,
                    "failed_count": total_failed,
                    "upload": primary,
                    "uploads": upload_results,
                    "triggered_job": bool(triggered_jobs),
                    "job": triggered_jobs[0]["job"] if len(triggered_jobs) == 1 else None,
                    "jobs": triggered_jobs,
                    "replace_existing": replace_existing,
                    "dry_run": dry_run,
                    "no_guidance": no_guidance,
                    "message": message,
                },
                status_code=status_code,
            )
        except ValueError as exc:
            logger.warning("Bad request to /api/corpus-a/upload: %s", exc)
            return JSONResponse({"error": "Invalid request parameters."}, status_code=400)
        except Exception as exc:
            logger.exception("Failed /api/corpus-a/upload request: %s", exc)
            return JSONResponse({"error": svc._INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.get("/api/corpus-a/status")
    def corpus_a_status(request: Request, auth_token: str = "") -> JSONResponse:
        if not svc._is_authorised_request(auth_token, request):
            return JSONResponse({"error": svc._unauthorised_message(request)}, status_code=401)

        try:
            status = svc._controls_framework_ingestion_status()
            return JSONResponse(
                {
                    "mode": "corpus-a-status",
                    "frameworks": status,
                }
            )
        except Exception as exc:
            logger.exception("Failed /api/corpus-a/status request: %s", exc)
            return JSONResponse({"error": svc._INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.get("/api/corpus-a/list")
    def corpus_a_list(
        request: Request, auth_token: str = "", limit: int = 100, framework: str = ""
    ) -> JSONResponse:
        if not svc._is_authorised_request(auth_token, request):
            return JSONResponse({"error": svc._unauthorised_message(request)}, status_code=401)

        try:
            filter_expr = "framework ne ''"
            selected = framework.strip()
            if selected:
                canonical = svc._canonical_framework_name(selected)
                if canonical is None:
                    key = svc._normalise_corpus_a_framework_key(selected)
                    canonical = svc._CORPUS_A_FRAMEWORKS.get(key or "", "") if key else None
                if canonical:
                    escaped = canonical.replace("'", "''")
                    filter_expr = f"framework eq '{escaped}'"

            listing = svc._list_search_documents_by_filter(
                svc.controls_search_client,
                filter_expr=filter_expr,
                select_fields=[
                    "requirement_id",
                    "framework",
                    "framework_version",
                    "control_family",
                    "source_uri",
                    "ingestion_loaded_at",
                ],
                limit=limit,
            )

            return JSONResponse(
                {
                    "mode": "corpus-a-list",
                    "framework_filter": selected or None,
                    **listing,
                }
            )
        except Exception as exc:
            logger.exception("Failed /api/corpus-a/list request: %s", exc)
            return JSONResponse({"error": svc._INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.get("/api/ingestion-job/diagnostics")
    def ingestion_job_diagnostics(request: Request, auth_token: str = "") -> JSONResponse:
        """Fetch Container App Job execution history and logs for debugging."""
        if not svc._is_authorised_request(auth_token, request):
            return JSONResponse({"error": svc._unauthorised_message(request)}, status_code=401)

        if not svc._is_ingestion_job_trigger_enabled():
            return JSONResponse(
                {
                    "configured": False,
                    "message": "Ingestion job trigger is not configured.",
                }
            )

        try:
            token = svc.credential.get_token("https://management.azure.com/.default").token
            url = (
                f"https://management.azure.com/subscriptions/{svc.config.ingestion_job_subscription_id}"
                f"/resourceGroups/{svc.config.ingestion_job_resource_group}"
                f"/providers/Microsoft.App/jobs/{svc.config.ingestion_job_name}/executions"
                "?api-version=2024-03-01"
            )
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )

            if response.status_code >= 400:
                return JSONResponse(
                    {
                        "configured": True,
                        "error": f"Failed to fetch job executions: {response.status_code}",
                        "details": response.text,
                    }
                )

            executions_data = response.json()
            executions = executions_data.get("value", [])

            if not executions:
                return JSONResponse(
                    {
                        "configured": True,
                        "executions": [],
                        "message": "No job executions found. Check if the job has been triggered.",
                    }
                )

            recent_executions = []
            for exec_item in sorted(
                executions,
                key=lambda x: x.get("properties", {}).get("startTime", ""),
                reverse=True,
            )[:5]:
                props = exec_item.get("properties", {})
                recent_executions.append(
                    {
                        "id": exec_item.get("id", ""),
                        "status": props.get("status", "Unknown"),
                        "startTime": props.get("startTime", ""),
                        "endTime": props.get("endTime", ""),
                        "detailedStatus": {
                            "activeReplicaCount": props.get("detailedStatus", {}).get(
                                "activeReplicaCount"
                            ),
                            "failedCount": props.get("detailedStatus", {}).get("failedCount"),
                            "runningCount": props.get("detailedStatus", {}).get("runningCount"),
                            "succeededCount": props.get("detailedStatus", {}).get("succeededCount"),
                        },
                    }
                )

            return JSONResponse(
                {
                    "configured": True,
                    "job_name": svc.config.ingestion_job_name,
                    "recent_executions": recent_executions,
                    "note": "Check Azure Portal > Container Apps > Job > Execution History for detailed logs",
                }
            )
        except Exception as exc:
            logger.exception("Failed /api/ingestion-job/diagnostics request: %s", exc)
            return JSONResponse({"error": svc._INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.get("/api/corpus-b/list")
    def corpus_b_list(
        request: Request, auth_token: str = "", limit: int = 100, upload_batch: str = ""
    ) -> JSONResponse:
        if not svc._is_authorised_request(auth_token, request):
            return JSONResponse({"error": svc._unauthorised_message(request)}, status_code=401)

        try:
            base_filter_expr = "corpus eq 'b'"
            filter_expr = base_filter_expr
            batch = upload_batch.strip()
            if batch:
                escaped = batch.replace("'", "''")
                filter_expr = f"{filter_expr} and upload_batch eq '{escaped}'"

            listing = svc._list_search_documents_by_filter(
                svc.search_client,
                filter_expr=filter_expr,
                select_fields=[
                    "id",
                    "source_name",
                    "source_path",
                    "corpus",
                    "corpus_role",
                    "upload_batch",
                    "uploaded_at",
                    "original_filename",
                ],
                limit=limit,
            )

            overall_total_count: int | None = None
            if batch:
                overall_total_count = svc._count_search_documents_total_by_filter(
                    svc.search_client,
                    filter_expr=base_filter_expr,
                )

            return JSONResponse(
                {
                    "mode": "corpus-b-list",
                    "upload_batch_filter": batch or None,
                    "overall_total_count": overall_total_count,
                    **listing,
                }
            )
        except Exception as exc:
            logger.exception("Failed /api/corpus-b/list request: %s", exc)
            return JSONResponse({"error": svc._INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.get("/api/corpus-c/list")
    def corpus_c_list(
        request: Request, auth_token: str = "", limit: int = 100, upload_batch: str = ""
    ) -> JSONResponse:
        if not svc._is_authorised_request(auth_token, request):
            return JSONResponse({"error": svc._unauthorised_message(request)}, status_code=401)

        try:
            base_filter_expr = "corpus eq 'c'"
            filter_expr = base_filter_expr
            batch = upload_batch.strip()
            if batch:
                escaped = batch.replace("'", "''")
                filter_expr = f"{filter_expr} and upload_batch eq '{escaped}'"

            listing = svc._list_search_documents_by_filter(
                svc.search_client,
                filter_expr=filter_expr,
                select_fields=[
                    "id",
                    "source_name",
                    "source_path",
                    "corpus",
                    "corpus_role",
                    "upload_batch",
                    "uploaded_at",
                    "original_filename",
                ],
                limit=limit,
            )

            overall_total_count = None
            if batch:
                overall_total_count = svc._count_search_documents_total_by_filter(
                    svc.search_client,
                    filter_expr=base_filter_expr,
                )

            return JSONResponse(
                {
                    "mode": "corpus-c-list",
                    "upload_batch_filter": batch or None,
                    "overall_total_count": overall_total_count,
                    **listing,
                }
            )
        except Exception as exc:
            logger.exception("Failed /api/corpus-c/list request: %s", exc)
            return JSONResponse({"error": svc._INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.get("/api/ingestion-job/latest")
    def get_latest_ingestion_job_status(request: Request, auth_token: str = "") -> JSONResponse:
        if not svc._is_authorised_request(auth_token, request):
            return JSONResponse({"error": svc._unauthorised_message(request)}, status_code=401)

        try:
            latest = svc._latest_ingestion_job_execution()
            return JSONResponse(
                {
                    "enabled": svc._is_ingestion_job_trigger_enabled(),
                    "resource_group": svc.config.ingestion_job_resource_group,
                    "job_name": svc.config.ingestion_job_name,
                    "latest": latest,
                }
            )
        except Exception as exc:
            logger.exception("Failed /api/ingestion-job/latest request: %s", exc)
            return JSONResponse({"error": svc._INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.get("/api/confluence/poll-status")
    def confluence_poll_status(
        request: Request,
        since_hours: int = 24,
        auth_token: str = "",
    ) -> JSONResponse:
        """Return the last Confluence poll status and assessed pages for the look-back window."""
        if not svc._is_authorised_request(auth_token, request):
            return JSONResponse({"error": svc._unauthorised_message(request)}, status_code=401)

        try:
            since_hours = max(1, min(since_hours, 720))
            if svc.confluence_poll_state_store is None:
                return JSONResponse(
                    {
                        "configured": False,
                        "message": (
                            "Confluence poll status store is unavailable because the orchestration "
                            "Cosmos container is not configured for this query-web instance."
                        ),
                        "since_hours": since_hours,
                        "last_poll": None,
                        "assessed_pages": [],
                    }
                )

            since_iso = (datetime.now(UTC) - timedelta(hours=since_hours)).isoformat()
            store_errors: list[str] = []

            latest_poll = None
            try:
                latest_poll = svc.confluence_poll_state_store.get_latest_poll_run_summary(
                    "confluence"
                )
            except Exception as exc:
                logger.exception("Failed reading latest Confluence poll summary: %s", exc)
                store_errors.append("latest poll summary unavailable")

            poll_state = None
            if latest_poll is None:
                try:
                    load_state = getattr(svc.confluence_poll_state_store, "load_state", None)
                    if callable(load_state):
                        poll_state = load_state("confluence")
                except Exception as exc:
                    logger.exception("Failed reading Confluence poll state fallback: %s", exc)

            assessed_pages: list[Any] = []
            try:
                assessed_pages = svc.confluence_poll_state_store.list_recent_page_assessments(
                    "confluence",
                    since_iso=since_iso,
                    limit=200,
                )
            except Exception as exc:
                logger.exception("Failed reading recent Confluence page assessments: %s", exc)
                store_errors.append("recent page assessments unavailable")

            recent_failures: list[Any] = []
            try:
                list_recent_failures = getattr(
                    svc.confluence_poll_state_store, "list_recent_failures", None
                )
                if callable(list_recent_failures):
                    raw_failures = list_recent_failures(
                        "confluence",
                        since_iso=since_iso,
                        limit=50,
                    )
                    recent_failures = list(raw_failures) if raw_failures is not None else []
            except Exception as exc:
                logger.exception("Failed reading recent Confluence poll failures: %s", exc)
                store_errors.append("recent poll failures unavailable")

            page_status_counts: dict[str, int] = {}
            risk_counts: dict[str, int] = {}
            for item in assessed_pages:
                page_status_counts[item.status] = page_status_counts.get(item.status, 0) + 1
                risk_label = svc._risk_label(item.overall_risk)
                risk_counts[risk_label] = risk_counts.get(risk_label, 0) + 1

            failure_status_counts: dict[str, int] = {}
            for item in recent_failures:
                failure_status_counts[item.status] = (
                    failure_status_counts.get(item.status, 0) + 1
                )

            return JSONResponse(
                {
                    "configured": (
                        latest_poll is not None
                        or poll_state is not None
                        or bool(assessed_pages)
                    ),
                    "message": (
                        "No Confluence poll cycle has written status yet."
                        if latest_poll is None and poll_state is None and not store_errors
                        else "; ".join(store_errors)
                        if store_errors
                        else ""
                    ),
                    "since_hours": since_hours,
                    "summary": {
                        "page_status_counts": page_status_counts,
                        "risk_counts": risk_counts,
                        "failure_status_counts": failure_status_counts,
                    },
                    "last_poll": (
                        None
                        if latest_poll is None and poll_state is None
                        else {
                            "polled_at": latest_poll.polled_at,
                            "space_key": (
                                ", ".join(latest_poll.space_keys)
                                if latest_poll.space_keys
                                else ""
                            ),
                            "mentions_found": latest_poll.mentions_found,
                            "jobs_queued": latest_poll.jobs_queued,
                            "terminal_failures": latest_poll.terminal_failures,
                            "error": latest_poll.error_message,
                            "watermark": latest_poll.watermark,
                            "since_iso": latest_poll.since_iso,
                        }
                        if latest_poll is not None
                        else {
                            "polled_at": getattr(poll_state, "last_success_at", ""),
                            "space_key": "",
                            "mentions_found": None,
                            "jobs_queued": None,
                            "terminal_failures": None,
                            "error": (
                                getattr(poll_state, "last_error", {}) or {}
                            ).get("error", ""),
                            "watermark": getattr(poll_state, "watermark", ""),
                            "since_iso": "",
                            "last_processed_event_id": getattr(
                                poll_state, "last_processed_event_id", ""
                            ),
                            "poll_count": getattr(poll_state, "poll_count", 0),
                        }
                    ),
                    "assessed_pages": [
                        {
                            "page_id": item.target_id,
                            "title": item.title,
                            "target_url": item.target_url,
                            "space_key": item.space_key,
                            "overall_risk": svc._risk_label(item.overall_risk),
                            "assessed_at": item.assessed_at,
                            "framework": item.framework_scope,
                            "findings_count": item.findings_count,
                            "status": item.status,
                            "page_version": item.page_version,
                        }
                        for item in assessed_pages
                    ],
                    "recent_failures": [
                        {
                            "event_id": item.event_id,
                            "status": item.status,
                            "attempt_count": item.attempt_count,
                            "last_error": item.last_error,
                            "last_attempt_at": item.last_attempt_at,
                            "run_id": item.run_id,
                        }
                        for item in recent_failures
                    ],
                }
            )
        except Exception as exc:
            logger.exception("Failed /api/confluence/poll-status request: %s", exc)
            return JSONResponse({"error": svc._INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.post("/api/corpus-a/ingest")
    def corpus_a_ingest(request: Request, payload: CorpusAIngestRequest) -> JSONResponse:
        if not svc._is_authorised_request(payload.auth_token, request):
            return JSONResponse({"error": svc._unauthorised_message(request)}, status_code=401)

        if not svc._is_ingestion_job_trigger_enabled():
            return JSONResponse(
                {
                    "error": (
                        "Ingestion job trigger is not configured. "
                        "Set INGESTION_JOB_SUBSCRIPTION_ID, INGESTION_JOB_RESOURCE_GROUP, and INGESTION_JOB_NAME."
                    )
                },
                status_code=500,
            )

        try:
            selected = svc._selected_corpus_a_frameworks(payload.frameworks)
            status = svc._controls_framework_ingestion_status()

            already_ingested = [fw for fw in selected if status.get(fw, {}).get("ingested")]
            pending = (
                selected
                if payload.replace_existing
                else [fw for fw in selected if fw not in already_ingested]
            )

            triggered: list[dict[str, Any]] = []
            skipped: list[dict[str, Any]] = []

            for fw in selected:
                if fw not in pending:
                    skipped.append(
                        {
                            "framework": fw,
                            "reason": "already_ingested",
                            "status": status.get(fw, {}),
                        }
                    )

            runnable_pending: list[str] = []
            source_upload_required: list[str] = []
            for fw in pending:
                if fw in svc._CORPUS_A_SOURCE_UPLOAD_REQUIRED_FRAMEWORKS:
                    source_upload_required.append(fw)
                    skipped.append(
                        {
                            "framework": fw,
                            "reason": "source_upload_required",
                            "message": (
                                "This framework requires source documents to be staged via "
                                "POST /api/corpus-a/upload before ingestion can run."
                            ),
                        }
                    )
                    continue
                runnable_pending.append(fw)

            for fw in runnable_pending:
                args_override = [
                    "--mode",
                    "controls",
                    "--controls-framework",
                    fw,
                ]
                if payload.replace_existing:
                    args_override.append("--replace-existing")
                if payload.dry_run:
                    args_override.append("--dry-run")
                if payload.no_guidance:
                    args_override.append("--no-guidance")

                job_result = svc._trigger_ingestion_job_with_args(args_override)
                triggered.append(
                    {
                        "framework": fw,
                        "job": job_result,
                    }
                )

            return JSONResponse(
                {
                    "mode": "corpus-a-ingest",
                    "selected_frameworks": selected,
                    "already_ingested_frameworks": already_ingested,
                    "source_upload_required_frameworks": source_upload_required,
                    "replace_existing": payload.replace_existing,
                    "dry_run": payload.dry_run,
                    "no_guidance": payload.no_guidance,
                    "triggered": triggered,
                    "skipped": skipped,
                    "framework_status": status,
                }
            )
        except Exception as exc:
            logger.exception("Failed /api/corpus-a/ingest request: %s", exc)
            return JSONResponse({"error": svc._INTERNAL_ERROR_MESSAGE}, status_code=500)
