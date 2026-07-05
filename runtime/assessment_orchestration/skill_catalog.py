"""
Skill Catalog for the Assessment Orchestration Runtime.

This module provides the SkillCatalog class, which represents a collection of discovered Agent Skills in the .agents/skills directory. Each skill is defined by a SkillDefinition dataclass that includes its name, description, and path to the SKILL.md file.
The SkillCatalog class allows for querying available skills, checking for the existence of specific skills, and mapping assessment stages to corresponding skills.

"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class SkillDefinition:
    """SkillDefinition.

    Attributes:
        name: The name of the skill.
        description: A brief description of the skill.
        path: The path to the SKILL.md file defining the skill.
    """

    name: str
    description: str
    path: Path


class SkillCatalog:
    """Runtime view of discovered Agent Skills in .agents/skills.

    Attributes:
        skills: A dictionary mapping skill names to SkillDefinition instances.
    """

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
        """Initialise the SkillCatalog with a mapping of skill names to SkillDefinition instances.

        Args:
            skills: A mapping of skill names to SkillDefinition instances.
        """
        self._skills = dict(skills)

    @property
    def skills(self) -> dict[str, SkillDefinition]:
        """Return a dictionary of skill names to SkillDefinition instances.

        Returns:
            A dictionary mapping skill names to SkillDefinition instances.
        """
        return dict(self._skills)

    def has_skill(self, name: str) -> bool:
        """Check if a skill with the given name exists in the catalog.

        Args:
            name: The name of the skill to check.
        Returns:
            True if the skill exists, False otherwise.
        """
        return name in self._skills

    def skill_for_stage(self, stage: str) -> str | None:
        """Run skill for stage.

        Args:
            stage: The name of the assessment stage.
        Returns:
            The name of the skill corresponding to the stage, or None if not found.
        """
        candidate = self._STAGE_TO_SKILL.get(stage)
        if not candidate:
            return None
        return candidate if candidate in self._skills else None


def _extract_frontmatter(skill_text: str) -> tuple[str, str]:
    """Run extract frontmatter.

    Args:
        skill_text: The content of the SKILL.md file as a string.
    Returns:
        A tuple containing the skill name and description extracted from the frontmatter.
    Raises:
        ValueError: If the frontmatter is missing or malformed.
    """
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
    """Run load skill catalog.

    Args:
        skills_root: The root directory containing skill subdirectories.
    Returns:
        A SkillCatalog instance containing all discovered skills.
    """
    skill_files = sorted(skills_root.glob("*/SKILL.md"))
    discovered: dict[str, SkillDefinition] = {}

    for skill_file in skill_files:
        name, description = _extract_frontmatter(skill_file.read_text(encoding="utf-8"))
        discovered[name] = SkillDefinition(name=name, description=description, path=skill_file)

    return SkillCatalog(discovered)
