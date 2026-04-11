from __future__ import annotations

from dataclasses import dataclass

import pytest

from runtime.ingestion import reset


class _FakeSearchClient:
    def __init__(self, endpoint: str, index_name: str, credential) -> None:
        self.pages = [
            [{"id": "a"}, {"id": "b"}],
            [{"id": "c"}],
            [],
        ]
        self.deleted: list[dict] = []

    def search(self, search_text: str, select: list[str], top: int):
        return self.pages.pop(0)

    def delete_documents(self, documents: list[dict]) -> None:
        self.deleted.extend(documents)


class _FakeIndexerClient:
    def __init__(self, endpoint: str, credential) -> None:
        self.reset_called = False

    def reset_indexer(self, indexer_name: str) -> None:
        self.reset_called = True


class _FakeBlobContainer:
    def __init__(self) -> None:
        self.deleted_batches: list[list[str]] = []

    def list_blobs(self):
        return [type("B", (), {"name": "1"})(), type("B", (), {"name": "2"})()]

    def delete_blobs(self, *batch):
        self.deleted_batches.append(list(batch))


class _FakeBlobService:
    def __init__(self, account_url: str, credential) -> None:
        self.container = _FakeBlobContainer()

    def get_container_client(self, container_name: str):
        return self.container


@dataclass
class _Cfg:
    search_endpoint: str = "https://search.example"
    search_index_name: str = "grounding-index"
    indexer_name: str = "grounding-index-indexer"
    storage_account_name: str = "storacct"
    storage_container_name: str = "grounding-data"


def test_reset_loaded_data_success_with_blob_purge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reset, "SearchClient", _FakeSearchClient)
    monkeypatch.setattr(reset, "SearchIndexerClient", _FakeIndexerClient)
    monkeypatch.setattr(reset, "BlobServiceClient", _FakeBlobService)

    result = reset.reset_loaded_data(_Cfg(), credential=object(), purge_blobs=True)

    assert result["deleted_index_documents"] == 3
    assert result["indexer_reset"] is True
    assert result["deleted_source_blobs"] == 2


def test_reset_loaded_data_wraps_search_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class _NotFound(Exception):
        pass

    class _FailSearchClient:
        def __init__(self, endpoint: str, index_name: str, credential) -> None:
            pass

        def search(self, *args, **kwargs):
            raise _NotFound("missing")

    monkeypatch.setattr(reset, "SearchClient", _FailSearchClient)
    monkeypatch.setattr(reset, "ResourceNotFoundError", _NotFound)

    with pytest.raises(RuntimeError, match="Search index not found"):
        reset.reset_loaded_data(_Cfg(), credential=object())
