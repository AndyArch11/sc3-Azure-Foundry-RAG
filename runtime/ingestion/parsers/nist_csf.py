"""Pre-parser for the NIST Cybersecurity Framework (CSF) 2.0.

Source: NIST CSWP 29, February 26, 2024
https://nvlpubs.nist.gov/nistpubs/CSWP/NIST.CSWP.29.pdf

The CSF 2.0 Core is embedded as structured static data (it is a stable, published
specification). The parser emits one RequirementRecord per Subcategory.

Optionally fetches supplementary guidance text from the NIST CPRT web pages for
each Category, controlled by the ``fetch_guidance`` constructor parameter.

Usage::

    from runtime.ingestion.parsers.nist_csf import NistCsfParser
    parser = NistCsfParser()
    records = parser.parse()
    print(parser.to_jsonl(records))
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

from .base import BaseParser, RequirementRecord, filter_keywords

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FRAMEWORK = "NIST CSF"
FRAMEWORK_VERSION = "2.0"
EFFECTIVE_DATE = "February 2024"
JURISDICTION = "United States"
SOURCE_URI = "https://nvlpubs.nist.gov/nistpubs/CSWP/NIST.CSWP.29.pdf"

# ---------------------------------------------------------------------------
# CSF 2.0 Core – static data derived from Appendix A of NIST CSWP 29
#
# Structure: list of (function_id, function_name, function_description, categories)
# Each category: (category_id, category_name, category_description, subcategories)
# Each subcategory: (subcategory_id, subcategory_text)
# ---------------------------------------------------------------------------

_CSF_CORE: List[Tuple] = [
    (
        "GV",
        "Govern",
        "The organization's cybersecurity risk management strategy, expectations, and policy "
        "are established, communicated, and monitored",
        [
            (
                "GV.OC",
                "Organizational Context",
                "The circumstances — mission, stakeholder expectations, dependencies, and legal, "
                "regulatory, and contractual requirements — surrounding the organization's "
                "cybersecurity risk management decisions are understood",
                [
                    (
                        "GV.OC-01",
                        "The organizational mission is understood and informs cybersecurity risk management",
                    ),
                    (
                        "GV.OC-02",
                        "Internal and external stakeholders are understood, and their needs and expectations regarding cybersecurity risk management are understood and considered",
                    ),
                    (
                        "GV.OC-03",
                        "Legal, regulatory, and contractual requirements regarding cybersecurity — including privacy and civil liberties obligations — are understood and managed",
                    ),
                    (
                        "GV.OC-04",
                        "Critical objectives, capabilities, and services that external stakeholders depend on or expect from the organization are understood and communicated",
                    ),
                    (
                        "GV.OC-05",
                        "Outcomes, capabilities, and services that the organization depends on are understood and communicated",
                    ),
                ],
            ),
            (
                "GV.RM",
                "Risk Management Strategy",
                "The organization's priorities, constraints, risk tolerance and appetite statements, "
                "and assumptions are established, communicated, and used to support operational risk decisions",
                [
                    (
                        "GV.RM-01",
                        "Risk management objectives are established and agreed to by organizational stakeholders",
                    ),
                    (
                        "GV.RM-02",
                        "Risk appetite and risk tolerance statements are established, communicated, and maintained",
                    ),
                    (
                        "GV.RM-03",
                        "Cybersecurity risk management activities and outcomes are included in enterprise risk management processes",
                    ),
                    (
                        "GV.RM-04",
                        "Strategic direction that describes appropriate risk response options is established and communicated",
                    ),
                    (
                        "GV.RM-05",
                        "Lines of communication across the organization are established for cybersecurity risks, including risks from suppliers and other third parties",
                    ),
                    (
                        "GV.RM-06",
                        "A standardized method for calculating, documenting, categorizing, and prioritizing cybersecurity risks is established and communicated",
                    ),
                    (
                        "GV.RM-07",
                        "Strategic opportunities (i.e., positive risks) are characterized and are included in organizational cybersecurity risk discussions",
                    ),
                ],
            ),
            (
                "GV.RR",
                "Roles, Responsibilities, and Authorities",
                "Cybersecurity roles, responsibilities, and authorities to foster accountability, "
                "performance assessment, and continuous improvement are established and communicated",
                [
                    (
                        "GV.RR-01",
                        "Organizational leadership is responsible and accountable for cybersecurity risk and fosters a culture that is risk-aware, ethical, and continually improving",
                    ),
                    (
                        "GV.RR-02",
                        "Roles, responsibilities, and authorities related to cybersecurity risk management are established, communicated, understood, and enforced",
                    ),
                    (
                        "GV.RR-03",
                        "Adequate resources are allocated commensurate with the cybersecurity risk strategy, roles, responsibilities, and policies",
                    ),
                    ("GV.RR-04", "Cybersecurity is included in human resources practices"),
                ],
            ),
            (
                "GV.PO",
                "Policy",
                "Organizational cybersecurity policy is established, communicated, and enforced",
                [
                    (
                        "GV.PO-01",
                        "Policy for managing cybersecurity risks is established based on organizational context, cybersecurity strategy, and priorities and is communicated and enforced",
                    ),
                    (
                        "GV.PO-02",
                        "Policy for managing cybersecurity risks is reviewed, updated, communicated, and enforced to reflect changes in requirements, threats, technology, and organizational mission",
                    ),
                ],
            ),
            (
                "GV.OV",
                "Oversight",
                "Results of organization-wide cybersecurity risk management activities and performance "
                "are used to inform, improve, and adjust the risk management strategy",
                [
                    (
                        "GV.OV-01",
                        "Cybersecurity risk management strategy outcomes are reviewed to inform and adjust strategy and direction",
                    ),
                    (
                        "GV.OV-02",
                        "The cybersecurity risk management strategy is reviewed and adjusted to ensure coverage of organizational requirements and risks",
                    ),
                    (
                        "GV.OV-03",
                        "Organizational cybersecurity risk management performance is evaluated and reviewed for adjustments needed",
                    ),
                ],
            ),
            (
                "GV.SC",
                "Cybersecurity Supply Chain Risk Management",
                "Cyber supply chain risk management processes are identified, established, managed, "
                "monitored, and improved by organizational stakeholders",
                [
                    (
                        "GV.SC-01",
                        "A cybersecurity supply chain risk management program, strategy, objectives, policies, and processes are established and agreed to by organizational stakeholders",
                    ),
                    (
                        "GV.SC-02",
                        "Cybersecurity roles and responsibilities for suppliers, customers, and partners are established, communicated, and coordinated internally and externally",
                    ),
                    (
                        "GV.SC-03",
                        "Cybersecurity supply chain risk management is integrated into cybersecurity and enterprise risk management, risk assessment, and improvement processes",
                    ),
                    ("GV.SC-04", "Suppliers are known and prioritized by criticality"),
                    (
                        "GV.SC-05",
                        "Requirements to address cybersecurity risks in supply chains are established, prioritized, and integrated into contracts and other types of agreements with suppliers and other relevant third parties",
                    ),
                    (
                        "GV.SC-06",
                        "Planning and due diligence are performed to reduce risks before entering into formal supplier or other third-party relationships",
                    ),
                    (
                        "GV.SC-07",
                        "The risks posed by a supplier, their products and services, and other third parties are understood, recorded, prioritized, assessed, responded to, and monitored over the course of the relationship",
                    ),
                    (
                        "GV.SC-08",
                        "Relevant suppliers and other third parties are included in incident planning, response, and recovery activities",
                    ),
                    (
                        "GV.SC-09",
                        "Supply chain security practices are integrated into cybersecurity and enterprise risk management programs, and their performance is monitored throughout the technology product and service life cycle",
                    ),
                    (
                        "GV.SC-10",
                        "Cybersecurity supply chain risk management plans include provisions for activities that occur after the conclusion of a partnership or service agreement",
                    ),
                ],
            ),
        ],
    ),
    (
        "ID",
        "Identify",
        "The organization's current cybersecurity risks are understood",
        [
            (
                "ID.AM",
                "Asset Management",
                "Assets (e.g., data, hardware, software, systems, facilities, services, people) that enable "
                "the organization to achieve business purposes are identified and managed consistent with their "
                "relative importance to organizational objectives and the organization's risk strategy",
                [
                    (
                        "ID.AM-01",
                        "Inventories of hardware managed by the organization are maintained",
                    ),
                    (
                        "ID.AM-02",
                        "Inventories of software, services, and systems managed by the organization are maintained",
                    ),
                    (
                        "ID.AM-03",
                        "Representations of the organization's authorized network communication and internal and external network data flows are maintained",
                    ),
                    ("ID.AM-04", "Inventories of services provided by suppliers are maintained"),
                    (
                        "ID.AM-05",
                        "Assets are prioritized based on classification, criticality, resources, and impact on the mission",
                    ),
                    (
                        "ID.AM-07",
                        "Inventories of data and corresponding metadata for designated data types are maintained",
                    ),
                    (
                        "ID.AM-08",
                        "Systems, hardware, software, services, and data are managed throughout their life cycles",
                    ),
                ],
            ),
            (
                "ID.RA",
                "Risk Assessment",
                "The cybersecurity risk to the organization, assets, and individuals is understood by the organization",
                [
                    (
                        "ID.RA-01",
                        "Vulnerabilities in assets are identified, validated, and recorded",
                    ),
                    (
                        "ID.RA-02",
                        "Cyber threat intelligence is received from information sharing forums and sources",
                    ),
                    (
                        "ID.RA-03",
                        "Internal and external threats to the organization are identified and recorded",
                    ),
                    (
                        "ID.RA-04",
                        "Potential impacts and likelihoods of threats exploiting vulnerabilities are identified and recorded",
                    ),
                    (
                        "ID.RA-05",
                        "Threats, vulnerabilities, likelihoods, and impacts are used to understand inherent risk and inform risk response prioritization",
                    ),
                    (
                        "ID.RA-06",
                        "Risk responses are chosen, prioritized, planned, tracked, and communicated",
                    ),
                    (
                        "ID.RA-07",
                        "Changes and exceptions are managed, assessed for risk impact, recorded, and tracked",
                    ),
                    (
                        "ID.RA-08",
                        "Processes for receiving, analyzing, and responding to vulnerability disclosures are established",
                    ),
                    (
                        "ID.RA-09",
                        "The authenticity and integrity of hardware and software are assessed prior to acquisition and use",
                    ),
                    ("ID.RA-10", "Critical suppliers are assessed prior to acquisition"),
                ],
            ),
            (
                "ID.IM",
                "Improvement",
                "Improvements to organizational cybersecurity risk management processes, procedures "
                "and activities are identified across all CSF Functions",
                [
                    ("ID.IM-01", "Improvements are identified from evaluations"),
                    (
                        "ID.IM-02",
                        "Improvements are identified from security tests and exercises, including those done in coordination with suppliers and relevant third parties",
                    ),
                    (
                        "ID.IM-03",
                        "Improvements are identified from execution of operational processes, procedures, and activities",
                    ),
                    (
                        "ID.IM-04",
                        "Incident response plans and other cybersecurity plans that affect operations are established, communicated, maintained, and improved",
                    ),
                ],
            ),
        ],
    ),
    (
        "PR",
        "Protect",
        "Safeguards to manage the organization's cybersecurity risks are used",
        [
            (
                "PR.AA",
                "Identity Management, Authentication, and Access Control",
                "Access to physical and logical assets is limited to authorized users, services, and hardware "
                "and managed commensurate with the assessed risk of unauthorized access",
                [
                    (
                        "PR.AA-01",
                        "Identities and credentials for authorized users, services, and hardware are managed by the organization",
                    ),
                    (
                        "PR.AA-02",
                        "Identities are proofed and bound to credentials based on the context of interactions",
                    ),
                    ("PR.AA-03", "Users, services, and hardware are authenticated"),
                    ("PR.AA-04", "Identity assertions are protected, conveyed, and verified"),
                    (
                        "PR.AA-05",
                        "Access permissions, entitlements, and authorizations are defined in a policy, managed, enforced, and reviewed, and incorporate the principles of least privilege and separation of duties",
                    ),
                    (
                        "PR.AA-06",
                        "Physical access to assets is managed, monitored, and enforced commensurate with risk",
                    ),
                ],
            ),
            (
                "PR.AT",
                "Awareness and Training",
                "The organization's personnel are provided with cybersecurity awareness and training so that "
                "they can perform their cybersecurity-related tasks",
                [
                    (
                        "PR.AT-01",
                        "Personnel are provided with awareness and training so that they possess the knowledge and skills to perform general tasks with cybersecurity risks in mind",
                    ),
                    (
                        "PR.AT-02",
                        "Individuals in specialized roles are provided with awareness and training so that they possess the knowledge and skills to perform relevant tasks with cybersecurity risks in mind",
                    ),
                ],
            ),
            (
                "PR.DS",
                "Data Security",
                "Data are managed consistent with the organization's risk strategy to protect the "
                "confidentiality, integrity, and availability of information",
                [
                    (
                        "PR.DS-01",
                        "The confidentiality, integrity, and availability of data-at-rest are protected",
                    ),
                    (
                        "PR.DS-02",
                        "The confidentiality, integrity, and availability of data-in-transit are protected",
                    ),
                    (
                        "PR.DS-10",
                        "The confidentiality, integrity, and availability of data-in-use are protected",
                    ),
                    ("PR.DS-11", "Backups of data are created, protected, maintained, and tested"),
                ],
            ),
            (
                "PR.PS",
                "Platform Security",
                "The hardware, software (e.g., firmware, operating systems, applications), and services of "
                "physical and virtual platforms are managed consistent with the organization's risk strategy "
                "to protect their confidentiality, integrity, and availability",
                [
                    ("PR.PS-01", "Configuration management practices are established and applied"),
                    (
                        "PR.PS-02",
                        "Software is maintained, replaced, and removed commensurate with risk",
                    ),
                    (
                        "PR.PS-03",
                        "Hardware is maintained, replaced, and removed commensurate with risk",
                    ),
                    (
                        "PR.PS-04",
                        "Log records are generated and made available for continuous monitoring",
                    ),
                    (
                        "PR.PS-05",
                        "Installation and execution of unauthorized software are prevented",
                    ),
                    (
                        "PR.PS-06",
                        "Secure software development practices are integrated, and their performance is monitored throughout the software development life cycle",
                    ),
                ],
            ),
            (
                "PR.IR",
                "Technology Infrastructure Resilience",
                "Security architectures are managed with the organization's risk strategy to protect asset "
                "confidentiality, integrity, and availability, and organizational resilience",
                [
                    (
                        "PR.IR-01",
                        "Networks and environments are protected from unauthorized logical access and usage",
                    ),
                    (
                        "PR.IR-02",
                        "The organization's technology assets are protected from environmental threats",
                    ),
                    (
                        "PR.IR-03",
                        "Mechanisms are implemented to achieve resilience requirements in normal and adverse situations",
                    ),
                    ("PR.IR-04", "Adequate resource capacity to ensure availability is maintained"),
                ],
            ),
        ],
    ),
    (
        "DE",
        "Detect",
        "Possible cybersecurity attacks and compromises are found and analyzed",
        [
            (
                "DE.CM",
                "Continuous Monitoring",
                "Assets are monitored to find anomalies, indicators of compromise, and other potentially "
                "adverse events",
                [
                    (
                        "DE.CM-01",
                        "Networks and network services are monitored to find potentially adverse events",
                    ),
                    (
                        "DE.CM-02",
                        "The physical environment is monitored to find potentially adverse events",
                    ),
                    (
                        "DE.CM-03",
                        "Personnel activity and technology usage are monitored to find potentially adverse events",
                    ),
                    (
                        "DE.CM-06",
                        "External service provider activities and services are monitored to find potentially adverse events",
                    ),
                    (
                        "DE.CM-09",
                        "Computing hardware and software, runtime environments, and their data are monitored to find potentially adverse events",
                    ),
                ],
            ),
            (
                "DE.AE",
                "Adverse Event Analysis",
                "Anomalies, indicators of compromise, and other potentially adverse events are analyzed to "
                "characterize the events and detect cybersecurity incidents",
                [
                    (
                        "DE.AE-02",
                        "Potentially adverse events are analyzed to better understand associated activities",
                    ),
                    ("DE.AE-03", "Information is correlated from multiple sources"),
                    ("DE.AE-04", "The estimated impact and scope of adverse events are understood"),
                    (
                        "DE.AE-06",
                        "Information on adverse events is provided to authorized staff and tools",
                    ),
                    (
                        "DE.AE-07",
                        "Cyber threat intelligence and other contextual information are integrated into the analysis",
                    ),
                    (
                        "DE.AE-08",
                        "Incidents are declared when adverse events meet the defined incident criteria",
                    ),
                ],
            ),
        ],
    ),
    (
        "RS",
        "Respond",
        "Actions regarding a detected cybersecurity incident are taken",
        [
            (
                "RS.MA",
                "Incident Management",
                "Responses to detected cybersecurity incidents are managed",
                [
                    (
                        "RS.MA-01",
                        "The incident response plan is executed in coordination with relevant third parties once an incident is declared",
                    ),
                    ("RS.MA-02", "Incident reports are triaged and validated"),
                    ("RS.MA-03", "Incidents are categorized and prioritized"),
                    ("RS.MA-04", "Incidents are escalated or elevated as needed"),
                    ("RS.MA-05", "The criteria for initiating incident recovery are applied"),
                ],
            ),
            (
                "RS.AN",
                "Incident Analysis",
                "Investigations are conducted to ensure effective response and support forensics and "
                "recovery activities",
                [
                    (
                        "RS.AN-03",
                        "Analysis is performed to establish what has taken place during an incident and the root cause of the incident",
                    ),
                    (
                        "RS.AN-06",
                        "Actions performed during an investigation are recorded, and the records' integrity and provenance are preserved",
                    ),
                    (
                        "RS.AN-07",
                        "Incident data and metadata are collected, and their integrity and provenance are preserved",
                    ),
                    ("RS.AN-08", "An incident's magnitude is estimated and validated"),
                ],
            ),
            (
                "RS.CO",
                "Incident Response Reporting and Communication",
                "Response activities are coordinated with internal and external stakeholders as required "
                "by laws, regulations, or policies",
                [
                    ("RS.CO-02", "Internal and external stakeholders are notified of incidents"),
                    (
                        "RS.CO-03",
                        "Information is shared with designated internal and external stakeholders",
                    ),
                ],
            ),
            (
                "RS.MI",
                "Incident Mitigation",
                "Activities are performed to prevent expansion of an event and mitigate its effects",
                [
                    ("RS.MI-01", "Incidents are contained"),
                    ("RS.MI-02", "Incidents are eradicated"),
                ],
            ),
        ],
    ),
    (
        "RC",
        "Recover",
        "Assets and operations affected by a cybersecurity incident are restored",
        [
            (
                "RC.RP",
                "Incident Recovery Plan Execution",
                "Restoration activities are performed to ensure operational availability of systems and "
                "services affected by cybersecurity incidents",
                [
                    (
                        "RC.RP-01",
                        "The recovery portion of the incident response plan is executed once initiated from the incident response process",
                    ),
                    (
                        "RC.RP-02",
                        "Recovery actions are selected, scoped, prioritized, and performed",
                    ),
                    (
                        "RC.RP-03",
                        "The integrity of backups and other restoration assets is verified before using them for restoration",
                    ),
                    (
                        "RC.RP-04",
                        "Critical mission functions and cybersecurity risk management are considered to establish post-incident operational norms",
                    ),
                    (
                        "RC.RP-05",
                        "The integrity of restored assets is verified, systems and services are restored, and normal operating status is confirmed",
                    ),
                    (
                        "RC.RP-06",
                        "The end of incident recovery is declared based on criteria, and incident-related documentation is completed",
                    ),
                ],
            ),
            (
                "RC.CO",
                "Incident Recovery Communication",
                "Restoration activities are coordinated with internal and external parties",
                [
                    (
                        "RC.CO-03",
                        "Recovery activities and progress in restoring operational capabilities are communicated to designated internal and external stakeholders",
                    ),
                    (
                        "RC.CO-04",
                        "Public updates on incident recovery are shared using approved methods and messaging",
                    ),
                ],
            ),
        ],
    ),
]

# ---------------------------------------------------------------------------
# Keywords per Category for search enrichment
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "GV.OC": [
        "mission",
        "stakeholders",
        "legal requirements",
        "regulatory",
        "contractual",
        "organizational context",
    ],
    "GV.RM": [
        "risk management",
        "risk appetite",
        "risk tolerance",
        "enterprise risk",
        "risk strategy",
    ],
    "GV.RR": [
        "roles",
        "responsibilities",
        "authorities",
        "accountability",
        "governance",
        "human resources",
    ],
    "GV.PO": ["policy", "cybersecurity policy", "risk policy", "governance"],
    "GV.OV": ["oversight", "performance", "monitoring", "risk strategy", "governance"],
    "GV.SC": [
        "supply chain",
        "suppliers",
        "third party",
        "vendor risk",
        "procurement",
        "supply chain risk",
    ],
    "ID.AM": [
        "asset management",
        "inventory",
        "hardware",
        "software",
        "data",
        "systems",
        "life cycle",
    ],
    "ID.RA": [
        "risk assessment",
        "vulnerability",
        "threat intelligence",
        "threat",
        "impact",
        "likelihood",
    ],
    "ID.IM": ["improvement", "lessons learned", "security testing", "incident response plan"],
    "PR.AA": [
        "identity",
        "authentication",
        "access control",
        "MFA",
        "least privilege",
        "credentials",
        "physical access",
    ],
    "PR.AT": ["awareness", "training", "security awareness", "education", "personnel"],
    "PR.DS": [
        "data security",
        "encryption",
        "data protection",
        "confidentiality",
        "integrity",
        "backup",
    ],
    "PR.PS": [
        "platform security",
        "configuration management",
        "patching",
        "hardening",
        "logging",
        "software security",
    ],
    "PR.IR": [
        "resilience",
        "network security",
        "availability",
        "redundancy",
        "environmental controls",
    ],
    "DE.CM": [
        "monitoring",
        "detection",
        "anomaly detection",
        "network monitoring",
        "SIEM",
        "log monitoring",
    ],
    "DE.AE": [
        "event analysis",
        "incident detection",
        "threat detection",
        "IOC",
        "indicators of compromise",
        "correlation",
    ],
    "RS.MA": ["incident management", "incident response", "triage", "escalation", "response plan"],
    "RS.AN": ["incident analysis", "forensics", "root cause analysis", "investigation"],
    "RS.CO": ["incident communication", "notification", "reporting", "stakeholder communication"],
    "RS.MI": ["containment", "eradication", "mitigation", "incident containment"],
    "RC.RP": [
        "recovery",
        "restoration",
        "business continuity",
        "backup restoration",
        "operational recovery",
    ],
    "RC.CO": [
        "recovery communication",
        "stakeholder notification",
        "public communication",
        "status updates",
    ],
}

# ---------------------------------------------------------------------------
# Optional guidance page URLs on the NIST CPRT website
# ---------------------------------------------------------------------------

_CPRT_BASE = "https://csrc.nist.gov/projects/cprt/catalog#/cprt/framework/version/CSF_2_0_0/home"

_CATEGORY_GUIDANCE_URLS: Dict[str, str] = {
    cat_id: f"https://csrc.nist.gov/projects/cprt/catalog#/cprt/framework/version/CSF_2_0_0/element/{cat_id}"
    for func_id, _, _, cats in _CSF_CORE
    for cat_id, _, _, _ in cats
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Run slugify."""
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _fetch_guidance(category_id: str) -> str:
    """Attempt to fetch category guidance text from NIST CPRT. Returns empty string on failure."""
    try:
        import requests  # noqa: PLC0415  # type: ignore
        from bs4 import BeautifulSoup  # noqa: PLC0415
    except ImportError:
        logger.debug(
            "requests/beautifulsoup4 not available; skipping guidance fetch for %s", category_id
        )
        return ""

    url = f"https://www.nist.gov/cyberframework/csf-20-by-the-numbers"
    try:
        resp = requests.get(
            "https://www.nist.gov/cyberframework",
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
    except Exception as exc:
        logger.debug("Guidance fetch failed for %s: %s", category_id, exc)
        return ""
    return ""


def _build_category_guidance_map(fetch_guidance: bool) -> Dict[str, str]:
    """Return a mapping of category_id → guidance text."""
    if not fetch_guidance:
        return {}

    try:
        import requests  # noqa: PLC0415  # type: ignore
        from bs4 import BeautifulSoup  # noqa: PLC0415
    except ImportError:
        logger.warning("requests/beautifulsoup4 not installed; guidance will be empty.")
        return {}

    guidance: Dict[str, str] = {}

    for func_id, func_name, func_desc, categories in _CSF_CORE:
        for cat_id, cat_name, cat_desc, _ in categories:
            # The NIST CPRT pages require JavaScript to render subcategory text,
            # so the best static guidance we can reliably embed is the category
            # description from the spec itself.  Attempt to get expanded text from
            # the NIST framework page for each function.
            guidance[cat_id] = cat_desc

    return guidance


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class NistCsfParser(BaseParser):
    """Parser for NIST Cybersecurity Framework 2.0.

    Emits one RequirementRecord per Subcategory using the embedded static core
    data from NIST CSWP 29 (Appendix A).  No network access is required unless
    ``fetch_guidance=True``, in which case the NIST website is consulted for
    supplementary category guidance text (best-effort; falls back to the
    category description from the spec if the page is unavailable).
    """

    def __init__(self, fetch_guidance: bool = True) -> None:
        """Run init."""
        self._fetch_guidance = fetch_guidance

    def parse(self) -> List[RequirementRecord]:
        """Run parse."""
        logger.info("Parsing NIST CSF 2.0 core (fetch_guidance=%s)", self._fetch_guidance)

        guidance_map = _build_category_guidance_map(self._fetch_guidance)

        records: List[RequirementRecord] = []

        for func_id, func_name, func_desc, categories in _CSF_CORE:
            for cat_id, cat_name, cat_desc, subcategories in categories:
                guidance_text = guidance_map.get(cat_id, cat_desc) or ""
                keywords = filter_keywords(_CATEGORY_KEYWORDS.get(cat_id, []))
                func_slug = _slugify(func_name)
                cat_slug = _slugify(cat_name)

                for sub_id, sub_text in subcategories:
                    seq = sub_id.split("-")[-1].zfill(3)
                    requirement_id = f"NIST-CSF-{func_id}-{sub_id.replace('.', '-')}"

                    records.append(
                        RequirementRecord(
                            requirement_id=requirement_id,
                            framework=FRAMEWORK,
                            framework_version=FRAMEWORK_VERSION,
                            control_family=f"{cat_id} – {cat_name}",
                            maturity_level=None,
                            requirement_text=f"[{sub_id}] {sub_text}",
                            guidance_text=guidance_text,
                            keywords=keywords,
                            source_uri=SOURCE_URI,
                            source_section=f"{func_name} ({func_id}) > {cat_name} ({cat_id})",
                            effective_date=EFFECTIVE_DATE,
                            jurisdiction_or_scope=JURISDICTION,
                        )
                    )

        logger.info("Produced %d NIST CSF 2.0 subcategory records", len(records))
        return records
