"""Unit tests for query_web/ingestion.py."""

from __future__ import annotations

import io
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

os.environ.setdefault("AZURE_SEARCH_ENDPOINT", "https://test.search.windows.net")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
os.environ.setdefault("AZURE_COSMOS_ENDPOINT", "https://test.documents.azure.com")
os.environ.setdefault("AZURE_COSMOS_DATABASE_NAME", "rag-conversations")
os.environ.setdefault("AZURE_COSMOS_CONTAINER_NAME", "conversations")

from query_web.endpoints.ingestion import REQUIRED_INGESTION_METADATA_KEYS, IngestionService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_svc(
    *,
    storage_account: str = "",
    sub_id: str = "",
    rg: str = "",
    job_name: str = "",
    search_endpoint: str = "https://test.search.windows.net",
    storage_container: str = "test-container",
    index_name: str = "test-index",
    credential: Any | None = None,
    allowed_extensions: frozenset | None = None,
) -> SimpleNamespace:
    config = SimpleNamespace(
        storage_account_name=storage_account,
        ingestion_job_subscription_id=sub_id,
        ingestion_job_resource_group=rg,
        ingestion_job_name=job_name,
        search_endpoint=search_endpoint,
        search_index_name=index_name,
        storage_container_name=storage_container,
    )
    mock_credential = credential or Mock()
    mock_token = Mock()
    mock_token.token = "fake-token"
    mock_credential.get_token = Mock(return_value=mock_token)

    svc = SimpleNamespace(
        config=config,
        credential=mock_credential,
        ALLOWED_EXTENSIONS=allowed_extensions or frozenset({".pdf", ".txt", ".docx"}),
    )
    svc._dedupe_blob_prefix = lambda corpus, h: f"corpus-{corpus}/by-dedupe/{h}"
    svc._sanitise_blob_name_component = lambda v: v.replace(" ", "_")
    svc._compute_normalised_text_hash = lambda content, filename, content_type: (
        None,
        "content_sha256",
    )
    return svc


def _make_upload_file(name: str, content: bytes = b"hello") -> Mock:
    f = Mock()
    f.filename = name
    f.content_type = "application/pdf"
    f.file = io.BytesIO(content)
    return f


# ---------------------------------------------------------------------------
# REQUIRED_INGESTION_METADATA_KEYS
# ---------------------------------------------------------------------------


def test_required_metadata_keys_complete() -> None:
    assert "corpus" in REQUIRED_INGESTION_METADATA_KEYS
    assert "upload_batch" in REQUIRED_INGESTION_METADATA_KEYS
    assert "dedupe_hash" in REQUIRED_INGESTION_METADATA_KEYS
    assert "uploaded_by" in REQUIRED_INGESTION_METADATA_KEYS


# ---------------------------------------------------------------------------
# is_corpus_upload_enabled
# ---------------------------------------------------------------------------


def test_is_corpus_upload_enabled_true_when_account_set() -> None:
    svc = _make_svc(storage_account="myaccount")
    service = IngestionService(svc)
    assert service.is_corpus_upload_enabled() is True


def test_is_corpus_upload_enabled_false_when_no_account() -> None:
    svc = _make_svc(storage_account="")
    service = IngestionService(svc)
    assert service.is_corpus_upload_enabled() is False


# ---------------------------------------------------------------------------
# is_ingestion_job_trigger_enabled
# ---------------------------------------------------------------------------


def test_is_ingestion_job_trigger_enabled_all_set() -> None:
    svc = _make_svc(sub_id="sub-123", rg="rg-prod", job_name="ingest-job")
    service = IngestionService(svc)
    assert service.is_ingestion_job_trigger_enabled() is True


def test_is_ingestion_job_trigger_enabled_missing_one_field() -> None:
    svc = _make_svc(sub_id="sub-123", rg="rg-prod", job_name="")
    service = IngestionService(svc)
    assert service.is_ingestion_job_trigger_enabled() is False


