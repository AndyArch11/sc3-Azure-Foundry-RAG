from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from runtime.assessment_orchestration.schema_validation import assert_named_schema


@dataclass(frozen=True)
class SkillDef:
    name: str
    description: str
    path: Path


def _extract_frontmatter(skill_text: str) -> tuple[str, str]:
    lines = skill_text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md must start with YAML frontmatter delimiter '---'")

    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break

    if end_idx is None:
        raise ValueError("SKILL.md frontmatter is not terminated with '---'")

    name = ""
    description = ""
    for line in lines[1:end_idx]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key == "name":
            name = value
        elif key == "description":
            description = value

    if not name:
        raise ValueError("SKILL.md frontmatter must contain 'name'")
    if not description:
        raise ValueError("SKILL.md frontmatter must contain 'description'")

    return name, description


def _discover_skills(skills_root: Path) -> list[SkillDef]:
    skill_files = sorted(skills_root.glob("*/SKILL.md"))
    discovered: list[SkillDef] = []
    for skill_file in skill_files:
        name, description = _extract_frontmatter(skill_file.read_text(encoding="utf-8"))
        discovered.append(SkillDef(name=name, description=description, path=skill_file))
    return discovered


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9][a-z0-9_-]*", text.lower()) if len(token) >= 3}


def _select_skill(prompt: str, skills: list[SkillDef]) -> SkillDef:
    prompt_tokens = _tokens(prompt)
    if not prompt_tokens:
        raise ValueError("Prompt must contain at least one token")

    def score(skill: SkillDef) -> tuple[int, int, str]:
        description_tokens = _tokens(skill.description)
        name_tokens = _tokens(skill.name.replace("-", " "))
        all_tokens = description_tokens | name_tokens
        overlap = len(prompt_tokens & all_tokens)

        # Small deterministic boost if the exact skill name appears in the prompt.
        exact_boost = 1 if skill.name in prompt.lower() else 0
        return overlap, exact_boost, skill.name

    # Deterministic winner: highest overlap, then exact name boost, then lexical name.
    ranked = sorted(skills, key=score, reverse=True)
    return ranked[0]


def _section_bullets(markdown: str, heading: str) -> list[str]:
    lines = markdown.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if line.strip() == heading:
            start = idx + 1
            break
    if start is None:
        raise ValueError(f"Missing required section heading: {heading}")

    items: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if stripped.startswith("- "):
            item = stripped[2:].strip().strip("`")
            if item:
                items.append(item)

    if not items:
        raise ValueError(f"Section {heading} must contain at least one bullet item")
    return items


def _extract_skill_doc_contract(skill: SkillDef) -> dict:
    text = skill.path.read_text(encoding="utf-8")
    return {
        "name": skill.name,
        "outputs": _section_bullets(text, "## Outputs"),
        "allowed_identity_modes": _section_bullets(text, "## Allowed identity modes"),
        "failure_modes": _section_bullets(text, "## Failure modes"),
        "audit_fields": _section_bullets(text, "## Audit fields"),
    }


def _repo_root() -> Path:
    # tests/unit/test_skill_evals.py -> repo root is parents[2]
    return Path(__file__).resolve().parents[2]


def _load_eval_schemas(repo: Path) -> dict:
    schemas_path = repo / "tests" / "evals" / "schemas" / "skill_eval_schemas.json"
    return json.loads(schemas_path.read_text(encoding="utf-8"))


def _load_eval_suite(repo: Path) -> dict:
    eval_path = repo / "tests" / "evals" / "skill_selection_cases.json"
    return json.loads(eval_path.read_text(encoding="utf-8"))


def test_skill_discovery_frontmatter_contract() -> None:
    repo = _repo_root()
    skills_root = repo / ".agents" / "skills"
    skills = _discover_skills(skills_root)

    assert skills, f"No skills discovered under {skills_root}"
    assert len(skills) == 9, f"Expected 9 skills, found {len(skills)}"

    names = [skill.name for skill in skills]
    assert len(names) == len(set(names)), "Skill names must be unique"


def test_skill_eval_input_schema_contract() -> None:
    repo = _repo_root()
    schemas = _load_eval_schemas(repo)
    payload = _load_eval_suite(repo)

    assert_named_schema(schemas, "eval_suite", payload)

    cases = payload.get("cases", [])
    for case in cases:
        assert_named_schema(schemas, "eval_case", case)


def test_skill_eval_fixture_covers_all_discovered_skills() -> None:
    repo = _repo_root()
    skills_root = repo / ".agents" / "skills"
    skills = _discover_skills(skills_root)
    skill_names = {skill.name for skill in skills}

    payload = _load_eval_suite(repo)
    cases = payload.get("cases", [])
    expected_names = {str(case.get("expected_skill", "")).strip() for case in cases}

    assert skill_names == expected_names, (
        "Eval coverage drift detected. "
        f"discovered={sorted(skill_names)} expected={sorted(expected_names)}"
    )


def test_skill_markdown_contract_sections_are_present_and_stable() -> None:
    repo = _repo_root()
    skills_root = repo / ".agents" / "skills"

    skills = _discover_skills(skills_root)
    schemas = _load_eval_schemas(repo)

    for skill in skills:
        snapshot = _extract_skill_doc_contract(skill)
        assert_named_schema(schemas, "skill_doc_contract", snapshot)

        modes = {item.lower() for item in snapshot["allowed_identity_modes"]}
        assert "app_only" in modes, f"{skill.name} must declare app_only identity mode"
        assert "delegated" in modes, f"{skill.name} must declare delegated identity mode"


def test_skill_selection_eval_cases() -> None:
    repo = _repo_root()
    skills_root = repo / ".agents" / "skills"

    skills = _discover_skills(skills_root)
    payload = _load_eval_suite(repo)
    schemas = _load_eval_schemas(repo)

    assert payload.get("version") == 1
    cases = payload.get("cases")
    assert isinstance(cases, list) and cases, "Eval file must contain a non-empty cases list"

    for case in cases:
        case_id = str(case.get("id", ""))
        prompt = str(case.get("prompt", ""))
        expected = str(case.get("expected_skill", ""))

        assert case_id, "Each eval case must include id"
        assert prompt, f"Eval case {case_id} missing prompt"
        assert expected, f"Eval case {case_id} missing expected_skill"

        selected = _select_skill(prompt, skills)
        result = {
            "case_id": case_id,
            "expected_skill": expected,
            "selected_skill": selected.name,
            "match": selected.name == expected,
        }
        assert_named_schema(schemas, "eval_result", result)

        assert selected.name == expected, (
            f"Case {case_id} failed: expected {expected}, got {selected.name}. "
            f"Prompt: {prompt}"
        )
