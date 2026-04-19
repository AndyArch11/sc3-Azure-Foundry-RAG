from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class SkillDefinition:
    """SkillDefinition."""

    name: str
    description: str
    path: Path


class SkillCatalog:
    """Runtime view of discovered Agent Skills in .agents/skills."""

    _STAGE_TO_SKILL = {
        "queue_message_received": "trigger-intake",
        "resolved_target": "content-resolution",
        "access_validated": "access-validation",
        "content_retrieved": "content-retrieval",
        "corpus_retrieved": "corpus-retrieval",
        "assessment_generated": "assessment",
        "delivery_decision": "delivery-decision",
        "publication": "publication",
        "audit_and_trace": "audit-and-trace",
    }

    def __init__(self, skills: Mapping[str, SkillDefinition]) -> None:
        """Run init."""
        self._skills = dict(skills)

    @property
    def skills(self) -> dict[str, SkillDefinition]:
        """Run skills."""
        return dict(self._skills)

    def has_skill(self, name: str) -> bool:
        """Run has skill."""
        return name in self._skills

    def skill_for_stage(self, stage: str) -> str | None:
        """Run skill for stage."""
        candidate = self._STAGE_TO_SKILL.get(stage)
        if not candidate:
            return None
        return candidate if candidate in self._skills else None


def _extract_frontmatter(skill_text: str) -> tuple[str, str]:
    """Run extract frontmatter."""
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


def load_skill_catalog(skills_root: Path) -> SkillCatalog:
    """Run load skill catalog."""
    skill_files = sorted(skills_root.glob("*/SKILL.md"))
    discovered: dict[str, SkillDefinition] = {}

    for skill_file in skill_files:
        name, description = _extract_frontmatter(skill_file.read_text(encoding="utf-8"))
        discovered[name] = SkillDefinition(name=name, description=description, path=skill_file)

    return SkillCatalog(discovered)