def test_is_ingestion_job_trigger_enabled_all_empty() -> None:
    svc = _make_svc()
    service = IngestionService(svc)
    assert service.is_ingestion_job_trigger_enabled() is False


# ---------------------------------------------------------------------------
# blob_has_required_ingestion_metadata
# ---------------------------------------------------------------------------


def test_blob_has_required_metadata_true() -> None:
    metadata = {key: "value" for key in REQUIRED_INGESTION_METADATA_KEYS}
    svc = _make_svc()
    service = IngestionService(svc)
    assert service.blob_has_required_ingestion_metadata(metadata) is True


def test_blob_has_required_metadata_false_missing_key() -> None:
    metadata = {key: "value" for key in REQUIRED_INGESTION_METADATA_KEYS}
    del metadata["corpus"]
    svc = _make_svc()
    service = IngestionService(svc)
    assert service.blob_has_required_ingestion_metadata(metadata) is False


def test_blob_has_required_metadata_false_empty_value() -> None:
    metadata = {key: "value" for key in REQUIRED_INGESTION_METADATA_KEYS}
    metadata["corpus"] = ""
    svc = _make_svc()
    service = IngestionService(svc)
    assert service.blob_has_required_ingestion_metadata(metadata) is False


def test_blob_has_required_metadata_false_none_metadata() -> None:
    svc = _make_svc()
    service = IngestionService(svc)
    assert service.blob_has_required_ingestion_metadata(None) is False


def test_blob_has_required_metadata_false_empty_dict() -> None:
    svc = _make_svc()
    service = IngestionService(svc)
    assert service.blob_has_required_ingestion_metadata({}) is False


# ---------------------------------------------------------------------------
# is_indexer_running
# ---------------------------------------------------------------------------


def test_is_indexer_running_inprogress() -> None:
    svc = _make_svc()
    service = IngestionService(svc)
    last_result = SimpleNamespace(status="inProgress")
    status = SimpleNamespace(last_result=last_result)
    assert service.is_indexer_running(status) is True


def test_is_indexer_running_succeeded() -> None:
    svc = _make_svc()
    service = IngestionService(svc)
    last_result = SimpleNamespace(status="success")
    status = SimpleNamespace(last_result=last_result)
    assert service.is_indexer_running(status) is False


def test_is_indexer_running_no_last_result() -> None:
    svc = _make_svc()
    service = IngestionService(svc)
    status = SimpleNamespace(last_result=None)
    assert service.is_indexer_running(status) is False


def test_is_indexer_running_exception_returns_false() -> None:
    svc = _make_svc()
    service = IngestionService(svc)

    # An object that raises when accessed
    class _Bad:
        @property
        def last_result(self):
            raise RuntimeError("oops")

    assert service.is_indexer_running(_Bad()) is False


# ---------------------------------------------------------------------------
# trigger_ingestion_job_with_args — not configured
# ---------------------------------------------------------------------------


def test_trigger_ingestion_job_raises_when_not_configured() -> None:
    svc = _make_svc()
    service = IngestionService(svc)
    with pytest.raises(RuntimeError, match="not configured"):
        service.trigger_ingestion_job_with_args(None)


def test_trigger_ingestion_job_raises_when_not_configured_with_args() -> None:
    svc = _make_svc()
    service = IngestionService(svc)
    with pytest.raises(RuntimeError):
        service.trigger_ingestion_job_with_args(["--mode", "azure"])


# ---------------------------------------------------------------------------
# trigger_ingestion_job_with_args — configured + mocked HTTP
# ---------------------------------------------------------------------------


def _configured_svc() -> SimpleNamespace:
    return _make_svc(sub_id="sub-123", rg="rg-prod", job_name="ingest-job")


def test_trigger_ingestion_job_with_args_none_sends_empty_body() -> None:
    svc = _configured_svc()
    service = IngestionService(svc)

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"name": "exec-1"}
    mock_response.headers = {}

    mock_requests = Mock()
    mock_requests.post.return_value = mock_response

    svc.requests = mock_requests
    result = service.trigger_ingestion_job_with_args(None)

    call_kwargs = mock_requests.post.call_args
    assert call_kwargs.kwargs["json"] == {}
    assert result["execution_name"] == "exec-1"
    assert result["args_override"] == []


