from __future__ import annotations

from pathlib import Path

from azure.core.credentials import AccessToken

from runtime.ingestion import blob_uploader


class _FakeContainerClient:
    def __init__(self) -> None:
        self.uploaded: list[tuple[str, bool, dict[str, str]]] = []

    def upload_blob(
        self,
        blob_name: str,
        data,
        overwrite: bool = True,
        metadata: dict[str, str] | None = None,
    ) -> None:
        if blob_name.endswith("bad.docx"):
            raise RuntimeError("upload failed")
        self.uploaded.append((blob_name, overwrite, metadata or {}))


class _FakeBlobServiceClient:
    def __init__(self, account_url: str, credential) -> None:
        self.account_url = account_url
        self.credential = credential
        self.container = _FakeContainerClient()

    def get_container_client(self, container_name: str):
        return self.container


class _FakeCredential:
    def get_token(self, *scopes: str, **kwargs) -> AccessToken:
        return AccessToken("token", 9999999999)


def test_upload_source_files_uploads_supported_and_tracks_skipped_failed(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "a.pdf").write_bytes(b"pdf")
    (tmp_path / "bad.docx").write_bytes(b"doc")
    (tmp_path / "skip.txt").write_text("x", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "deck.pptx").write_bytes(b"ppt")

    captured_client = _FakeBlobServiceClient(
        "https://storacct.blob.core.windows.net",
        _FakeCredential(),
    )
    monkeypatch.setattr(
        blob_uploader,
        "BlobServiceClient",
        lambda account_url, credential: captured_client,
    )

    summary = blob_uploader.upload_source_files(
        storage_account_name="storacct",
        container_name="grounding-data",
        input_dir=tmp_path,
        credential=_FakeCredential(),
        overwrite=False,
        uploaded_by="test_user",
        upload_batch="batch-123",
    )

    assert "a.pdf" in summary.uploaded
    assert "nested/deck.pptx" in summary.uploaded
    assert any(item.endswith("skip.txt") for item in summary.skipped)
    assert any(item.startswith("bad.docx:") for item in summary.failed)

    # Confirm metadata required by index projections is present on uploaded blobs.
    metadata_by_blob = {
        name: metadata
        for name, _, metadata in captured_client.container.uploaded
        if not name.endswith("bad.docx")
    }
    assert metadata_by_blob["a.pdf"]["uploaded_by"] == "test_user"
    assert metadata_by_blob["a.pdf"]["upload_batch"] == "batch-123"
    assert metadata_by_blob["a.pdf"]["corpus"] == "b"
    assert metadata_by_blob["a.pdf"]["corpus_role"] == "narrative_guidance"
    assert metadata_by_blob["a.pdf"]["upload_source"] == "ingestion_runner"
    assert metadata_by_blob["a.pdf"]["original_filename"] == "a.pdf"
    assert metadata_by_blob["a.pdf"]["dedupe_method"] == "content_sha256"
