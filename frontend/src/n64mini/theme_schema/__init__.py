"""Batocera EmulationStation theme grammar (extracted from upstream C++) and validator."""

from n64mini.theme_schema.grammar import Grammar, PropertyType, load_grammar
from n64mini.theme_schema.validate import (
    Code,
    Issue,
    Severity,
    ValidationResult,
    validate_theme,
)

__all__ = [
    "Code",
    "Grammar",
    "Issue",
    "PropertyType",
    "Severity",
    "ValidationResult",
    "load_grammar",
    "validate_theme",
]
