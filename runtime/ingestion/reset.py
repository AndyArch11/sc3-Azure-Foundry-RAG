from __future__ import annotations

import logging
from typing import Any

from azure.core.credentials import TokenCredential
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexerClient
from azure.storage.blob import BlobServiceClient

from .config import IngestionConfig

logger = logging.getLogger(__name__)


def _purge_index_documents(config: IngestionConfig, credential: TokenCredential, batch_size: int = 500) -> int:
    """Delete all indexed chunk documents while preserving the index schema."""
    client = SearchClient(
        endpoint=config.search_endpoint,
        index_name=config.search_index_name,
        credential=credential,
    )

    deleted = 0
    while True:
        page = list(client.search(search_text="*", select=["id"], top=batch_size))
        if not page:
            break

        docs_to_delete = [{"id": doc["id"]} for doc in page if doc.get("id")]
        if not docs_to_delete:
            break

        client.delete_documents(documents=docs_to_delete)
        deleted += len(docs_to_delete)

    return deleted


def _reset_indexer_state(config: IngestionConfig, credential: TokenCredential) -> bool:
    """Reset indexer high-watermark so unchanged blobs can be reprocessed."""
    client = SearchIndexerClient(endpoint=config.search_endpoint, credential=credential)
    try:
        client.reset_indexer(config.indexer_name)
        return True
    except ResourceNotFoundError:
        logger.warning("Indexer not found for reset: %s", config.indexer_name)
        return False


def _purge_source_blobs(config: IngestionConfig, credential: TokenCredential) -> int:
    """Delete all blobs in the ingestion source container."""
    account_url = f"https://{config.storage_account_name}.blob.core.windows.net"
    blob_service = BlobServiceClient(account_url=account_url, credential=credential)
    container_client = blob_service.get_container_client(config.storage_container_name)

    deleted = 0
    blob_names = [b.name for b in container_client.list_blobs()]
    if not blob_names:
        return 0

    # Delete in chunks to keep requests small and predictable.
    chunk = 256
    for i in range(0, len(blob_names), chunk):
        batch = blob_names[i : i + chunk]
        container_client.delete_blobs(*batch)
        deleted += len(batch)

    return deleted


def reset_loaded_data(
    config: IngestionConfig,
    credential: TokenCredential,
    *,
    purge_blobs: bool = False,
) -> dict[str, Any]:
    """
    Remove loaded indexed data on demand without deleting Azure resources.

    - Purges all indexed documents from the Search index.
    - Resets indexer state so a later run can reprocess unchanged blobs.
    - Optionally purges source blobs from the configured storage container.
    """
    deleted_docs = 0
    deleted_blobs = 0

    try:
        deleted_docs = _purge_index_documents(config, credential)
    except ResourceNotFoundError as exc:
        raise RuntimeError(f"Search index not found: {config.search_index_name}") from exc
    except HttpResponseError as exc:
        raise RuntimeError(f"Failed to purge indexed documents: {exc.message or str(exc)}") from exc

    indexer_reset = _reset_indexer_state(config, credential)

    if purge_blobs:
        try:
            deleted_blobs = _purge_source_blobs(config, credential)
        except ResourceNotFoundError as exc:
            raise RuntimeError(f"Storage container not found: {config.storage_container_name}") from exc
        except HttpResponseError as exc:
            raise RuntimeError(f"Failed to purge source blobs: {exc.message or str(exc)}") from exc

    return {
        "deleted_index_documents": deleted_docs,
        "indexer_reset": indexer_reset,
        "deleted_source_blobs": deleted_blobs,
        "storage_container": config.storage_container_name,
        "search_index": config.search_index_name,
    }