def test_trigger_ingestion_job_with_args_override_fetches_container() -> None:
    svc = _configured_svc()
    service = IngestionService(svc)

    get_response = Mock()
    get_response.status_code = 200
    get_response.json.return_value = {
        "properties": {
            "template": {"containers": [{"image": "myimage", "name": "ingest", "args": []}]}
        }
    }

    post_response = Mock()
    post_response.status_code = 202
    post_response.json.return_value = {}
    post_response.headers = {"Location": "https://example.com/executions/exec-abc"}

    mock_requests = Mock()
    mock_requests.get.return_value = get_response
    mock_requests.post.return_value = post_response

    svc.requests = mock_requests
    result = service.trigger_ingestion_job_with_args(["--mode", "azure"])

    # Verify container args were overridden
    post_call = mock_requests.post.call_args
    body = post_call.kwargs["json"]
    assert body["containers"][0]["args"] == ["--mode", "azure"]
    assert result["execution_name"] == "exec-abc"
    assert result["args_override"] == ["--mode", "azure"]


def test_trigger_ingestion_job_raises_on_http_error() -> None:
    svc = _configured_svc()
    service = IngestionService(svc)

    mock_response = Mock()
    mock_response.status_code = 403
    mock_response.text = "Forbidden"

    mock_requests = Mock()
    mock_requests.post.return_value = mock_response
    svc.requests = mock_requests

    with pytest.raises(RuntimeError, match="Failed to start ingestion job"):
        service.trigger_ingestion_job_with_args(None)


# ---------------------------------------------------------------------------
# trigger_ingestion_job (convenience wrapper)
# ---------------------------------------------------------------------------


def test_trigger_ingestion_job_delegates_to_with_args_none() -> None:
    svc = _configured_svc()
    service = IngestionService(svc)

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"name": "exec-x"}
    mock_response.headers = {}
    mock_requests = Mock()
    mock_requests.post.return_value = mock_response
    svc.requests = mock_requests

    result = service.trigger_ingestion_job()
    assert result["args_override"] == []


# ---------------------------------------------------------------------------
# latest_ingestion_job_execution
# ---------------------------------------------------------------------------


def test_latest_ingestion_job_execution_returns_none_when_not_configured() -> None:
    svc = _make_svc()
    service = IngestionService(svc)
    assert service.latest_ingestion_job_execution() is None


def test_latest_ingestion_job_execution_returns_none_for_empty_list() -> None:
    svc = _configured_svc()
    service = IngestionService(svc)

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"value": []}
    mock_requests = Mock()
    mock_requests.get.return_value = mock_response
    svc.requests = mock_requests

    assert service.latest_ingestion_job_execution() is None


def test_latest_ingestion_job_execution_returns_most_recent() -> None:
    svc = _configured_svc()
    service = IngestionService(svc)

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "value": [
            {
                "name": "exec-older",
                "properties": {
                    "status": "Succeeded",
                    "startTime": "2024-01-01T00:00:00Z",
                    "endTime": "2024-01-01T00:05:00Z",
                },
            },
            {
                "name": "exec-newer",
                "properties": {
                    "status": "Running",
                    "startTime": "2024-01-02T00:00:00Z",
                    "endTime": None,
                },
            },
        ]
    }
    mock_requests = Mock()
    mock_requests.get.return_value = mock_response
    svc.requests = mock_requests

    result = service.latest_ingestion_job_execution()
    assert result is not None
    assert result["name"] == "exec-newer"
    assert result["status"] == "Running"


def test_latest_ingestion_job_execution_raises_on_http_error() -> None:
    svc = _configured_svc()
    service = IngestionService(svc)

    mock_response = Mock()
    mock_response.status_code = 500
    mock_response.text = "Server error"
    mock_requests = Mock()
    mock_requests.get.return_value = mock_response
    svc.requests = mock_requests

    with pytest.raises(RuntimeError, match="Failed to list"):
        service.latest_ingestion_job_execution()


