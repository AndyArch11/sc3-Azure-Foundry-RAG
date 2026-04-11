from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from runtime.ingestion.parsers import aescsf, ism, nist_csf


def test_aescsf_slugify_and_parse_maturity_level() -> None:
    assert aescsf._slugify("Version 2.0") == "version_2_0"
    assert aescsf._parse_maturity_level("MIL-3") == 3
    assert aescsf._parse_maturity_level(None) is None
    assert aescsf._parse_maturity_level("none") is None


def test_aescsf_build_records_filters_rows_and_emits_expected_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Sheet:
        def iter_rows(self, values_only: bool = True):
            return [
                (
                    "Domain",
                    "Objective ID",
                    "Objective",
                    "Practice ID",
                    "Practice",
                    "Guidance",
                    "MIL",
                    "Profile",
                ),
                ("ACCESS", "ACCESS-1", "Identity", "ACCESS-1a", "Do MFA", "Guide", "MIL-2", "OS"),
                (
                    "ACCESS",
                    "ACCESS-AP",
                    "Identity",
                    "ACCESS-AP1",
                    "Bad practice",
                    "Guide",
                    "MIL-1",
                    "OS",
                ),
                ("ACCESS", "ACCESS-1", "Identity", "not-a-practice", "ignore", "", "", ""),
            ]

    class _Workbook:
        sheetnames = ["Sheet1"]

        def __getitem__(self, name: str):
            return _Sheet()

    class _OpenPyXL:
        @staticmethod
        def load_workbook(*args, **kwargs):
            return _Workbook()

    monkeypatch.setitem(__import__("sys").modules, "openpyxl", _OpenPyXL)

    parser = aescsf.AescsfParser(include_anti_patterns=False)
    records = parser._build_records(b"ignored-by-fake-loader")

    assert len(records) == 1
    assert records[0].requirement_id == "AESCSF-ACCESS-1a"
    assert records[0].maturity_level == 2
    assert records[0].framework == aescsf.FRAMEWORK


def test_aescsf_build_records_raises_when_sheet_or_rows_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _WorkbookNoSheets:
        sheetnames: list[str] = []

    class _SheetEmpty:
        def iter_rows(self, values_only: bool = True):
            return []

    class _WorkbookEmptyRows:
        sheetnames = ["s"]

        def __getitem__(self, name: str):
            return _SheetEmpty()

    class _OpenPyXLNoSheets:
        @staticmethod
        def load_workbook(*args, **kwargs):
            return _WorkbookNoSheets()

    class _OpenPyXLEmptyRows:
        @staticmethod
        def load_workbook(*args, **kwargs):
            return _WorkbookEmptyRows()

    monkeypatch.setitem(__import__("sys").modules, "openpyxl", _OpenPyXLNoSheets)
    with pytest.raises(RuntimeError, match="no sheets"):
        aescsf.AescsfParser()._build_records(b"x")

    monkeypatch.setitem(__import__("sys").modules, "openpyxl", _OpenPyXLEmptyRows)
    with pytest.raises(RuntimeError, match="empty"):
        aescsf.AescsfParser()._build_records(b"x")


def test_ism_helpers_cover_requirement_and_statement_and_recursion() -> None:
    assert ism._slugify("ISM March 2026") == "ism_march_2026"
    assert ism._requirement_id("ism-1997") == "ISM-1997"
    assert ism._requirement_id("ism-principle-gov-01") == "ISM-GOV-01"
    assert ism._requirement_id("weird-id") == "ISM-WEIRD-ID"

    statement_control = {"parts": [{"name": "statement", "prose": "  use mfa  "}]}
    assert ism._extract_statement(statement_control) == "use mfa"
    assert ism._extract_statement({"parts": [{"name": "other", "prose": "x"}]}) == ""

    group = {
        "title": "Guideline A",
        "groups": [
            {
                "title": "Section A1",
                "controls": [{"id": "ism-1"}],
                "groups": [{"title": "Sub A1a", "controls": [{"id": "ism-2"}]}],
            }
        ],
    }
    results: list[tuple[dict, str, str, str]] = []
    ism._collect_controls(group, guideline="", section="", results=results)
    assert len(results) == 2
    assert results[0][1] == "Guideline A"


def test_ism_build_records_emits_expected_shape() -> None:
    data = {
        "catalog": {
            "metadata": {"version": "v-test", "last-modified": "2026-03-01T00:00:00Z"},
            "groups": [
                {
                    "title": "Gov",
                    "groups": [
                        {
                            "title": "Policies",
                            "controls": [
                                {
                                    "id": "ism-1997",
                                    "title": "Control: ism-1997",
                                    "props": [{"name": "applicability", "value": "NC"}],
                                    "parts": [
                                        {"name": "statement", "prose": "Implement backup controls"}
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    }

    records = ism.IsmParser()._build_records(data)
    assert len(records) == 1
    rec = records[0]
    assert rec.requirement_id == "ISM-1997"
    assert rec.framework_version == "v-test"
    assert rec.effective_date == "2026-03-01"
    assert rec.guidance_text == ""
    assert rec.control_family == "Gov"


def test_nist_helpers_and_parse_with_small_core(monkeypatch: pytest.MonkeyPatch) -> None:
    assert nist_csf._slugify("Risk Management") == "risk-management"

    monkeypatch.setattr(
        nist_csf,
        "_CSF_CORE",
        [
            (
                "ID",
                "Identify",
                "desc",
                [
                    (
                        "ID.AM",
                        "Asset Management",
                        "category desc",
                        [("ID.AM-01", "Inventory assets")],
                    )
                ],
            )
        ],
    )
    monkeypatch.setattr(nist_csf, "_CATEGORY_KEYWORDS", {"ID.AM": ["asset", "management"]})

    parser = nist_csf.NistCsfParser(fetch_guidance=False)
    records = parser.parse()

    assert len(records) == 1
    rec = records[0]
    assert rec.requirement_id == "NIST-CSF-ID-ID-AM-01"
    assert rec.guidance_text == "category desc"
    assert rec.source_section.startswith("Identify")


def test_nist_build_category_guidance_map_fetch_disabled_and_missing_libs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert nist_csf._build_category_guidance_map(fetch_guidance=False) == {}

    original_import = __import__("builtins").__import__

    def _raise_import(name, *args, **kwargs):
        if name in {"requests", "bs4"}:
            raise ImportError("missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(__import__("builtins"), "__import__", _raise_import)
    result = nist_csf._build_category_guidance_map(fetch_guidance=True)
    assert result == {}


def test_ism_fetch_catalog_uses_json_response(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"catalog": {"metadata": {"version": "x"}, "groups": []}}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(ism.urllib.request, "urlopen", lambda req, timeout=30: _Resp())
    data = ism.IsmParser()._fetch_catalog()
    assert data == payload


def test_aescsf_fetch_workbook_reads_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return b"xlsx-bytes"

    monkeypatch.setattr(aescsf.urllib.request, "urlopen", lambda req, timeout=60: _Resp())
    data = aescsf.AescsfParser()._fetch_workbook()
    assert data == b"xlsx-bytes"
