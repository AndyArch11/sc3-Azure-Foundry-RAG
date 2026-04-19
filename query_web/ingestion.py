"""Ingestion and corpus upload helpers."""
from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests  # type: ignore[import-untyped]
from azure.core.exceptions import HttpResponseError
from azure.search.documents.indexes import SearchIndexerClient
from azure.storage.blob import BlobServiceClient, ContentSettings
from fastapi import UploadFile

logger = logging.getLogger(__name__)


REQUIRED_INGESTION_METADATA_KEYS = {
    "corpus",
    "corpus_role",
    "upload_source",
    "uploaded_by",
    "upload_batch",
    "uploaded_at",
    "original_filename",
    "dedupe_hash",
    "dedupe_method",
}


class IngestionService:
    """Encapsulate ingestion-job and corpus upload helpers.

    The service receives the app module as a dependency container so helper
    lookups happen at runtime and existing patch.object(app_module, ...) tests
    keep working through the thin wrappers left in app.py.
    """

    def __init__(self, svc: Any) -> None:
        self.svc = svc

    def _svc_attr(self, name: str, default: Any) -> Any:
        return getattr(self.svc, name, default)

    def is_corpus_upload_enabled(self) -> bool:
        return bool(self.svc.config.storage_account_name)

    def is_ingestion_job_trigger_enabled(self) -> bool:
        return bool(
            self.svc.config.ingestion_job_subscription_id
            and self.svc.config.ingestion_job_resource_group
            and self.svc.config.ingestion_job_name
        )

    def trigger_ingestion_job(self) -> dict[str, Any]:
        return self.trigger_ingestion_job_with_args(None)

    def is_indexer_running(self, status: Any) -> bool:
        try:
            last_result = getattr(status, "last_result", None)
            if last_result is not None:
                run_status = str(getattr(last_result, "status", "")).strip().lower()
                if run_status == "inprogress":
                    return True
        except Exception:
            pass
        return False

    def wait_for_indexer_idle(self, indexer_name: str, timeout_seconds: int = 900) -> bool:
        search_indexer_client_cls = self._svc_attr("SearchIndexerClient", SearchIndexerClient)
        client = search_indexer_client_cls(
            endpoint=self.svc.config.search_endpoint,
            credential=self.svc.credential,
        )
        deadline = time.monotonic() + timeout_seconds

        while time.monotonic() < deadline:
            try:
                status = client.get_indexer_status(indexer_name)
            except Exception:
                time.sleep(5)
                continue

            if not self.is_indexer_running(status):
                return True
            time.sleep(5)

        return False

    def reset_grounding_indexer_state(self) -> str:
        indexer_name = os.getenv(
            "AZURE_SEARCH_INDEXER_NAME",
            f"{self.svc.config.search_index_name}-indexer",
        ).strip()
        if not indexer_name:
            raise RuntimeError("AZURE_SEARCH_INDEXER_NAME is empty.")

        search_indexer_client_cls = self._svc_attr("SearchIndexerClient", SearchIndexerClient)
        client = search_indexer_client_cls(
            endpoint=self.svc.config.search_endpoint,
            credential=self.svc.credential,
        )
        try:
            client.reset_indexer(indexer_name)
        except HttpResponseError as exc:
            if exc.status_code != 409:
                raise

            logger.warning(
                "Indexer %s reset blocked by active run (409); waiting for idle before retry",
                indexer_name,
            )
            if not self.wait_for_indexer_idle(indexer_name):
                raise RuntimeError(
                    f"Timed out waiting for indexer '{indexer_name}' to become idle for reset."
                ) from exc

            client.reset_indexer(indexer_name)
            logger.info(
                "Indexer %s reset succeeded after waiting for active run to finish",
                indexer_name,
            )
        return indexer_name

    def get_ingestion_job_template_container(self, token: str) -> dict[str, Any]:
        requests_module = self._svc_attr("requests", requests)
        get_url = (
            f"https://management.azure.com/subscriptions/{self.svc.config.ingestion_job_subscription_id}"
            f"/resourceGroups/{self.svc.config.ingestion_job_resource_group}"
            f"/providers/Microsoft.App/jobs/{self.svc.config.ingestion_job_name}"
            "?api-version=2024-03-01"
        )
        resp = requests_module.get(
            get_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Failed to fetch ingestion job definition: {resp.status_code} {resp.text}"
            )
        containers = resp.json().get("properties", {}).get("template", {}).get("containers", [])
        if not containers:
            raise RuntimeError("Ingestion job definition contains no containers.")
        return dict(containers[0])

    def trigger_ingestion_job_with_args(
        self, args_override: list[str] | None
    ) -> dict[str, Any]:
        if not self.is_ingestion_job_trigger_enabled():
            raise RuntimeError(
                "Ingestion job trigger is not configured. "
                "Set INGESTION_JOB_SUBSCRIPTION_ID, INGESTION_JOB_RESOURCE_GROUP, and INGESTION_JOB_NAME."
            )

        token = self.svc.credential.get_token("https://management.azure.com/.default").token
        requests_module = self._svc_attr("requests", requests)
        url = (
            f"https://management.azure.com/subscriptions/{self.svc.config.ingestion_job_subscription_id}"
            f"/resourceGroups/{self.svc.config.ingestion_job_resource_group}"
            f"/providers/Microsoft.App/jobs/{self.svc.config.ingestion_job_name}/start"
            "?api-version=2024-03-01"
        )

        if args_override:
            container = self.get_ingestion_job_template_container(token)
            container["args"] = args_override
            body: dict[str, Any] = {"containers": [container]}
        else:
            body = {}

        response = requests_module.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Failed to start ingestion job: {response.status_code} {response.text}"
            )

        execution_name: str | None = None
        try:
            payload = response.json()
            if isinstance(payload, dict):
                execution_name = str(payload.get("name") or "").strip() or None
        except Exception:
            execution_name = None

        location_header = str(response.headers.get("Location") or "").strip()
        if not execution_name and "/executions/" in location_header:
            execution_name = location_header.rsplit("/executions/", 1)[-1].split("?", 1)[0] or None

        return {
            "status_code": response.status_code,
            "resource_group": self.svc.config.ingestion_job_resource_group,
            "job_name": self.svc.config.ingestion_job_name,
            "execution_name": execution_name,
            "args_override": args_override or [],
        }

    def blob_has_required_ingestion_metadata(
        self, metadata: dict[str, str] | None
    ) -> bool:
        if not metadata:
            return False
        for key in REQUIRED_INGESTION_METADATA_KEYS:
            if not str(metadata.get(key) or "").strip():
                return False
        return True

    def mark_dedupe_blobs_for_reindex(
        self, corpus: str, dedupe_hashes: list[str], *, user_id: str
    ) -> dict[str, Any]:
        if not dedupe_hashes:
            return {"requested": 0, "touched": 0, "not_found": [], "failed": []}

        account_url = f"https://{self.svc.config.storage_account_name}.blob.core.windows.net"
        blob_service_client_cls = self._svc_attr("BlobServiceClient", BlobServiceClient)
        client = blob_service_client_cls(account_url=account_url, credential=self.svc.credential)
        container = client.get_container_client(self.svc.config.storage_container_name)

        touched = 0
        not_found: list[str] = []
        failed: list[str] = []

        for dedupe_hash in dedupe_hashes:
            dedupe_prefix = self.svc._dedupe_blob_prefix(corpus, dedupe_hash)
            matching_blob_names = [
                blob.name for blob in container.list_blobs(name_starts_with=dedupe_prefix)
            ]
            if not matching_blob_names:
                not_found.append(f"{dedupe_prefix}*")
                continue

            for blob_name in matching_blob_names:
                blob = container.get_blob_client(blob_name)
                try:
                    props = blob.get_blob_properties()
                    metadata = dict(props.metadata or {})
                    metadata["reindex_requested_at"] = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                    metadata["reindex_requested_by"] = self.svc._sanitise_blob_name_component(
                        user_id or "anonymous"
                    )
                    blob.set_blob_metadata(metadata=metadata)
                    touched += 1
                except Exception as exc:
                    failed.append(f"{blob_name}: {exc}")

        return {
            "requested": len(dedupe_hashes),
            "touched": touched,
            "not_found": not_found,
            "failed": failed,
        }

    def latest_ingestion_job_execution(self) -> dict[str, Any] | None:
        if not self.is_ingestion_job_trigger_enabled():
            return None

        token = self.svc.credential.get_token("https://management.azure.com/.default").token
        url = (
            f"https://management.azure.com/subscriptions/{self.svc.config.ingestion_job_subscription_id}"
            f"/resourceGroups/{self.svc.config.ingestion_job_resource_group}"
            f"/providers/Microsoft.App/jobs/{self.svc.config.ingestion_job_name}/executions"
            "?api-version=2024-03-01"
        )
        requests_module = self._svc_attr("requests", requests)
        response = requests_module.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Failed to list ingestion job executions: {response.status_code} {response.text}"
            )

        values = response.json().get("value", [])
        if not values:
            return None

        def _sort_key(item: dict[str, Any]) -> str:
            props = item.get("properties", {})
            return str(props.get("startTime") or "")

        latest = max(values, key=_sort_key)
        props = latest.get("properties", {})
        return {
            "name": latest.get("name"),
            "status": props.get("status"),
            "start_time": props.get("startTime"),
            "end_time": props.get("endTime"),
        }

    def upload_corpus_files(
        self,
        files: list[UploadFile],
        user_id: str,
        *,
        corpus: str,
        corpus_role: str,
    ) -> dict[str, Any]:
        if not self.is_corpus_upload_enabled():
            raise RuntimeError(
                "Corpus upload is not configured. Set AZURE_STORAGE_ACCOUNT_NAME in query web configuration."
            )

        account_url = f"https://{self.svc.config.storage_account_name}.blob.core.windows.net"
        blob_service_client_cls = self._svc_attr("BlobServiceClient", BlobServiceClient)
        content_settings_cls = self._svc_attr("ContentSettings", ContentSettings)
        client = blob_service_client_cls(account_url=account_url, credential=self.svc.credential)
        container = client.get_container_client(self.svc.config.storage_container_name)

        uploaded: list[dict[str, Any]] = []
        skipped: list[str] = []
        failed: list[str] = []

        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        upload_batch_id: str | None = None

        for file in files:
            original_name = file.filename or "uploaded.bin"
            ext = Path(original_name).suffix.lower()
            if ext not in self.svc.ALLOWED_EXTENSIONS:
                skipped.append(f"{original_name}: disallowed filetype {ext}")
                try:
                    file.file.close()
                except Exception:
                    pass
                continue

            try:
                content = file.file.read()
                if not content:
                    skipped.append(original_name)
                    continue

                content_sha256 = hashlib.sha256(content).hexdigest()
                normalised_text_sha256, hash_method = self.svc._compute_normalised_text_hash(
                    content,
                    filename=original_name,
                    content_type=file.content_type or "",
                )
                dedupe_hash = normalised_text_sha256 or content_sha256
                dedupe_method = (
                    "normalised_text_sha256" if normalised_text_sha256 else "content_sha256"
                )
                hash_blob_prefix = self.svc._dedupe_blob_prefix(corpus, dedupe_hash)
                hash_blob_name = f"{hash_blob_prefix}{ext}"
                existing_blob_names = [
                    blob.name for blob in container.list_blobs(name_starts_with=hash_blob_prefix)
                ]

                if upload_batch_id is None:
                    upload_batch_id = str(uuid.uuid4())

                metadata = {
                    "corpus": corpus,
                    "corpus_role": corpus_role,
                    "upload_source": "query_web",
                    "uploaded_by": self.svc._sanitise_blob_name_component(user_id or "anonymous"),
                    "upload_batch": upload_batch_id,
                    "uploaded_at": ts,
                    "original_filename": self.svc._sanitise_blob_name_component(original_name),
                    "content_sha256": content_sha256,
                    "normalised_text_sha256": normalised_text_sha256 or "",
                    "dedupe_hash": dedupe_hash,
                    "dedupe_method": dedupe_method,
                    "hash_method": hash_method,
                }

                should_repair_existing = False
                for existing_blob_name in existing_blob_names:
                    existing_blob = container.get_blob_client(existing_blob_name)
                    try:
                        existing_props = existing_blob.get_blob_properties()
                        existing_metadata = dict(existing_props.metadata or {})
                    except Exception:
                        existing_metadata = {}
                    existing_ext = Path(existing_blob_name).suffix.lower()
                    metadata_ok = self.blob_has_required_ingestion_metadata(existing_metadata)
                    if not metadata_ok or existing_ext != ext:
                        should_repair_existing = True
                        break

                if existing_blob_names and not should_repair_existing:
                    skipped.append(f"{original_name}: duplicate-{dedupe_method}:{dedupe_hash}")
                    continue

                container.upload_blob(
                    name=hash_blob_name,
                    data=content,
                    overwrite=True,
                    metadata=metadata,
                    content_settings=content_settings_cls(
                        content_type=file.content_type or "application/octet-stream"
                    ),
                )

                if should_repair_existing:
                    for existing_blob_name in existing_blob_names:
                        if existing_blob_name == hash_blob_name:
                            continue
                        try:
                            container.delete_blob(existing_blob_name)
                        except Exception as exc:
                            logger.warning(
                                "Failed to delete stale dedupe blob %s during repair: %s",
                                existing_blob_name,
                                exc,
                            )

                uploaded.append(
                    {
                        "blob_name": hash_blob_name,
                        "size_bytes": len(content),
                        "content_type": file.content_type or "application/octet-stream",
                        "content_sha256": content_sha256,
                        "normalised_text_sha256": normalised_text_sha256,
                        "dedupe_hash": dedupe_hash,
                        "dedupe_method": dedupe_method,
                        "repaired_existing": should_repair_existing,
                        "metadata": metadata,
                    }
                )
            except Exception as exc:
                logger.warning("Failed to upload file %s: %s", original_name, exc, exc_info=True)
                failed.append(f"{original_name}: upload failed")
            finally:
                try:
                    file.file.close()
                except Exception:
                    pass

        return {
            "upload_batch_id": upload_batch_id,
            "prefix": f"corpus-{corpus}/by-dedupe",
            "uploaded": uploaded,
            "skipped": skipped,
            "failed": failed,
        }

    def upload_corpus_b_files(self, files: list[UploadFile], user_id: str) -> dict[str, Any]:
        return self.upload_corpus_files(
            files,
            user_id,
            corpus="b",
            corpus_role="narrative_guidance",
        )

    def upload_corpus_c_files(self, files: list[UploadFile], user_id: str) -> dict[str, Any]:
        return self.upload_corpus_files(
            files,
            user_id,
            corpus="c",
            corpus_role="assessed_artifact",
        )

    def upload_corpus_a_reference_files(
        self,
        files: list[UploadFile],
        user_id: str,
        *,
        framework: str,
    ) -> dict[str, Any]:
        if not self.is_corpus_upload_enabled():
            raise RuntimeError(
                "Corpus upload is not configured. Set AZURE_STORAGE_ACCOUNT_NAME in query web configuration."
            )

        framework_key, prepared_uploads = self.svc._prepare_corpus_a_reference_uploads(framework, files)

        account_url = f"https://{self.svc.config.storage_account_name}.blob.core.windows.net"
        blob_service_client_cls = self._svc_attr("BlobServiceClient", BlobServiceClient)
        content_settings_cls = self._svc_attr("ContentSettings", ContentSettings)
        client = blob_service_client_cls(account_url=account_url, credential=self.svc.credential)
        container = client.get_container_client(self.svc.config.storage_container_name)

        uploaded: list[dict[str, Any]] = []
        failed: list[str] = []

        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        upload_batch_id = str(uuid.uuid4())
        source_prefix = f"corpus-a/source/{framework_key}/{upload_batch_id}"

        for file, original_name, target_name in prepared_uploads:
            try:
                content = file.file.read()
                if not content:
                    raise ValueError(f"{original_name} is empty")

                blob_name = f"{source_prefix}/{target_name}"
                metadata = {
                    "corpus": "a",
                    "framework": framework_key,
                    "upload_source": "query_web",
                    "uploaded_by": self.svc._sanitise_blob_name_component(user_id or "anonymous"),
                    "upload_batch": upload_batch_id,
                    "uploaded_at": ts,
                    "original_filename": self.svc._sanitise_blob_name_component(original_name),
                    "target_filename": target_name,
                }
                container.upload_blob(
                    name=blob_name,
                    data=content,
                    overwrite=True,
                    metadata=metadata,
                    content_settings=content_settings_cls(
                        content_type=file.content_type or "application/octet-stream"
                    ),
                )
                uploaded.append(
                    {
                        "blob_name": blob_name,
                        "size_bytes": len(content),
                        "content_type": file.content_type or "application/octet-stream",
                        "original_filename": original_name,
                        "target_filename": target_name,
                        "metadata": metadata,
                    }
                )
            except Exception as exc:
                logger.warning("Failed to upload file %s: %s", original_name, exc, exc_info=True)
                failed.append(f"{original_name}: upload failed")
            finally:
                try:
                    file.file.close()
                except Exception:
                    pass

        return {
            "framework": framework_key,
            "framework_name": self.svc._CORPUS_A_FRAMEWORKS[framework_key],
            "upload_batch_id": upload_batch_id,
            "source_prefix": source_prefix,
            "uploaded": uploaded,
            "failed": failed,
        }