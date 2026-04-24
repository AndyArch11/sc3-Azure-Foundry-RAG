from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from runtime.ingestion.parsers import aescsf, ism, nist_ai_rmf, nist_csf


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


def test_nist_ai_rmf_loads_explicit_local_pdf_path(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit_pdf = tmp_path / "explicit.pdf"
    explicit_pdf.write_bytes(b"%PDF-1.4 explicit")

    called_with: list[object] = []

    def _fake_reader(arg):
        called_with.append(arg)
        return "reader-from-explicit"

    monkeypatch.setattr(nist_ai_rmf, "_PdfReader", _fake_reader)
    monkeypatch.setattr(
        nist_ai_rmf.requests,
        "get",
        lambda *args, **kwargs: pytest.fail("requests.get should not be called"),
    )

    parser = nist_ai_rmf.NistAiRmfParser(pdf_path=explicit_pdf)
    reader = parser._load_pdf_reader()

    assert reader == "reader-from-explicit"
    assert called_with == [str(explicit_pdf.resolve())]


def test_nist_ai_rmf_loads_default_local_pdf_path_when_present(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default_pdf = tmp_path / "NIST.AI.100-1.pdf"
    default_pdf.write_bytes(b"%PDF-1.4 default")

    called_with: list[object] = []

    def _fake_reader(arg):
        called_with.append(arg)
        return "reader-from-default"

    monkeypatch.setattr(nist_ai_rmf, "_DEFAULT_PDF_PATH", default_pdf)
    monkeypatch.setattr(nist_ai_rmf, "_PdfReader", _fake_reader)
    monkeypatch.setattr(
        nist_ai_rmf.requests,
        "get",
        lambda *args, **kwargs: pytest.fail("requests.get should not be called"),
    )

    parser = nist_ai_rmf.NistAiRmfParser()
    reader = parser._load_pdf_reader()

    assert reader == "reader-from-default"
    assert called_with == [str(default_pdf)]


def test_nist_ai_rmf_downloads_pdf_when_local_files_absent(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called_with: list[object] = []

    class _Response:
        content = b"%PDF-1.4 downloaded"

        @staticmethod
        def raise_for_status() -> None:
            return None

    def _fake_reader(arg):
        called_with.append(arg)
        return "reader-from-download"

    requested: dict[str, object] = {}

    def _fake_get(url: str, timeout: int):
        requested["url"] = url
        requested["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(nist_ai_rmf, "_DEFAULT_PDF_PATH", tmp_path / "missing-default.pdf")
    monkeypatch.setattr(nist_ai_rmf, "_PdfReader", _fake_reader)
    monkeypatch.setattr(nist_ai_rmf.requests, "get", _fake_get)

    parser = nist_ai_rmf.NistAiRmfParser(pdf_path=tmp_path / "missing-explicit.pdf")
    reader = parser._load_pdf_reader()

    assert reader == "reader-from-download"
    assert requested == {"url": nist_ai_rmf.SOURCE_URI, "timeout": 90}
    assert len(called_with) == 1
    assert isinstance(called_with[0], io.BytesIO)


def test_nist_ai_rmf_extract_control_entries_parses_real_style_tokens() -> None:
    text = (
        "GOVERN 1: Governance is established. "
        "GOVERN 1.1: Legal and regulatory requirements involving AI are understood. "
        "GOVERN 1.2: Trustworthy AI characteristics are integrated. "
        "MAP 1: Context is established and understood. "
        "MAP 1.1: Intended purposes and settings are understood."
    )

    entries = nist_ai_rmf._extract_control_entries(text)

    assert entries[0] == ("GOVERN", "1", "Governance is established.")
    assert entries[1][0] == "GOVERN"
    assert entries[1][1] == "1.1"
    assert "Legal and regulatory requirements" in entries[1][2]
    assert entries[-1][0] == "MAP"
    assert entries[-1][1] == "1.1"


def test_nist_ai_rmf_parse_controls_from_text_uses_real_subcategories_only() -> None:
    text = (
        "GOVERN 1: Governance is established. "
        "GOVERN 1.1: Legal and regulatory requirements involving AI are understood. "
        "GOVERN 1.2: Trustworthy AI characteristics are integrated. "
        "MAP 1: Context is established and understood. "
        "MAP 1.1: Intended purposes and settings are understood."
    )

    parser = nist_ai_rmf.NistAiRmfParser(fetch_guidance=False)
    records = parser._parse_controls_from_text(text)

    assert len(records) == 3
    assert [record.requirement_id for record in records] == [
        "NIST-AI-RMF-GOVERN-1-1",
        "NIST-AI-RMF-GOVERN-1-2",
        "NIST-AI-RMF-MAP-1-1",
    ]
    assert records[0].requirement_text.startswith("Legal and regulatory requirements involving AI")
    assert records[0].control_family.startswith("Govern 1 - Governance is established")
    assert records[0].guidance_text == "Governance is established."
    assert records[0].source_section == "Govern 1.1"
    assert records[0].source_uri == nist_ai_rmf.SOURCE_URI


def test_nist_ai_rmf_extract_playbook_page_guidance_maps_bookmark_urls() -> None:
    html = """
    <html><body>
      <h3>GOVERN 1.1</h3>
      <p>Playbook guidance for 1.1.</p>
      <h3>GOVERN 1.2</h3>
      <p>Playbook guidance for 1.2.</p>
    </body></html>
    """

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        pytest.skip("beautifulsoup4 not installed")

    soup = BeautifulSoup(html, "html.parser")
    result = nist_ai_rmf._extract_playbook_page_guidance(
        soup,
        "GOVERN",
        "https://airc.nist.gov/airmf-resources/playbook/govern/",
    )

    assert result["GOVERN 1.1"][0] == "Playbook guidance for 1.1."
    assert result["GOVERN 1.1"][1].endswith("#govern-1-1")
    assert result["GOVERN 1.2"][0] == "Playbook guidance for 1.2."
    assert result["GOVERN 1.2"][1].endswith("#govern-1-2")
