from __future__ import annotations

import hashlib
import sys
from datetime import datetime
from types import SimpleNamespace

from query_web import utils


def test_utc_now_iso_returns_parseable_utc_timestamp() -> None:
    value = utils._utc_now_iso()
    parsed = datetime.fromisoformat(value)

    assert parsed.tzinfo is not None


def test_sanitise_blob_name_component_replaces_invalid_chars_and_falls_back() -> None:
    assert utils._sanitise_blob_name_component("  a/b\\c:d*e?f  ") == "a_b_c_d_e_f"
    assert utils._sanitise_blob_name_component("   ") == "file"


def test_compute_normalised_text_hash_for_text_content() -> None:
    content = b" Hello\n\tWORLD  "

    digest, method = utils._compute_normalised_text_hash(
        content,
        filename="sample.txt",
        content_type="text/plain",
    )

    expected = hashlib.sha256("hello world".encode("utf-8")).hexdigest()
    assert digest == expected
    assert method == "normalised_text"


def test_compute_normalised_text_hash_for_binary_and_empty_text() -> None:
    digest, method = utils._compute_normalised_text_hash(
        b"\x00\x01\x02",
        filename="sample.pdf",
        content_type="application/pdf",
    )
    assert digest is None
    assert method == "binary"

    digest, method = utils._compute_normalised_text_hash(
        b"\n\t   ",
        filename="sample.txt",
        content_type="text/plain",
    )
    assert digest is None
    assert method == "empty"


def test_extract_dedupe_hashes_deduplicates_and_ignores_invalid_entries() -> None:
    hash_a = "a" * 64
    hash_b = "B" * 64

    result = utils._extract_dedupe_hashes(
        [
            f"file1: duplicate-content_sha256:{hash_a}",
            "not-a-match",
            f"file2: duplicate-normalised_text_sha256:{hash_b}",
            f"file3: duplicate-content_sha256:{hash_a}",
        ]
    )

    assert result == [hash_a, hash_b]


def test_dedupe_blob_prefix_builds_expected_path() -> None:
    assert utils._dedupe_blob_prefix("b", "deadbeef") == "corpus-b/by-dedupe/deadbeef"


def test_sanitise_untrusted_text_delegates_to_guard(monkeypatch) -> None:
    fake_module = SimpleNamespace(sanitise_untrusted_text=lambda text: f"safe:{text}")
    monkeypatch.setitem(sys.modules, "query_web.security.prompt_injection_guard", fake_module)

    assert utils.sanitise_untrusted_text("hello") == "safe:hello"


def test_sanitise_conversation_turn_delegates_to_guard(monkeypatch) -> None:
    fake_module = SimpleNamespace(
        sanitise_conversation_turn=lambda role, content: f"{role}:{content}:safe"
    )
    monkeypatch.setitem(sys.modules, "query_web.security.prompt_injection_guard", fake_module)

    assert utils.sanitise_conversation_turn("user", "hi") == "user:hi:safe"
