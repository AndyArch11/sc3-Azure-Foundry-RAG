"""Standards document pre-parsers that extract normalised RequirementRecord objects."""
from .base import BaseParser, RequirementRecord
from .essential_eight import EssentialEightParser

__all__ = ["BaseParser", "RequirementRecord", "EssentialEightParser"]