# ---------------------------------------------------------------------------
# mark_dedupe_blobs_for_reindex
# ---------------------------------------------------------------------------


def test_mark_dedupe_blobs_for_reindex_empty_hashes_returns_early() -> None:
    svc = _make_svc(storage_account="myaccount")
    service = IngestionService(svc)
    result = service.mark_dedupe_blobs_for_reindex("b", [], user_id="user1")
    assert result == {"requested": 0, "touched": 0, "not_found": [], "failed": []}


def test_mark_dedupe_blobs_for_reindex_touches_matching_blobs() -> None:
    svc = _make_svc(storage_account="myaccount")
    service = IngestionService(svc)

    # Mock BlobServiceClient
    mock_blob = Mock()
    mock_blob.get_blob_properties.return_value = Mock(metadata={"some": "key"})
    mock_blob.set_blob_metadata = Mock()

    mock_container = Mock()
    mock_container.list_blobs.return_value = [SimpleNamespace(name="corpus-b/by-dedupe/abc123.pdf")]
    mock_container.get_blob_client.return_value = mock_blob

    mock_client = Mock()
    mock_client.get_container_client.return_value = mock_container

    mock_bsc = Mock(return_value=mock_client)
    svc.BlobServiceClient = mock_bsc

    result = service.mark_dedupe_blobs_for_reindex("b", ["abc123"], user_id="user1")
    assert result["requested"] == 1
    assert result["touched"] == 1
    assert result["not_found"] == []
    assert result["failed"] == []
    mock_blob.set_blob_metadata.assert_called_once()


def test_mark_dedupe_blobs_for_reindex_records_not_found() -> None:
    svc = _make_svc(storage_account="myaccount")
    service = IngestionService(svc)

    mock_container = Mock()
    mock_container.list_blobs.return_value = []

    mock_client = Mock()
    mock_client.get_container_client.return_value = mock_container
    mock_bsc = Mock(return_value=mock_client)
    svc.BlobServiceClient = mock_bsc

    result = service.mark_dedupe_blobs_for_reindex("b", ["nonexistent_hash"], user_id="user1")
    assert result["touched"] == 0
    assert len(result["not_found"]) == 1


# ---------------------------------------------------------------------------
# upload_corpus_b_files / upload_corpus_c_files
# ---------------------------------------------------------------------------


def test_upload_corpus_files_raises_when_storage_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _make_svc(storage_account="")
    service = IngestionService(svc)
    monkeypatch.delenv("CLOUD_PROVIDER", raising=False)
    with pytest.raises(RuntimeError, match="not configured"):
        service.upload_corpus_files([], "user1", corpus="b", corpus_role="narrative_guidance")


def test_upload_corpus_b_files_delegates_with_correct_corpus() -> None:
    svc = _make_svc(storage_account="myaccount")
    service = IngestionService(svc)

    mock_container = Mock()
    mock_container.list_blobs.return_value = []
    mock_container.upload_blob = Mock()

    mock_client = Mock()
    mock_client.get_container_client.return_value = mock_container
    mock_bsc = Mock(return_value=mock_client)
    mock_cs = Mock()
    svc.BlobServiceClient = mock_bsc
    svc.ContentSettings = mock_cs

    file = _make_upload_file("test.pdf", b"pdf content")
    result = service.upload_corpus_b_files([file], "user1")

    assert result["prefix"].startswith("corpus-b")
    assert len(result["uploaded"]) == 1
    assert result["uploaded"][0]["metadata"]["corpus"] == "b"


def test_upload_corpus_c_files_sets_correct_corpus_role() -> None:
    svc = _make_svc(storage_account="myaccount")
    service = IngestionService(svc)

    mock_container = Mock()
    mock_container.list_blobs.return_value = []
    mock_container.upload_blob = Mock()

    mock_client = Mock()
    mock_client.get_container_client.return_value = mock_container
    mock_bsc = Mock(return_value=mock_client)
    mock_cs = Mock()
    svc.BlobServiceClient = mock_bsc
    svc.ContentSettings = mock_cs

    file = _make_upload_file("test.txt", b"content")
    result = service.upload_corpus_c_files([file], "user1")

    assert result["uploaded"][0]["metadata"]["corpus_role"] == "assessed_artifact"


