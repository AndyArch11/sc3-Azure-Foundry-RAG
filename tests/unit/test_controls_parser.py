"""Unit tests for the controls pre-parser pipeline.

Covers:
- RequirementRecord and BaseParser.to_jsonl (parsers/base.py)
- Essential Eight helper functions: _slugify, _normalise_family_name,
  _extract_introduction, _extract_cell_requirements,
  _parse_requirement_table, _parse_maturity_model_page
- EssentialEightParser with fully mocked HTTP (_fetch_soup patched)
- load_controls_jsonl validation (publish_controls.py)
- _batched helper (publish_controls.py)

No network connections are made; all HTTP calls are replaced with unittest.mock.patch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup

from runtime.ingestion.parsers.base import BaseParser, RequirementRecord, filter_keywords
from runtime.ingestion.parsers.cis_controls import CisControlsParser, _build_control_guidance_map
from runtime.ingestion.parsers.essential_eight import (
    CONTROL_FAMILIES,
    EssentialEightParser,
    _extract_cell_requirements,
    _extract_introduction,
    _normalise_family_name,
    _parse_maturity_model_page,
    _parse_requirement_table,
    _slugify,
)
from runtime.ingestion.parsers.pci_dss import (
    PciDssParser,
    _build_requirement_and_guidance_maps,
    _extract_full_text,
)
from runtime.ingestion.parsers.pspf import PspfParser, _parse_pspf_release_text, _pspf_keywords
from runtime.ingestion.publish_controls import _batched, load_controls_jsonl

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_record(**overrides: Any) -> RequirementRecord:
    """Build a minimal valid RequirementRecord, merging any *overrides*."""
    defaults: dict[str, Any] = dict(
        requirement_id="E8-patch-applications-ML1-001",
        framework="Essential Eight",
        framework_version="November 2023",
        control_family="Patch applications",
        maturity_level=1,
        requirement_text="A long enough requirement statement for testing.",
        guidance_text="Some guidance text.",
        keywords=["patching", "vulnerability"],
        source_uri="https://example.com",
        source_section="Appendix A",
        effective_date="November 2023",
        jurisdiction_or_scope="Australia",
    )
    defaults.update(overrides)
    return RequirementRecord(**defaults)


def _minimal_jsonl_dict(**overrides) -> dict:
    """Build a minimal JSONL record dict that satisfies all required fields."""
    base = {
        "requirement_id": "E8-test-ML1-001",
        "framework": "Essential Eight",
        "framework_version": "November 2023",
        "control_family": "Patch applications",
        "maturity_level": 1,
        "requirement_text": "A test requirement.",
        "guidance_text": "",
        "keywords": [],
        "source_uri": "https://example.com",
        "source_section": "Appendix A",
        "effective_date": "November 2023",
        "jurisdiction_or_scope": "Australia",
    }
    base.update(overrides)
    return base


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _table_soup(html: str):
    return _soup(html).find("table")


def _cell_soup(inner_html: str):
    return _soup(f"<td>{inner_html}</td>").find("td")


# ---------------------------------------------------------------------------
# RequirementRecord
# ---------------------------------------------------------------------------


class TestRequirementRecord:
    def test_to_dict_preserves_all_fields(self):
        rec = _make_record()
        d = rec.to_dict()
        assert d["requirement_id"] == "E8-patch-applications-ML1-001"
        assert d["framework"] == "Essential Eight"
        assert d["maturity_level"] == 1
        assert d["keywords"] == ["patching", "vulnerability"]
        assert d["jurisdiction_or_scope"] == "Australia"

    def test_to_dict_returns_plain_dict(self):
        assert isinstance(_make_record().to_dict(), dict)

    def test_maturity_level_can_be_none(self):
        rec = _make_record(maturity_level=None)
        assert rec.to_dict()["maturity_level"] is None


# ---------------------------------------------------------------------------
# BaseParser.to_jsonl
# ---------------------------------------------------------------------------


class _ConcreteParser(BaseParser):
    """Minimal concrete subclass for testing BaseParser.to_jsonl."""

    def __init__(self, records):
        self._records = records

    def parse(self):
        return self._records


class TestBaseParserToJsonl:
    def test_produces_one_line_per_record(self):
        records = [_make_record(requirement_id=f"E8-test-ML1-{i:03d}") for i in range(3)]
        lines = _ConcreteParser(records).to_jsonl(records).splitlines()
        assert len(lines) == 3

    def test_each_line_is_valid_json_object(self):
        records = [_make_record()]
        for line in _ConcreteParser(records).to_jsonl(records).splitlines():
            obj = json.loads(line)
            assert isinstance(obj, dict)

    def test_roundtrip_preserves_requirement_id(self):
        rec = _make_record(requirement_id="E8-roundtrip-ML2-042")
        line = _ConcreteParser([rec]).to_jsonl([rec])
        assert json.loads(line)["requirement_id"] == "E8-roundtrip-ML2-042"

    def test_empty_records_produces_empty_string(self):
        result = _ConcreteParser([]).to_jsonl([])
        assert result == ""


class TestKeywordFiltering:
    def test_removes_noise_and_stopword_tokens(self):
        filtered = filter_keywords(
            [
                "account",
                "ro",
                "sp",
                "and",
                "def",
                "wireless",
                "pci",
                "dss",
                "v4",
                "cis",
                "v8",
                "guidelines",
                "defined",
                "documented",
                "examine",
                "requirement",
                "verify",
            ]
        )
        assert filtered == ["account", "wireless"]


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_lowercase_and_spaces_to_hyphens(self):
        assert _slugify("Patch Applications") == "patch-applications"

    def test_version_string(self):
        assert _slugify("November 2023") == "november-2023"

    def test_special_chars_collapsed(self):
        assert _slugify("Multi-factor Authentication!") == "multi-factor-authentication"

    def test_no_leading_or_trailing_hyphens(self):
        slug = _slugify("--test--")
        assert not slug.startswith("-")
        assert not slug.endswith("-")

    def test_all_lowercase(self):
        assert _slugify("UPPERCASE") == "uppercase"


# ---------------------------------------------------------------------------
# _normalise_family_name
# ---------------------------------------------------------------------------


class TestNormaliseFamilyName:
    def test_exact_match(self):
        assert _normalise_family_name("Patch applications") == "Patch applications"

    def test_case_insensitive_match(self):
        assert _normalise_family_name("PATCH APPLICATIONS") == "Patch applications"

    def test_whitespace_stripped(self):
        assert _normalise_family_name("  Patch applications  ") == "Patch applications"

    def test_prefix_match(self):
        # "Patch ap" starts with the first 8 chars of "Patch applications"
        assert _normalise_family_name("Patch app") == "Patch applications"

    def test_unknown_returns_none(self):
        assert _normalise_family_name("Completely Unknown Control Family") is None

    def test_empty_returns_none(self):
        assert _normalise_family_name("") is None

    @pytest.mark.parametrize("family", CONTROL_FAMILIES)
    def test_all_canonical_families_resolve(self, family):
        assert _normalise_family_name(family) == family


# ---------------------------------------------------------------------------
# _extract_introduction
# ---------------------------------------------------------------------------


class TestExtractIntroduction:
    def test_returns_empty_when_no_heading(self):
        result = _extract_introduction(_soup("<div><p>Just a paragraph.</p></div>"))
        assert result == ""

    def test_extracts_paragraph_after_intro_heading(self):
        html = "<div><h2>Introduction</h2><p>This is the intro.</p><h2>Next</h2></div>"
        result = _extract_introduction(_soup(html))
        assert "This is the intro" in result

    def test_stops_at_same_level_heading(self):
        html = (
            "<div><h2>Introduction</h2><p>Intro text.</p><h2>Overview</h2><p>Not intro.</p></div>"
        )
        result = _extract_introduction(_soup(html))
        assert "Intro text" in result
        assert "Not intro" not in result

    def test_includes_list_items_with_bullet(self):
        html = "<div><h3>Introduction</h3><ul><li>Item one</li></ul></div>"
        result = _extract_introduction(_soup(html))
        assert "Item one" in result
        assert "\u2022" in result  # bullet character

    def test_multiple_paragraphs_joined(self):
        html = "<div><h2>Introduction</h2><p>First.</p><p>Second.</p><h2>End</h2></div>"
        result = _extract_introduction(_soup(html))
        assert "First" in result
        assert "Second" in result


# ---------------------------------------------------------------------------
# _extract_cell_requirements
# ---------------------------------------------------------------------------


class TestExtractCellRequirements:
    def test_prefers_li_items(self):
        cell = _cell_soup("<ul><li>Requirement one</li><li>Requirement two</li></ul>")
        items = _extract_cell_requirements(cell)
        assert items == ["Requirement one", "Requirement two"]

    def test_falls_back_to_p_elements(self):
        cell = _cell_soup("<p>First requirement here in para.</p><p>Second requirement here.</p>")
        items = _extract_cell_requirements(cell)
        assert len(items) == 2
        assert "First requirement" in items[0]

    def test_fallback_to_plain_text_lines(self):
        cell = _cell_soup("This is a long enough requirement text on one line.")
        items = _extract_cell_requirements(cell)
        assert any("long enough" in i for i in items)

    def test_skips_lines_shorter_than_5_chars(self):
        # "ok" has only 2 chars, should be filtered out
        cell = _cell_soup("ok")
        assert _extract_cell_requirements(cell) == []

    def test_empty_li_skipped(self):
        cell = _cell_soup("<ul><li></li><li>Valid requirement statement here.</li></ul>")
        items = _extract_cell_requirements(cell)
        assert len(items) == 1


# ---------------------------------------------------------------------------
# _parse_requirement_table
# ---------------------------------------------------------------------------


def _req_table_html(family: str, requirements: list[str]) -> str:
    """Build a minimal two-column requirement table HTML string."""
    li_items = "".join(f"<li>{r}</li>" for r in requirements)
    return f"<table><tr><td>{family}</td><td><ul>{li_items}</ul></td></tr></table>"


class TestParseRequirementTable:
    def test_parses_basic_single_requirement(self):
        html = _req_table_html(
            "Patch applications", ["Apply patches within 48 hours of release when critical."]
        )
        records = _parse_requirement_table(
            _table_soup(html), 1, "https://example.com", "Appendix A", {}
        )
        assert len(records) == 1
        assert records[0].control_family == "Patch applications"
        assert records[0].maturity_level == 1

    def test_requirement_id_format_ml1(self):
        html = _req_table_html("Patch applications", ["Requirement statement that is long enough."])
        records = _parse_requirement_table(
            _table_soup(html), 1, "https://example.com", "Appendix A", {}
        )
        assert records[0].requirement_id == "E8-patch-applications-ML1-001"

    def test_requirement_id_format_ml2(self):
        html = _req_table_html(
            "Regular backups", ["Backups must be retained in a secure and resilient manner."]
        )
        records = _parse_requirement_table(
            _table_soup(html), 2, "https://example.com", "Appendix B", {}
        )
        assert records[0].requirement_id == "E8-regular-backups-ML2-001"

    def test_sequential_ids_within_family(self):
        reqs = [
            "First requirement statement that is long enough to parse.",
            "Second requirement statement that is also long enough.",
        ]
        html = _req_table_html("Regular backups", reqs)
        records = _parse_requirement_table(
            _table_soup(html), 1, "https://example.com", "Appendix A", {}
        )
        assert records[0].requirement_id.endswith("-001")
        assert records[1].requirement_id.endswith("-002")

    def test_skips_header_row_by_cell_value(self):
        html = "<table><tr><td>Mitigation strategy</td><td>Maturity Level One</td></tr></table>"
        records = _parse_requirement_table(
            _table_soup(html), 1, "https://example.com", "Appendix A", {}
        )
        assert records == []

    def test_guidance_text_injected_from_map(self):
        html = _req_table_html(
            "Patch applications", ["A long enough patch requirement statement here."]
        )
        guidance = {"Patch applications": "ASD guidance about patching."}
        records = _parse_requirement_table(
            _table_soup(html), 1, "https://example.com", "Appendix A", guidance
        )
        assert records[0].guidance_text == "ASD guidance about patching."

    def test_family_with_no_guidance_gets_empty_string(self):
        html = _req_table_html(
            "Patch applications", ["A long enough patch requirement statement here."]
        )
        records = _parse_requirement_table(
            _table_soup(html), 1, "https://example.com", "Appendix A", {}
        )
        assert records[0].guidance_text == ""

    def test_unknown_family_produces_no_records(self):
        html = "<table><tr><td>Unknown Control Family</td><td><ul><li>A sufficiently long requirement statement.</li></ul></td></tr></table>"
        records = _parse_requirement_table(
            _table_soup(html), 1, "https://example.com", "Appendix A", {}
        )
        assert records == []

    def test_keywords_assigned_per_family(self):
        html = _req_table_html(
            "Multi-factor authentication", ["MFA must be used for all privileged user accounts."]
        )
        records = _parse_requirement_table(
            _table_soup(html), 1, "https://example.com", "Appendix A", {}
        )
        assert "MFA" in records[0].keywords

    def test_provenance_fields_set_correctly(self):
        html = _req_table_html("Patch applications", ["A long enough patch requirement statement."])
        records = _parse_requirement_table(
            _table_soup(html), 1, "https://test.example", "Appendix A – Section", {}
        )
        assert records[0].source_uri == "https://test.example"
        assert records[0].source_section == "Appendix A – Section"
        assert records[0].framework == "Essential Eight"
        assert records[0].jurisdiction_or_scope == "Australia"


# ---------------------------------------------------------------------------
# _parse_maturity_model_page
# ---------------------------------------------------------------------------


def _model_page_html(appendix_letter: str, family: str, req: str) -> str:
    """Build a minimal maturity model page with one appendix heading and one table."""
    return f"""
    <main>
        <h2>Appendix {appendix_letter.upper()} \u2013 Maturity Level</h2>
        <table>
            <tr>
                <td>{family}</td>
                <td><ul><li>{req}</li></ul></td>
            </tr>
        </table>
    </main>
    """


class TestParseMaturityModelPage:
    def test_extracts_records_from_appendix_a(self):
        html = _model_page_html(
            "a",
            "Patch applications",
            "Apply patches within 48 hours when vulnerabilities are critical.",
        )
        records = _parse_maturity_model_page(_soup(html), "https://example.com", {})
        assert len(records) >= 1
        assert records[0].maturity_level == 1

    def test_extracts_records_from_appendix_b(self):
        html = _model_page_html(
            "b", "Regular backups", "Backups must be retained in a secure and resilient manner."
        )
        records = _parse_maturity_model_page(_soup(html), "https://example.com", {})
        assert len(records) >= 1
        assert records[0].maturity_level == 2

    def test_extracts_records_from_appendix_c(self):
        html = _model_page_html(
            "c",
            "Application control",
            "Application control is implemented on all servers and workstations.",
        )
        records = _parse_maturity_model_page(_soup(html), "https://example.com", {})
        assert len(records) >= 1
        assert records[0].maturity_level == 3

    def test_ignores_tables_before_any_appendix_heading(self):
        html = """
        <main>
            <h2>Introduction</h2>
            <table>
                <tr><td>Patch applications</td><td><ul><li>Should not be extracted from here.</li></ul></td></tr>
            </table>
            <h2>Appendix A \u2013 Maturity Level</h2>
            <table>
                <tr><td>Patch applications</td><td><ul><li>Apply patches within 48 hours when critical vulnerabilities exist.</li></ul></td></tr>
            </table>
        </main>
        """
        records = _parse_maturity_model_page(_soup(html), "https://example.com", {})
        assert len(records) == 1

    def test_returns_empty_for_no_appendix_headings(self):
        html = "<main><h2>Overview</h2><table><tr><td>Patch applications</td><td><ul><li>Ignored.</li></ul></td></tr></table></main>"
        records = _parse_maturity_model_page(_soup(html), "https://example.com", {})
        assert records == []

    def test_source_uri_propagated_to_all_records(self):
        html = _model_page_html(
            "a",
            "Patch applications",
            "Apply patches within 48 hours when critical vulnerabilities exist.",
        )
        records = _parse_maturity_model_page(_soup(html), "https://canonical.example.com", {})
        assert all(r.source_uri == "https://canonical.example.com" for r in records)


# ---------------------------------------------------------------------------
# EssentialEightParser (mocked HTTP)
# ---------------------------------------------------------------------------

_MINIMAL_MODEL_HTML = """
<main>
    <h2>Appendix A \u2013 Maturity Level One</h2>
    <table>
        <tr>
            <td>Patch applications</td>
            <td>
                <ul>
                    <li>Apply patches within 48 hours of release when vulnerabilities are assessed as critical.</li>
                    <li>Remove applications that are no longer supported by vendors.</li>
                </ul>
            </td>
        </tr>
    </table>
    <h2>Appendix B \u2013 Maturity Level Two</h2>
    <table>
        <tr>
            <td>Regular backups</td>
            <td><ul><li>Backups of data are retained in a secure and resilient manner.</li></ul></td>
        </tr>
    </table>
