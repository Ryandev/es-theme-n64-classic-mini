"""Typed model of the Batocera EmulationStation theme grammar."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Final, TypedDict

GRAMMAR_PATH: Final = Path(__file__).with_name("data") / "batocera_theme_grammar.json"


class PropertyType(StrEnum):
    """Mirrors ``ThemeData::ElementPropertyType`` in ThemeData.h."""

    NORMALIZED_RECT = "NORMALIZED_RECT"
    NORMALIZED_PAIR = "NORMALIZED_PAIR"
    PATH = "PATH"
    STRING = "STRING"
    COLOR = "COLOR"
    FLOAT = "FLOAT"
    BOOLEAN = "BOOLEAN"


@dataclass(frozen=True, slots=True)
class Grammar:
    """The theme grammar enforced by Batocera's EmulationStation."""

    source_commit: str
    minimum_format_version: int
    current_format_version: int
    views: frozenset[str]
    features: frozenset[str]
    item_template_parents: frozenset[str]
    auto_extra_types: frozenset[str]
    base_classes: dict[str, str]
    elements: dict[str, dict[str, PropertyType]]
    color_names: frozenset[str]
    engine_variables: frozenset[str]
    engine_variable_prefixes: frozenset[str]
    collection_systems: tuple[str, ...]
    collection_names: dict[str, str]  # theme folder -> declared name (${system.name})
    system_folder_aliases: dict[str, str]

    def is_engine_variable(self, name: str) -> bool:
        """True if the engine itself defines ``${name}`` at runtime (AC-V15)."""
        if name in self.engine_variables:
            return True
        for prefix in self.engine_variable_prefixes:
            if name.startswith(prefix):
                rest = name.removeprefix(prefix)
                # ThemeData::loadFile skips settings whose name contains a dot (BLIND-15).
                return bool(rest) and not (prefix == "settings." and "." in rest)
        return False

    def property_type(self, element_type: str, prop: str) -> PropertyType | None:
        """Look up ``prop`` on ``element_type``, falling back through base classes.

        Matches ``ThemeData::findPropertyFromBaseClass``.
        """
        seen: set[str] = set()
        current: str | None = element_type
        while current is not None and current not in seen:
            seen.add(current)
            found = self.elements.get(current, {}).get(prop)
            if found is not None:
                return found
            current = self.base_classes.get(current)
        return None


class GrammarJson(TypedDict):
    """On-disk JSON shape of :class:`Grammar`."""

    source_commit: str
    minimum_format_version: int
    current_format_version: int
    views: list[str]
    features: list[str]
    item_template_parents: list[str]
    auto_extra_types: list[str]
    base_classes: dict[str, str]
    elements: dict[str, dict[str, str]]
    color_names: list[str]
    engine_variables: list[str]
    engine_variable_prefixes: list[str]
    collection_systems: list[str]
    collection_names: dict[str, str]
    system_folder_aliases: dict[str, str]


def grammar_to_json(grammar: Grammar) -> GrammarJson:
    """Serialise ``grammar`` with sorted keys for stable diffs."""
    return {
        "source_commit": grammar.source_commit,
        "minimum_format_version": grammar.minimum_format_version,
        "current_format_version": grammar.current_format_version,
        "views": sorted(grammar.views),
        "features": sorted(grammar.features),
        "item_template_parents": sorted(grammar.item_template_parents),
        "auto_extra_types": sorted(grammar.auto_extra_types),
        "base_classes": dict(sorted(grammar.base_classes.items())),
        "elements": {
            name: {prop: t.value for prop, t in sorted(props.items())}
            for name, props in sorted(grammar.elements.items())
        },
        "color_names": sorted(grammar.color_names),
        "engine_variables": sorted(grammar.engine_variables),
        "engine_variable_prefixes": sorted(grammar.engine_variable_prefixes),
        "collection_systems": list(grammar.collection_systems),
        "collection_names": dict(sorted(grammar.collection_names.items())),
        "system_folder_aliases": dict(sorted(grammar.system_folder_aliases.items())),
    }


def grammar_from_json(data: GrammarJson) -> Grammar:
    """Inverse of :func:`grammar_to_json`."""
    return Grammar(
        source_commit=data["source_commit"],
        minimum_format_version=data["minimum_format_version"],
        current_format_version=data["current_format_version"],
        views=frozenset(data["views"]),
        features=frozenset(data["features"]),
        item_template_parents=frozenset(data["item_template_parents"]),
        auto_extra_types=frozenset(data["auto_extra_types"]),
        base_classes=dict(data["base_classes"]),
        elements={
            name: {prop: PropertyType(t) for prop, t in props.items()}
            for name, props in data["elements"].items()
        },
        color_names=frozenset(data["color_names"]),
        engine_variables=frozenset(data["engine_variables"]),
        engine_variable_prefixes=frozenset(data["engine_variable_prefixes"]),
        collection_systems=tuple(data["collection_systems"]),
        collection_names=dict(data["collection_names"]),
        system_folder_aliases=dict(data["system_folder_aliases"]),
    )


@cache
def load_grammar(path: Path = GRAMMAR_PATH) -> Grammar:
    """Load the grammar JSON (cached per path)."""
    data: GrammarJson = json.loads(path.read_text(encoding="utf-8"))
    return grammar_from_json(data)
