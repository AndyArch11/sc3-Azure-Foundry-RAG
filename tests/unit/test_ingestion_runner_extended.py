"""Extended unit tests for runtime/ingestion/runner.py – targeting uncovered branches.

Supplements test_ingestion_runner.py to push coverage above 50%.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import runtime.ingestion.runner as runner

# ---------------------------------------------------------------------------
# _is_missing_controls_source_error
# ---------------------------------------------------------------------------


def test_is_missing_controls_source_error_true_for_known_markers() -> None:
    assert runner._is_missing_controls_source_error(ValueError("file not found")) is True
    assert runner._is_missing_controls_source_error(ValueError("no such file or directory")) is True
    assert (
        runner._is_missing_controls_source_error(ValueError("upload source documents first"))
        is True
    )
    assert runner._is_missing_controls_source_error(ValueError("workbook not found")) is True
    assert (
        runner._is_missing_controls_source_error(ValueError("PDF not found")) is True
    )  # case-insensitive


def test_is_missing_controls_source_error_false_for_unrelated_errors() -> None:
    assert runner._is_missing_controls_source_error(ValueError("unexpected token")) is False
    assert runner._is_missing_controls_source_error(RuntimeError("connection timeout")) is False
    assert runner._is_missing_controls_source_error(Exception("")) is False


# ---------------------------------------------------------------------------
# _download_controls_source_files_aws – early-exit paths
# ---------------------------------------------------------------------------


def test_download_controls_source_files_aws_empty_prefix_returns_empty() -> None:
    assert (
        runner._download_controls_source_files_aws("cis_controls", "", object(), "my-bucket") == []
    )
    assert (
        runner._download_controls_source_files_aws("cis_controls", "   ", object(), "my-bucket")
        == []
    )
    assert (
        runner._download_controls_source_files_aws("cis_controls", "/", object(), "my-bucket") == []
    )


def test_download_controls_source_files_aws_unsupported_framework_raises() -> None:
    with pytest.raises(RuntimeError, match="only supported for cis_controls and pci_dss"):
        runner._download_controls_source_files_aws("ism", "some/prefix", object(), "my-bucket")


def test_download_controls_source_files_aws_missing_bucket_raises() -> None:
    with pytest.raises(RuntimeError, match="S3_BUCKET_NAME is required"):
        runner._download_controls_source_files_aws("cis_controls", "some/prefix", object(), "")
    with pytest.raises(RuntimeError, match="S3_BUCKET_NAME is required"):
        runner._download_controls_source_files_aws("cis_controls", "some/prefix", object(), "  ")


def test_download_controls_source_files_aws_no_client_method_raises() -> None:
    session_without_client = SimpleNamespace()  # no .client() method
    with pytest.raises(RuntimeError, match="AWS session is not available"):
        runner._download_controls_source_files_aws(
            "cis_controls", "some/prefix", session_without_client, "my-bucket"
        )


# ---------------------------------------------------------------------------
# _run_local – nonexistent input directory
# ---------------------------------------------------------------------------


def test_run_local_nonexistent_input_dir(capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(
        input_dir="/nonexistent/path/xyzzy_does_not_exist",
        output_jsonl="./out/chunks.jsonl",
        chunk_size=1200,
        chunk_overlap=200,
        enable_local_ocr=False,
        local_ocr_min_text_chars=80,
    )
    code = runner._run_local(args)
    assert code == 2
    assert "does not exist" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _run_aws helpers
# ---------------------------------------------------------------------------


def _setup_aws_env(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Install fake credential and storage factories; return a fresh s3 mock."""
    s3_mock = MagicMock()
    s3_mock.list_objects.return_value = []

    class _FakeSession:
        region_name = "ap-southeast-2"

        def client(self, svc: str) -> MagicMock:
            return MagicMock()

    class _FakeCred:
        def get_sdk_credential(self) -> _FakeSession:
            return _FakeSession()

    monkeypatch.setitem(
        sys.modules,
        "runtime.credentials",
        type(
            "CRED",
            (),
            {"get_credential_provider": staticmethod(lambda cloud_provider=None: _FakeCred())},
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "runtime.storage",
        type("STO", (), {"get_storage_client": staticmethod(lambda **kwargs: s3_mock)}),
    )
    return s3_mock


def _fake_grounding_module(from_env_raises: Exception | None = None) -> object:
    class _Cfg:
        opensearch_endpoint = "https://os.example"
        grounding_index_name = "grounding-index"

        @classmethod
        def from_env(cls) -> "_Cfg":
            if from_env_raises is not None:
                raise from_env_raises
            return cls()

    return type(
        "GIA",
        (),
        {
            "AWSGroundingIndexConfig": _Cfg,
            "ensure_grounding_index_aws": staticmethod(lambda config, session: None),
        },
    )()


# ---------------------------------------------------------------------------
# _run_aws – early exit paths
# ---------------------------------------------------------------------------


def test_run_aws_missing_bucket_returns_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _setup_aws_env(monkeypatch)
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)
    monkeypatch.delenv("AWS_S3_BUCKET_NAME", raising=False)

    args = argparse.Namespace(skip_upload=True, input_dir=None, storage_container_query="")
    assert runner._run_aws(args) == 1
    assert "S3_BUCKET_NAME" in capsys.readouterr().err