</main>
"""

_PATCH_PATH = "runtime.ingestion.parsers.essential_eight._fetch_soup"


class TestEssentialEightParser:
    def test_returns_list_of_requirement_records(self):
        model_soup = BeautifulSoup(_MINIMAL_MODEL_HTML, "html.parser")
        with patch(_PATCH_PATH, return_value=model_soup):
            records = EssentialEightParser(fetch_guidance=False).parse()
        assert isinstance(records, list)
        assert len(records) > 0
        assert all(isinstance(r, RequirementRecord) for r in records)

    def test_no_guidance_fetches_only_main_url(self):
        model_soup = BeautifulSoup(_MINIMAL_MODEL_HTML, "html.parser")
        with patch(_PATCH_PATH, return_value=model_soup) as mock_fetch:
            EssentialEightParser(fetch_guidance=False).parse()
        assert mock_fetch.call_count == 1

    def test_returns_correct_framework_and_version(self):
        model_soup = BeautifulSoup(_MINIMAL_MODEL_HTML, "html.parser")
        with patch(_PATCH_PATH, return_value=model_soup):
            records = EssentialEightParser(fetch_guidance=False).parse()
        assert all(r.framework == "Essential Eight" for r in records)
        assert all(r.framework_version == "November 2023" for r in records)

    def test_records_span_multiple_maturity_levels(self):
        model_soup = BeautifulSoup(_MINIMAL_MODEL_HTML, "html.parser")
        with patch(_PATCH_PATH, return_value=model_soup):
            records = EssentialEightParser(fetch_guidance=False).parse()
        levels = {r.maturity_level for r in records}
        assert 1 in levels
        assert 2 in levels

    def test_empty_page_returns_empty_list(self):
        empty_soup = BeautifulSoup("<main><p>No tables.</p></main>", "html.parser")
        with patch(_PATCH_PATH, return_value=empty_soup):
            records = EssentialEightParser(fetch_guidance=False).parse()
        assert records == []

    def test_all_ids_are_unique(self):
        model_soup = BeautifulSoup(_MINIMAL_MODEL_HTML, "html.parser")
        with patch(_PATCH_PATH, return_value=model_soup):
            records = EssentialEightParser(fetch_guidance=False).parse()
        ids = [r.requirement_id for r in records]
        assert len(ids) == len(set(ids))

    def test_with_guidance_fetches_guidance_urls(self):
        model_soup = BeautifulSoup(_MINIMAL_MODEL_HTML, "html.parser")
        guidance_soup = BeautifulSoup(
            "<div><h2>Introduction</h2><p>Guidance prose.</p></div>", "html.parser"
        )
        # First call returns model page; subsequent calls return guidance page
        call_results = [model_soup] + [guidance_soup] * 10
        with patch(_PATCH_PATH, side_effect=call_results) as mock_fetch:
            EssentialEightParser(fetch_guidance=True).parse()
        # Should have fetched more than just the main URL
        assert mock_fetch.call_count > 1


# ---------------------------------------------------------------------------
# CIS Controls parser
# ---------------------------------------------------------------------------


class _FakePdfPage:
    def __init__(self, text: str) -> None:
        self._text = text

    def extract_text(self) -> str:
        return self._text


class _FakePdfReader:
    def __init__(self, _path: str) -> None:
        self.pages = [
            _FakePdfPage("""