def test_upload_corpus_files_skips_disallowed_extension() -> None:
    svc = _make_svc(storage_account="myaccount", allowed_extensions=frozenset({".pdf"}))
    service = IngestionService(svc)

    mock_container = Mock()
    mock_container.list_blobs.return_value = []

    mock_client = Mock()
    mock_client.get_container_client.return_value = mock_container
    mock_bsc = Mock(return_value=mock_client)
    svc.BlobServiceClient = mock_bsc
    svc.ContentSettings = Mock()

    file = _make_upload_file("malware.exe", b"evil")
    result = service.upload_corpus_files(
        [file], "user1", corpus="b", corpus_role="narrative_guidance"
    )
    assert len(result["uploaded"]) == 0
    assert len(result["skipped"]) == 1


def test_upload_corpus_files_skips_duplicate_blob() -> None:
    svc = _make_svc(storage_account="myaccount")
    service = IngestionService(svc)

    existing_blob = Mock()
    existing_props = Mock()
    existing_props.metadata = {key: "value" for key in REQUIRED_INGESTION_METADATA_KEYS}
    existing_blob.get_blob_properties.return_value = existing_props

    mock_container = Mock()
    mock_container.list_blobs.return_value = [SimpleNamespace(name="corpus-b/by-dedupe/abc.pdf")]
    mock_container.get_blob_client.return_value = existing_blob

    mock_client = Mock()
    mock_client.get_container_client.return_value = mock_container
    svc.BlobServiceClient = Mock(return_value=mock_client)
    svc.ContentSettings = Mock()

    file = _make_upload_file("doc.pdf", b"pdf content")
    result = service.upload_corpus_files(
        [file], "user1", corpus="b", corpus_role="narrative_guidance"
    )
    assert len(result["uploaded"]) == 0
    assert len(result["skipped"]) == 1


def test_upload_corpus_files_empty_file_skipped() -> None:
    svc = _make_svc(storage_account="myaccount")
    service = IngestionService(svc)

    mock_container = Mock()
    mock_container.list_blobs.return_value = []

    mock_client = Mock()
    mock_client.get_container_client.return_value = mock_container
    svc.BlobServiceClient = Mock(return_value=mock_client)
    svc.ContentSettings = Mock()

    file = _make_upload_file("empty.pdf", b"")
    result = service.upload_corpus_files(
        [file], "user1", corpus="b", corpus_role="narrative_guidance"
    )
    assert len(result["uploaded"]) == 0
    assert len(result["skipped"]) == 1


def test_upload_corpus_files_local_mode_indexes_without_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _make_svc(storage_account="")
    svc.search_client = SimpleNamespace(_docs=[], load_documents=Mock())
    service = IngestionService(svc)
    monkeypatch.setenv("CLOUD_PROVIDER", "local")

    file = _make_upload_file("local.pdf", b"local content")
    fake_doc = SimpleNamespace(source_path="local.pdf", source_type="pdf", text="local content")
    fake_chunk = SimpleNamespace(
        chunk_id="chunk-1",
        source_path="local.pdf",
        source_type="pdf",
        chunk_index=0,
        content="local content",
    )

    with (
        patch("runtime.ingestion.extractors.extract_source_document", return_value=fake_doc),
        patch("runtime.ingestion.chunking.chunk_document", return_value=[fake_chunk]),
    ):
        result = service.upload_corpus_files(
            [file], "user1", corpus="b", corpus_role="narrative_guidance"
        )

    assert result["local_indexed"] is True
    assert len(result["uploaded"]) == 1
    assert result["uploaded"][0]["local_documents"] == 1
    svc.search_client.load_documents.assert_called_once()