def test_run_aws_grounding_config_error_returns_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _setup_aws_env(monkeypatch)
    monkeypatch.setenv("S3_BUCKET_NAME", "test-bucket")

    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.grounding_index_aws",
        _fake_grounding_module(from_env_raises=ValueError("OPENSEARCH_ENDPOINT is required")),
    )

    args = argparse.Namespace(skip_upload=True, input_dir=None, storage_container_query="")
    assert runner._run_aws(args) == 1
    assert "configuration error" in capsys.readouterr().err.lower()


def test_run_aws_ensure_grounding_index_fails_returns_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _setup_aws_env(monkeypatch)
    monkeypatch.setenv("S3_BUCKET_NAME", "test-bucket")

    class _Cfg:
        opensearch_endpoint = "https://os.example"
        grounding_index_name = "grounding-index"

        @classmethod
        def from_env(cls) -> "_Cfg":
            return cls()

    def _fail_ensure(config: object, session: object) -> None:
        raise RuntimeError("Connection refused")

    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.grounding_index_aws",
        type(
            "GIA",
            (),
            {
                "AWSGroundingIndexConfig": _Cfg,
                "ensure_grounding_index_aws": staticmethod(_fail_ensure),
            },
        )(),
    )

    args = argparse.Namespace(skip_upload=True, input_dir=None, storage_container_query="")
    assert runner._run_aws(args) == 1
    assert "grounding index" in capsys.readouterr().err.lower()


def test_run_aws_list_objects_fails_returns_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    s3_mock = _setup_aws_env(monkeypatch)
    monkeypatch.setenv("S3_BUCKET_NAME", "test-bucket")
    s3_mock.list_objects.side_effect = RuntimeError("S3 unreachable")

    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.grounding_index_aws",
        _fake_grounding_module(),
    )

    args = argparse.Namespace(skip_upload=True, input_dir=None, storage_container_query="")
    assert runner._run_aws(args) == 1
    assert "Failed to list" in capsys.readouterr().err


def test_run_aws_no_indexable_files_returns_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    s3_mock = _setup_aws_env(monkeypatch)
    monkeypatch.setenv("S3_BUCKET_NAME", "test-bucket")
    # Return only an unsupported extension so indexable_keys is empty
    s3_mock.list_objects.return_value = ["corpus-b/by-dedupe/archive.zip"]

    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.grounding_index_aws",
        _fake_grounding_module(),
    )

    args = argparse.Namespace(skip_upload=True, input_dir=None, storage_container_query="")
    assert runner._run_aws(args) == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["mode"] == "aws"
    assert payload["documents_processed"] == 0
    assert "No indexable files" in payload["note"]