01
 Inventory and Control of Enterprise Assets
OVERVIEW  Manage enterprise assets.
Why is this Control critical?
You cannot defend assets you do not know you have.
"""),
            _FakePdfPage("""
Control 01: Inventory and Control of Enterprise Assets
Procedures and tools
Use inventories, scanners, and supporting processes.
Safeguards
"""),
            _FakePdfPage("""
02
 Inventory and Control of Software Assets
OVERVIEW  Manage software assets.
Why is this Control critical?
Vulnerable software expands attack surface.
"""),
        ]


class TestCisControlsParser:
    def test_build_control_guidance_map_extracts_sections(self, tmp_path: Path):
        pdf_path = tmp_path / "cis.pdf"
        pdf_path.write_bytes(b"placeholder")
        with patch("runtime.ingestion.parsers.cis_controls._PdfReader", _FakePdfReader):
            guidance = _build_control_guidance_map(pdf_path)

        assert "1" in guidance
        assert "Overview: Manage enterprise assets." in guidance["1"]
        assert "Why critical: You cannot defend assets you do not know you have." in guidance["1"]
        assert (
            "Procedures and tools: Use inventories, scanners, and supporting processes."
            in guidance["1"]
        )

    def test_parse_workbook_rows_into_requirement_records(self, tmp_path: Path):
        import openpyxl

        workbook_path = tmp_path / "cis.xlsx"
        pdf_path = tmp_path / "cis.pdf"
        pdf_path.write_bytes(b"placeholder")

        wb = openpyxl.Workbook()
        ws = cast(Any, wb.active)
        ws.title = "Controls V8"
        ws.append(
            [
                "CIS Control",
                "CIS Safeguard",
                "Asset Type",
                "Security Function",
                "Title",
                "Description",
                "IG1",
                "IG2",
                "IG3",
            ]
        )
        ws.append(
            [
                "1",
                "",
                "",
                "",
                "Inventory and Control of Enterprise Assets",
                "Header description for control 1.",
                "",
                "",
                "",
            ]
        )
        ws.append(
            [
                "1",
                "1.1",
                "Devices",
                "Identify",
                "Establish and Maintain Detailed Enterprise Asset Inventory",
                "Maintain an accurate inventory.",
                "x",
                "x",
                "x",
            ]
        )
        ws.append(
            [
                "1",
                "1.2",
                "Devices",
                "Respond",
                "Address Unauthorized Assets",
                "Address unauthorized assets weekly.",
                "",
                "x",
                "x",
            ]
        )
        ws.append(
            [
                "1",
                "1.3",
                "Devices",
                "Protect",
                "Use an Active Discovery Tool",
                "Use active discovery to detect unmanaged assets.",
                "",
                "",
                "x",
            ]
        )
        ws.append(
            [
                "1",
                "1.4",
                "Devices",
                "Protect",
                "Maintain Asset Metadata",
                "Maintain metadata for managed assets.",
                "",
                "",
                "",
            ]
        )
        wb.save(workbook_path)

        with patch("runtime.ingestion.parsers.cis_controls._PdfReader", _FakePdfReader):
            records = CisControlsParser(workbook_path=workbook_path, pdf_path=pdf_path).parse()

        assert len(records) == 4
        assert all(isinstance(record, RequirementRecord) for record in records)
        assert records[0].framework == "CIS Controls"
        assert records[0].framework_version == "v8"
        assert records[0].requirement_id == "CISv8-1_1"
        assert records[0].control_family == "Inventory and Control of Enterprise Assets"
        assert records[0].source_section == "Control 01 > Safeguard 1.1"
        assert "ig1" in records[0].keywords
        assert records[0].maturity_level == 1
        assert records[1].maturity_level == 2
        assert records[2].maturity_level == 3
        assert records[3].maturity_level is None
        assert records[0].guidance_text

    def test_parse_real_samples_populates_maturity_levels(self):
        workspace_root = Path(__file__).resolve().parents[2]
        workbook_path = workspace_root / "runtime" / "samples" / "CIS_Controls_Version_8.xlsx"
        pdf_path = (
            workspace_root
            / "runtime"
            / "samples"
            / "CIS_Controls__v8__Critical_Security_Controls__2023_08.pdf"
        )

        if not workbook_path.exists() or not pdf_path.exists():
            pytest.skip("CIS sample files are not present in runtime/samples")

        records = CisControlsParser(workbook_path=workbook_path, pdf_path=pdf_path).parse()

        assert records, "Expected CIS parser to return records"
        assert all(record.maturity_level in {1, 2, 3} for record in records)
        assert all("\n" not in record.guidance_text for record in records)


# ---------------------------------------------------------------------------
# PCI DSS parser
# ---------------------------------------------------------------------------

# Minimal synthetic PDF page text that mirrors the PCI DSS column layout.
_PCI_FAKE_PAGE = """\
Requirements and Testing Procedures Guidance
1.1 Processes and mechanisms for installing and maintaining network security controls are defined and understood.
Defined Approach Requirements Defined Approach Testing Procedures Purpose
Requirement 1.1.1 is about managing policies in Requirement 1.
Good Practice
Policies should be reviewed regularly.
1.1.1 All security policies and operational procedures that are identified in Requirement 1 are:
\u2022 Documented.
\u2022 Kept up to date.
1.1.1 Examine documentation and interview personnel to verify that security policies are managed in accordance with this requirement.
Customized Approach Objective
Expectations and oversight for Requirement 1 are defined and adhered to by affected personnel.
1.2 Network security controls (NSCs) are configured and maintained.
Defined Approach Requirements Defined Approach Testing Procedures Purpose
NSCs control traffic flowing inbound and outbound from the CDE.
1.2.1 Configuration standards for NSC rulesets are:
\u2022 Defined.
\u2022 Implemented.
1.2.1 Examine the configuration standards for NSC rulesets to verify they are in accordance with this requirement.
Customized Approach Objective
The way that NSCs are configured and operate are defined and consistently applied.
"""


class _FakePciPdfReader:
    """Minimal fake PdfReader that yields one synthetic PCI DSS page."""

    def __init__(self, _path: str) -> None:
        pass

    class _FakePage:
        def __init__(self, text: str) -> None:
            self._text = text

        def extract_text(self) -> str:
            return self._text

    @property
    def pages(self):
        return [self._FakePage(_PCI_FAKE_PAGE)]


class TestPciDssParser:
    def test_extracts_requirement_text(self, tmp_path: Path):
        pdf_path = tmp_path / "pci.pdf"
        pdf_path.write_bytes(b"placeholder")

        with patch("runtime.ingestion.parsers.pci_dss._PdfReader", _FakePciPdfReader):
            records = PciDssParser(pdf_path=pdf_path).parse()

        assert len(records) == 2
        ids = {r.requirement_id for r in records}
        assert "PCIDSS-1_1_1" in ids
        assert "PCIDSS-1_2_1" in ids

    def test_skips_testing_procedures(self, tmp_path: Path):
        pdf_path = tmp_path / "pci.pdf"
        pdf_path.write_bytes(b"placeholder")

        with patch("runtime.ingestion.parsers.pci_dss._PdfReader", _FakePciPdfReader):
            records = PciDssParser(pdf_path=pdf_path).parse()

        # Testing procedures start with "Examine …" — none should appear in requirement_text
        for rec in records:
            assert not rec.requirement_text.lower().startswith("examine")

        rec_1_1_1 = next(r for r in records if r.requirement_id == "PCIDSS-1_1_1")
        rec_1_2_1 = next(r for r in records if r.requirement_id == "PCIDSS-1_2_1")
        assert "Examine documentation and interview personnel" not in rec_1_1_1.requirement_text
        assert "Examine the configuration standards" not in rec_1_2_1.requirement_text

    def test_requirement_fields(self, tmp_path: Path):
        pdf_path = tmp_path / "pci.pdf"
        pdf_path.write_bytes(b"placeholder")

        with patch("runtime.ingestion.parsers.pci_dss._PdfReader", _FakePciPdfReader):
            records = PciDssParser(pdf_path=pdf_path).parse()

        rec = next(r for r in records if r.requirement_id == "PCIDSS-1_1_1")
        assert rec.framework == "PCI DSS"
        assert rec.framework_version == "v4.0.1"
        assert rec.control_family == "Install and Maintain Network Security Controls"
        assert rec.maturity_level is None
        assert rec.source_section == "Requirement 1 > 1.1 > 1.1.1"
        assert "Documented" in rec.requirement_text
        assert rec.guidance_text  # section-level guidance should be captured
        assert "and" not in rec.keywords
        assert "the" not in rec.keywords

    def test_no_newlines_in_text_fields(self, tmp_path: Path):
        pdf_path = tmp_path / "pci.pdf"
        pdf_path.write_bytes(b"placeholder")

        with patch("runtime.ingestion.parsers.pci_dss._PdfReader", _FakePciPdfReader):
            records = PciDssParser(pdf_path=pdf_path).parse()

        for rec in records:
            assert "\n" not in rec.requirement_text
            assert "\n" not in rec.guidance_text

    def test_parse_real_sample(self):
        workspace_root = Path(__file__).resolve().parents[2]
        pdf_path = workspace_root / "runtime" / "samples" / "PCI-DSS-v4_0_1.pdf"

        if not pdf_path.exists():
            pytest.skip("PCI-DSS-v4_0_1.pdf is not present in runtime/samples")

        records = PciDssParser(pdf_path=pdf_path).parse()

        assert len(records) >= 200, f"Expected 200+ PCI DSS requirements, got {len(records)}"
        assert all(r.framework == "PCI DSS" for r in records)
        assert all(r.maturity_level is None for r in records)
        assert all("\n" not in r.requirement_text for r in records)
        assert all("\n" not in r.guidance_text for r in records)
        # Every record should map to a known top-level requirement
        assert all(r.requirement_id.startswith("PCIDSS-") for r in records)


# ---------------------------------------------------------------------------
# PSPF parser
# ---------------------------------------------------------------------------

_PSPF_FAKE_TEXT = """\
1 Whole of Government Protective Security Roles
1.1 Departments of State
Departments of State provide leadership and guidance to supported entities.
Requirement 0001 | GOV | Department of State | 31 October 2024
The Department of State supports portfolio entities to achieve and maintain an acceptable level of protective security.
2 Entity Protective Security Roles and Responsibilities
2.3 Chief Information Security Officer
The Chief Information Security Officer provides cyber security leadership within the entity.
Requirement 0011 | GOV | All entities | 01 July 2025
A Chief Information Security Officer is appointed to oversee the entity's cyber security program and most critical technology resources.
Table 1: Ignored table content
This line should not be captured in the requirement body.
"""


class TestPspfParser:
    def test_control_specific_keyword_tuning_adds_aliases(self):
        ciso_keywords = _pspf_keywords(
            "GOV",
            "All entities",
            {
                0: ("2", "Entity Protective Security Roles and Responsibilities"),
                1: ("2.3", "Chief Information Security Officer"),
            },
            "A Chief Information Security Officer is appointed to oversee the entity's cyber security program.",
        )
        sogs_keywords = _pspf_keywords(
            "TECH",
            "System of Government Significance",
            {
                0: ("15", "Cyber Security Programs"),
                1: ("15.7", "Systems of Government Significance"),
            },
            "Declared Systems of Government Significance are protected in accordance with the standard.",
        )

        assert "ciso" in ciso_keywords
        assert "cyber" in ciso_keywords
        assert "sogs" in sogs_keywords
        assert "critical" in sogs_keywords

    def test_parse_release_text_extracts_requirements_and_guidance(self):
        records = _parse_pspf_release_text(_PSPF_FAKE_TEXT)

        assert len(records) == 2
        assert records[0].requirement_id == "PSPF-0001"
        assert records[0].framework == "PSPF"
        assert records[0].control_family == "Departments of State"
        assert (
            records[0].guidance_text
            == "Departments of State provide leadership and guidance to supported entities."
        )
        assert records[0].effective_date == "31 October 2024"
        assert records[1].control_family == "Chief Information Security Officer"
        assert (
            records[1].source_section
            == "GOV > 2 Entity Protective Security Roles and Responsibilities > 2.3 Chief Information Security Officer > Requirement 11"
        )
        assert "Ignored table content" not in records[1].requirement_text

    def test_parse_uses_downloaded_pdf_content(self):
        with patch("runtime.ingestion.parsers.pspf._download_pdf_bytes", return_value=b"pdf"):
            with patch(
                "runtime.ingestion.parsers.pspf._extract_full_text", return_value=_PSPF_FAKE_TEXT
            ):
                records = PspfParser().parse()

        assert len(records) == 2
        assert all(record.framework == "PSPF" for record in records)
        assert all(record.maturity_level is None for record in records)


# ---------------------------------------------------------------------------
# load_controls_jsonl
# ---------------------------------------------------------------------------


class TestLoadControlsJsonl:
    def test_loads_single_valid_record(self, tmp_path: Path):
        p = tmp_path / "controls.jsonl"
        p.write_text(json.dumps(_minimal_jsonl_dict()) + "\n", encoding="utf-8")
        records = load_controls_jsonl(p)
        assert len(records) == 1
        assert records[0]["requirement_id"] == "E8-test-ML1-001"

    def test_loads_multiple_records(self, tmp_path: Path):
        rows = [_minimal_jsonl_dict(requirement_id=f"E8-test-ML1-{i:03d}") for i in range(5)]
        p = tmp_path / "multi.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        assert len(load_controls_jsonl(p)) == 5

    def test_skips_blank_lines(self, tmp_path: Path):
        p = tmp_path / "blanks.jsonl"
        p.write_text("\n" + json.dumps(_minimal_jsonl_dict()) + "\n\n", encoding="utf-8")
        assert len(load_controls_jsonl(p)) == 1

    def test_raises_on_invalid_json(self, tmp_path: Path):
        p = tmp_path / "bad.jsonl"
        p.write_text("{not valid json}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid JSONL"):
            load_controls_jsonl(p)

    def test_raises_on_missing_required_field(self, tmp_path: Path):
        record = _minimal_jsonl_dict()
        del record["requirement_text"]
        p = tmp_path / "missing.jsonl"
        p.write_text(json.dumps(record) + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="missing fields"):
            load_controls_jsonl(p)

    def test_raises_on_empty_file(self, tmp_path: Path):
        p = tmp_path / "empty.jsonl"
        p.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="No records found"):
            load_controls_jsonl(p)

    def test_error_message_names_missing_field(self, tmp_path: Path):
        record = _minimal_jsonl_dict()
        del record["framework_version"]
        del record["source_uri"]
        p = tmp_path / "missing2.jsonl"
        p.write_text(json.dumps(record) + "\n", encoding="utf-8")
        with pytest.raises(ValueError) as exc_info:
            load_controls_jsonl(p)
        msg = str(exc_info.value)
        assert "framework_version" in msg or "source_uri" in msg


# ---------------------------------------------------------------------------
# _batched
# ---------------------------------------------------------------------------


def _dicts(n: int) -> list[dict[str, Any]]:
    """Build *n* minimal distinct dicts for use as _batched test data."""
    return [{"i": i} for i in range(n)]


class TestBatched:
    def test_splits_into_equal_batches(self):
        items = _dicts(10)
        assert _batched(items, 5) == [items[:5], items[5:]]

    def test_last_batch_has_remainder(self):
        batches = _batched(_dicts(7), 3)
        assert len(batches) == 3
        assert batches[-1] == [{"i": 6}]

    def test_empty_list_returns_empty(self):
        result: list[list[dict[str, Any]]] = _batched([], 10)
        assert result == []

    def test_batch_larger_than_list(self):
        items = _dicts(2)
        assert _batched(items, 100) == [items]

    def test_batch_size_one(self):
        items = _dicts(3)
        assert _batched(items, 1) == [[items[0]], [items[1]], [items[2]]]

    def test_no_items_lost(self):
        items = _dicts(23)
        batches = _batched(items, 7)
        flat = [item for batch in batches for item in batch]
        assert flat == items
