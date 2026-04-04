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
from typing import Any
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup

from runtime.ingestion.parsers.base import BaseParser, RequirementRecord
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
        html = "<div><h2>Introduction</h2><p>Intro text.</p><h2>Overview</h2><p>Not intro.</p></div>"
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
        html = _req_table_html("Patch applications", ["Apply patches within 48 hours of release when critical."])
        records = _parse_requirement_table(_table_soup(html), 1, "https://example.com", "Appendix A", {})
        assert len(records) == 1
        assert records[0].control_family == "Patch applications"
        assert records[0].maturity_level == 1

    def test_requirement_id_format_ml1(self):
        html = _req_table_html("Patch applications", ["Requirement statement that is long enough."])
        records = _parse_requirement_table(_table_soup(html), 1, "https://example.com", "Appendix A", {})
        assert records[0].requirement_id == "E8-patch-applications-ML1-001"

    def test_requirement_id_format_ml2(self):
        html = _req_table_html("Regular backups", ["Backups must be retained in a secure and resilient manner."])
        records = _parse_requirement_table(_table_soup(html), 2, "https://example.com", "Appendix B", {})
        assert records[0].requirement_id == "E8-regular-backups-ML2-001"

    def test_sequential_ids_within_family(self):
        reqs = [
            "First requirement statement that is long enough to parse.",
            "Second requirement statement that is also long enough.",
        ]
        html = _req_table_html("Regular backups", reqs)
        records = _parse_requirement_table(_table_soup(html), 1, "https://example.com", "Appendix A", {})
        assert records[0].requirement_id.endswith("-001")
        assert records[1].requirement_id.endswith("-002")

    def test_skips_header_row_by_cell_value(self):
        html = "<table><tr><td>Mitigation strategy</td><td>Maturity Level One</td></tr></table>"
        records = _parse_requirement_table(_table_soup(html), 1, "https://example.com", "Appendix A", {})
        assert records == []

    def test_guidance_text_injected_from_map(self):
        html = _req_table_html("Patch applications", ["A long enough patch requirement statement here."])
        guidance = {"Patch applications": "ASD guidance about patching."}
        records = _parse_requirement_table(_table_soup(html), 1, "https://example.com", "Appendix A", guidance)
        assert records[0].guidance_text == "ASD guidance about patching."

    def test_family_with_no_guidance_gets_empty_string(self):
        html = _req_table_html("Patch applications", ["A long enough patch requirement statement here."])
        records = _parse_requirement_table(_table_soup(html), 1, "https://example.com", "Appendix A", {})
        assert records[0].guidance_text == ""

    def test_unknown_family_produces_no_records(self):
        html = "<table><tr><td>Unknown Control Family</td><td><ul><li>A sufficiently long requirement statement.</li></ul></td></tr></table>"
        records = _parse_requirement_table(_table_soup(html), 1, "https://example.com", "Appendix A", {})
        assert records == []

    def test_keywords_assigned_per_family(self):
        html = _req_table_html("Multi-factor authentication", ["MFA must be used for all privileged user accounts."])
        records = _parse_requirement_table(_table_soup(html), 1, "https://example.com", "Appendix A", {})
        assert "MFA" in records[0].keywords

    def test_provenance_fields_set_correctly(self):
        html = _req_table_html("Patch applications", ["A long enough patch requirement statement."])
        records = _parse_requirement_table(_table_soup(html), 1, "https://test.example", "Appendix A – Section", {})
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
        html = _model_page_html("a", "Patch applications", "Apply patches within 48 hours when vulnerabilities are critical.")
        records = _parse_maturity_model_page(_soup(html), "https://example.com", {})
        assert len(records) >= 1
        assert records[0].maturity_level == 1

    def test_extracts_records_from_appendix_b(self):
        html = _model_page_html("b", "Regular backups", "Backups must be retained in a secure and resilient manner.")
        records = _parse_maturity_model_page(_soup(html), "https://example.com", {})
        assert len(records) >= 1
        assert records[0].maturity_level == 2

    def test_extracts_records_from_appendix_c(self):
        html = _model_page_html("c", "Application control", "Application control is implemented on all servers and workstations.")
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
        html = _model_page_html("a", "Patch applications", "Apply patches within 48 hours when critical vulnerabilities exist.")
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
        guidance_soup = BeautifulSoup("<div><h2>Introduction</h2><p>Guidance prose.</p></div>", "html.parser")
        # First call returns model page; subsequent calls return guidance page
        call_results = [model_soup] + [guidance_soup] * 10
        with patch(_PATCH_PATH, side_effect=call_results) as mock_fetch:
            EssentialEightParser(fetch_guidance=True).parse()
        # Should have fetched more than just the main URL
        assert mock_fetch.call_count > 1


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