def test_run_aws_success_skip_upload(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Full happy-path: skip upload, one PDF in S3, extract → chunk → index."""
    s3_mock = _setup_aws_env(monkeypatch)
    monkeypatch.setenv("S3_BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("AWS_REGION", "ap-southeast-2")

    s3_mock.list_objects.return_value = ["corpus-b/by-dedupe/report.pdf"]
    s3_mock.get_object.return_value = b"%PDF fake content"
    s3_mock.get_object_metadata.return_value = {
        "corpus": "b",
        "corpus_role": "narrative_guidance",
    }

    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.grounding_index_aws",
        _fake_grounding_module(),
    )
    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.publish_grounding_aws",
        type(
            "PGA",
            (),
            {
                "upload_grounding_chunks_aws": staticmethod(
                    lambda *a, **k: {
                        "records_indexed": 3,
                        "records_skipped": 0,
                        "records_failed": 0,
                    }
                )
            },
        )(),
    )

    # Fake document returned by extract_source_document
    class _FakeRawDoc:
        source_type = "pdf"
        text = "Sample document text content for indexing purposes. " * 5

    # Fake SourceDocument wrapping
    class _FakeSourceDocument:
        def __init__(self, source_path: str, source_type: str, text: str) -> None:
            self.source_path = source_path
            self.source_type = source_type
            self.text = text

    # Fake chunks
    class _FakeChunk:
        chunk_id = "chunk-001"
        chunk_index = 0
        content = "Sample document text content for indexing purposes."
        source_type = "pdf"

    import runtime.ingestion.chunking as _chunking
    import runtime.ingestion.extractors as _extractors
    import runtime.ingestion.models as _models

    monkeypatch.setattr(_extractors, "extract_source_document", lambda *a, **k: _FakeRawDoc())
    monkeypatch.setattr(_chunking, "chunk_documents", lambda docs, **k: [_FakeChunk()])
    monkeypatch.setattr(_models, "SourceDocument", _FakeSourceDocument)

    args = argparse.Namespace(
        skip_upload=True,
        input_dir=None,
        storage_container_query="",
        replace_existing=False,
    )
    code = runner._run_aws(args)
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert payload["mode"] == "aws"
    assert payload["storage"] == "s3"
    assert payload["documents_processed"] == 1
    assert payload["chunks_indexed"] == 3


# ---------------------------------------------------------------------------
# _run_reset – additional error paths
# ---------------------------------------------------------------------------


def test_run_reset_unsupported_cloud_provider(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLOUD_PROVIDER", "gcp")
    code = runner._run_reset(argparse.Namespace(purge_blobs=False))
    assert code == 1
    assert "Unsupported" in capsys.readouterr().err


def test_run_reset_aws_config_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")

    class _BadCfg:
        @classmethod
        def from_env(cls) -> None:
            raise ValueError("OPENSEARCH_ENDPOINT is required")

    monkeypatch.setitem(
        sys.modules,
        "runtime.credentials",
        type(
            "CRED",
            (),
            {"get_credential_provider": staticmethod(lambda cloud_provider=None: MagicMock())},
        )(),
    )
    monkeypatch.setitem(
        sys.modules,
        "runtime.storage",
        type(
            "STO",
            (),
            {"get_storage_client": staticmethod(lambda **k: MagicMock())},
        )(),
    )
    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.reset_aws",
        type(
            "RAW",
            (),
            {
                "AWSResetConfig": _BadCfg,
                "reset_loaded_data_aws": staticmethod(lambda *a, **k: {}),
            },
        )(),
    )

    code = runner._run_reset(argparse.Namespace(purge_blobs=False))
    assert code == 1
    assert "configuration error" in capsys.readouterr().err.lower()


def test_run_reset_aws_runtime_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")

    class _Cfg:
        @classmethod
        def from_env(cls) -> "_Cfg":
            return cls()

    class _FakeCred:
        def get_sdk_credential(self) -> object:
            return object()

    def _fail_reset(*a: object, **k: object) -> None:
        raise RuntimeError("OpenSearch unavailable")

    monkeypatch.setitem(
        sys.modules,
        "runtime.credentials",
        type(
            "CRED",
            (),
            {"get_credential_provider": staticmethod(lambda cloud_provider=None: _FakeCred())},
        )(),
    )
    monkeypatch.setitem(
        sys.modules,
        "runtime.storage",
        type(
            "STO",
            (),
            {"get_storage_client": staticmethod(lambda **k: MagicMock())},
        )(),
    )
    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.reset_aws",
        type(
            "RAW",
            (),
            {
                "AWSResetConfig": _Cfg,
                "reset_loaded_data_aws": staticmethod(_fail_reset),
            },
        )(),
    )

    code = runner._run_reset(argparse.Namespace(purge_blobs=False))
    assert code == 1
    assert "Reset error" in capsys.readouterr().err


def test_run_reset_azure_config_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLOUD_PROVIDER", "azure")

    class _BadCfg:
        @classmethod
        def from_env(cls) -> None:
            raise ValueError("AZURE_SEARCH_ENDPOINT is required")

    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.config",
        type("M", (), {"IngestionConfig": _BadCfg})(),
    )
    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.reset",
        type(
            "R",
            (),
            {"reset_loaded_data": staticmethod(lambda *a, **k: {})},
        )(),
    )
    monkeypatch.setitem(
        sys.modules,
        "azure.identity",
        type("A", (), {"DefaultAzureCredential": staticmethod(lambda: object())})(),
    )

    code = runner._run_reset(argparse.Namespace(purge_blobs=False))
    assert code == 1
    assert "configuration error" in capsys.readouterr().err.lower()


def test_run_reset_azure_runtime_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLOUD_PROVIDER", "azure")

    class _Cfg:
        @classmethod
        def from_env(cls) -> "_Cfg":
            return cls()

    def _fail_reset(*a: object, **k: object) -> None:
        raise RuntimeError("Search service unavailable")

    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.config",
        type("M", (), {"IngestionConfig": _Cfg})(),
    )
    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.reset",
        type(
            "R",
            (),
            {"reset_loaded_data": staticmethod(_fail_reset)},
        )(),
    )
    monkeypatch.setitem(
        sys.modules,
        "azure.identity",
        type("A", (), {"DefaultAzureCredential": staticmethod(lambda: object())})(),
    )

    code = runner._run_reset(argparse.Namespace(purge_blobs=False))
    assert code == 1
    assert "Reset error" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _run_controls – AWS error paths
# ---------------------------------------------------------------------------


def _setup_controls_aws_modules(monkeypatch: pytest.MonkeyPatch, registry: dict) -> None:
    """Patch sys.modules with fakes for _run_controls AWS path."""

    class _Provider:
        def get_sdk_credential(self) -> object:
            return object()

    monkeypatch.setitem(
        sys.modules,
        "runtime.credentials",
        type(
            "CRED",
            (),
            {"get_credential_provider": staticmethod(lambda cloud_provider=None: _Provider())},
        )(),
    )
    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.controls_runner",
        type(
            "CR",
            (),
            {
                "_build_parser_registry": staticmethod(lambda: registry),
                "_selected_frameworks": staticmethod(lambda framework, reg: list(reg.keys())),
            },
        )(),
    )
    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.publish_controls_aws",
        type(
            "PCA",
            (),
            {
                "upload_controls_records_aws": staticmethod(
                    lambda *a, **k: {"records_failed": 0, "records_uploaded": 1}
                )
            },
        )(),
    )


def test_run_controls_aws_config_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")

    class _BadCfg:
        @classmethod
        def from_env(cls) -> None:
            raise ValueError("OPENSEARCH_ENDPOINT is required")

    _setup_controls_aws_modules(monkeypatch, registry={})
    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.controls_index_aws",
        type(
            "CIA",
            (),
            {
                "AWSControlsIndexConfig": _BadCfg,
                "ensure_controls_index_aws": staticmethod(lambda *a: None),
            },
        )(),
    )

    controls_args = argparse.Namespace(
        controls_framework="all",
        controls_source_prefix="",
        replace_existing=False,
        dry_run=False,
        no_guidance=False,
    )
    assert runner._run_controls(controls_args) == 1
    assert "configuration error" in capsys.readouterr().err.lower()


def test_run_controls_aws_skip_missing_source_files(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")

    class _Cfg:
        @classmethod
        def from_env(cls) -> "_Cfg":
            return cls()

    registry = {
        "cis_controls": {
            "factory": lambda fetch_guidance: type(
                "BadP",
                (),
                {
                    "parse": lambda self: (_ for _ in ()).throw(RuntimeError("workbook not found")),
                    "to_jsonl": lambda self, recs: "",
                },
            )(),
        }
    }
    _setup_controls_aws_modules(monkeypatch, registry=registry)
    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.controls_index_aws",
        type(
            "CIA",
            (),
            {
                "AWSControlsIndexConfig": _Cfg,
                "ensure_controls_index_aws": staticmethod(lambda *a: None),
            },
        )(),
    )

    controls_args = argparse.Namespace(
        controls_framework="cis_controls",
        controls_source_prefix="",
        replace_existing=False,
        dry_run=False,
        no_guidance=False,
        skip_missing_source_files=True,
    )
    code = runner._run_controls(controls_args)
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    result = payload["results"][0]
    assert result["action"] == "skipped_missing_source"
    assert result["framework"] == "cis_controls"


def test_run_controls_aws_no_records(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")

    class _Cfg:
        @classmethod
        def from_env(cls) -> "_Cfg":
            return cls()

    registry = {
        "ism": {
            "factory": lambda fetch_guidance: type(
                "EmptyP",
                (),
                {
                    "parse": lambda self: [],
                    "to_jsonl": lambda self, recs: "",
                },
            )(),
        }
    }
    _setup_controls_aws_modules(monkeypatch, registry=registry)
    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.controls_index_aws",
        type(
            "CIA",
            (),
            {
                "AWSControlsIndexConfig": _Cfg,
                "ensure_controls_index_aws": staticmethod(lambda *a: None),
            },
        )(),
    )

    controls_args = argparse.Namespace(
        controls_framework="ism",
        controls_source_prefix="",
        replace_existing=False,
        dry_run=False,
        no_guidance=False,
    )
    code = runner._run_controls(controls_args)
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 1
    assert payload["results"][0]["error"] == "Parser returned no records"


def test_run_controls_aws_upload_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")

    class _Cfg:
        @classmethod
        def from_env(cls) -> "_Cfg":
            return cls()

    class _Provider:
        def get_sdk_credential(self) -> object:
            return object()

    registry = {
        "nist_csf": {
            "factory": lambda fetch_guidance: type(
                "GoodP",
                (),
                {
                    "parse": lambda self: [{"id": 1}],
                    "to_jsonl": lambda self, recs: '{"id":1}\n',
                },
            )(),
        }
    }

    monkeypatch.setitem(
        sys.modules,
        "runtime.credentials",
        type(
            "CRED",
            (),
            {"get_credential_provider": staticmethod(lambda cloud_provider=None: _Provider())},
        )(),
    )
    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.controls_runner",
        type(
            "CR",
            (),
            {
                "_build_parser_registry": staticmethod(lambda: registry),
                "_selected_frameworks": staticmethod(lambda framework, reg: list(reg.keys())),
            },
        )(),
    )
    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.controls_index_aws",
        type(
            "CIA",
            (),
            {
                "AWSControlsIndexConfig": _Cfg,
                "ensure_controls_index_aws": staticmethod(lambda *a: None),
            },
        )(),
    )
    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.publish_controls_aws",
        type(
            "PCA",
            (),
            {
                "upload_controls_records_aws": staticmethod(
                    lambda *a, **k: (_ for _ in ()).throw(RuntimeError("OpenSearch write error"))
                )
            },
        )(),
    )

    controls_args = argparse.Namespace(
        controls_framework="nist_csf",
        controls_source_prefix="",
        replace_existing=False,
        dry_run=False,
        no_guidance=False,
    )
    code = runner._run_controls(controls_args)
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 1
    assert "Index publish failed" in payload["results"][0]["error"]


# ---------------------------------------------------------------------------
# _run_controls – unsupported provider
# ---------------------------------------------------------------------------


def test_run_controls_unsupported_provider(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLOUD_PROVIDER", "gcp")

    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.controls_runner",
        type(
            "CR",
            (),
            {
                "_build_parser_registry": staticmethod(lambda: {}),
                "_selected_frameworks": staticmethod(lambda *a: []),
            },
        )(),
    )

    controls_args = argparse.Namespace(
        controls_framework="all",
        controls_source_prefix="",
        replace_existing=False,
        dry_run=False,
        no_guidance=False,
    )
    code = runner._run_controls(controls_args)
    assert code == 1
    assert "Unsupported" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _run_controls – Azure config error
# ---------------------------------------------------------------------------


def test_run_controls_azure_config_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLOUD_PROVIDER", "azure")

    class _BadCfg:
        @classmethod
        def from_env(cls) -> None:
            raise ValueError("AZURE_SEARCH_ENDPOINT is required")

    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.controls_runner",
        type(
            "CR",
            (),
            {
                "_build_parser_registry": staticmethod(lambda: {}),
                "_selected_frameworks": staticmethod(lambda *a: []),
            },
        )(),
    )
    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.controls_index",
        type(
            "CI",
            (),
            {
                "ControlsIndexConfig": _BadCfg,
                "ensure_controls_index": staticmethod(lambda *a: None),
            },
        )(),
    )
    monkeypatch.setitem(
        sys.modules,
        "azure.identity",
        type("A", (), {"DefaultAzureCredential": staticmethod(lambda: object())})(),
    )

    controls_args = argparse.Namespace(
        controls_framework="all",
        controls_source_prefix="",
        replace_existing=False,
        dry_run=False,
        no_guidance=False,
    )
    code = runner._run_controls(controls_args)
    assert code == 1
    assert "configuration error" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# _run_controls – Azure skip_missing and no_records paths
# ---------------------------------------------------------------------------


def _setup_controls_azure_modules(monkeypatch: pytest.MonkeyPatch, registry: dict) -> None:
    """Patch sys.modules with fakes for _run_controls Azure path."""
    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.controls_runner",
        type(
            "CR",
            (),
            {
                "_build_parser_registry": staticmethod(lambda: registry),
                "_selected_frameworks": staticmethod(lambda framework, reg: list(reg.keys())),
            },
        )(),
    )
    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.publish_controls",
        type(
            "PC",
            (),
            {
                "upload_controls_records": staticmethod(
                    lambda *a, **k: {"records_failed": 0, "records_uploaded": 1}
                )
            },
        )(),
    )
    monkeypatch.setitem(
        sys.modules,
        "azure.identity",
        type("A", (), {"DefaultAzureCredential": staticmethod(lambda: object())})(),
    )


def test_run_controls_azure_skip_missing_source_files(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLOUD_PROVIDER", "azure")

    class _Cfg:
        @classmethod
        def from_env(cls) -> "_Cfg":
            return cls()

    registry = {
        "pci_dss": {
            "factory": lambda fetch_guidance: type(
                "BadP",
                (),
                {
                    "parse": lambda self: (_ for _ in ()).throw(RuntimeError("PDF not found")),
                    "to_jsonl": lambda self, recs: "",
                },
            )(),
        }
    }
    _setup_controls_azure_modules(monkeypatch, registry=registry)
    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.controls_index",
        type(
            "CI",
            (),
            {
                "ControlsIndexConfig": _Cfg,
                "ensure_controls_index": staticmethod(lambda *a: None),
            },
        )(),
    )

    controls_args = argparse.Namespace(
        controls_framework="pci_dss",
        controls_source_prefix="",
        replace_existing=False,
        dry_run=False,
        no_guidance=False,
        skip_missing_source_files=True,
    )
    code = runner._run_controls(controls_args)
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    result = payload["results"][0]
    assert result["action"] == "skipped_missing_source"
    assert result["framework"] == "pci_dss"


def test_run_controls_azure_no_records(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLOUD_PROVIDER", "azure")

    class _Cfg:
        @classmethod
        def from_env(cls) -> "_Cfg":
            return cls()

    registry = {
        "essential_eight": {
            "factory": lambda fetch_guidance: type(
                "EmptyP",
                (),
                {
                    "parse": lambda self: [],
                    "to_jsonl": lambda self, recs: "",
                },
            )(),
        }
    }
    _setup_controls_azure_modules(monkeypatch, registry=registry)
    monkeypatch.setitem(
        sys.modules,
        "runtime.ingestion.controls_index",
        type(
            "CI",
            (),
            {
                "ControlsIndexConfig": _Cfg,
                "ensure_controls_index": staticmethod(lambda *a: None),
            },
        )(),
    )

    controls_args = argparse.Namespace(
        controls_framework="essential_eight",
        controls_source_prefix="",
        replace_existing=False,
        dry_run=False,
        no_guidance=False,
    )
    code = runner._run_controls(controls_args)
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 1
    assert payload["results"][0]["error"] == "Parser returned no records"


# ---------------------------------------------------------------------------
# main() – additional dispatch paths
# ---------------------------------------------------------------------------


def test_main_dispatches_azure_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUD_PROVIDER", "azure")  # let monkeypatch restore original value
    monkeypatch.setattr(runner, "parse_args", lambda: argparse.Namespace(mode="azure"))
    monkeypatch.setattr(runner, "_run_azure", lambda args: 42)
    assert runner.main() == 42
    assert os.environ.get("CLOUD_PROVIDER") == "azure"


def test_main_dispatches_aws_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")  # let monkeypatch restore original value
    monkeypatch.setattr(runner, "parse_args", lambda: argparse.Namespace(mode="aws"))
    monkeypatch.setattr(runner, "_run_aws", lambda args: 43)
    assert runner.main() == 43
    assert os.environ.get("CLOUD_PROVIDER") == "aws"


def test_main_dispatches_reset_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLOUD_PROVIDER", raising=False)
    monkeypatch.setattr(runner, "parse_args", lambda: argparse.Namespace(mode="reset"))
    monkeypatch.setattr(runner, "_run_reset", lambda args: 44)
    assert runner.main() == 44


def test_main_dispatches_controls_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLOUD_PROVIDER", raising=False)
    monkeypatch.setattr(runner, "parse_args", lambda: argparse.Namespace(mode="controls"))
    monkeypatch.setattr(runner, "_run_controls", lambda args: 45)
    assert runner.main() == 45
