from __future__ import annotations

from runtime.assessment_orchestration.assessment_runtime import (
    _infer_framework_filter,
    _parse_framework_authority_order,
)


def test_parse_framework_authority_order_defaults_include_all_supported_frameworks() -> None:
    assert _parse_framework_authority_order(None) == (
        "Essential Eight",
        "ISM",
        "AESCSF",
        "NIST CSF",
        "PSPF",
        "PCI DSS",
        "CIS Controls",
    )


def test_parse_framework_authority_order_recognises_aliases_for_all_supported_frameworks() -> None:
    parsed = _parse_framework_authority_order("pspf,pci_dss,cis_controls,essential_eight")
    assert parsed == ("PSPF", "PCI DSS", "CIS Controls", "Essential Eight")


def test_infer_framework_filter_detects_pspf_pci_and_cis() -> None:
    assert _infer_framework_filter("Review this page against PSPF controls") == "PSPF"
    assert _infer_framework_filter("Run a PCI DSS v4 review") == "PCI DSS"
    assert _infer_framework_filter("Assess this against CIS Controls") == "CIS Controls"
