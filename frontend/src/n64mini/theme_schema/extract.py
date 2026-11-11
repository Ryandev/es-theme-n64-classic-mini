"""Extract the Batocera EmulationStation theme grammar from upstream C++ source.

Batocera publishes no XSD or other formal schema for theme XML. The grammar the
engine actually enforces lives in ``ThemeData.cpp`` (``sElementMap``,
``sSupportedViews``, ...) and colour names in ``utils/HtmlColor.cpp``. This
module turns those tables into a JSON grammar file. That way the validator
follows the real engine and needs no hand-copied list.

Regenerate after bumping the vendored source::

    uv run n64mini-extract-grammar
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Final

from n64mini.paths import VENDOR_DIR
from n64mini.theme_schema.grammar import (
    GRAMMAR_PATH,
    Grammar,
    PropertyType,
    grammar_to_json,
)

_PROPERTY_TYPES: Final[frozenset[str]] = frozenset(t.value for t in PropertyType)

_STRING_SET_RE: Final = re.compile(
    r"std::set<std::string>\s+(?:ThemeData::)?(\w+)\s*\{([^}]*)\}", re.MULTILINE
)
_BASE_CLASSES_RE: Final = re.compile(r"ThemeData::sBaseClasses\s*\{(.*?)\n\};", re.DOTALL)
_ELEMENT_MAP_RE: Final = re.compile(r"ThemeData::sElementMap\s*\{(.*?)\n\};", re.DOTALL)
_ELEMENT_HEAD_RE: Final = re.compile(r'^\s*\{\s*"(\w+)"\s*,\s*\{')
_PROPERTY_RE: Final = re.compile(r'\{\s*"(\w+)"\s*,\s*([A-Z_]+)\s*\}')
_PAIR_RE: Final = re.compile(r'\{\s*"(\w+)"\s*,\s*"(\w+)"\s*\}')
_DEFINE_RE: Final = re.compile(r"#define\s+(\w+_THEME_FORMAT_VERSION)\s+(\d+)")
_COLOR_NAME_RE: Final = re.compile(r'\{\s*"([a-z]+)"\s*,\s*0x[0-9A-Fa-f]+\s*\}')
_LINE_COMMENT_RE: Final = re.compile(r"//[^\n]*")
# Engine-defined theme variables (AC-V15).
_THEME_VAR_RE: Final = re.compile(r'mVariables\["([\w.]+)"\]')
_SYSDATA_VAR_RE: Final = re.compile(r'sysData\["([\w.]+)"\]')
_PREFIX_VAR_RE: Final = re.compile(r'"([a-z]+\.)" \+ (?:name|subsetAttr)\b')
_SYSTEM_PROPS_RE: Final = re.compile(
    r"static std::map<std::string, std::function<BindableProperty\(SystemData\*\)>> "
    r"properties =\s*\{(.*?)\n\};",
    re.DOTALL,
)
_PROP_KEY_RE: Final = re.compile(r'^\s*\{\s*"(\w+)",', re.MULTILINE)
# AC-V22: built-in collection theme folders, and resolveSystemVariable's folder aliases.
_COLLECTION_RE: Final = re.compile(r'FileSorts::\w+,\s*"([\w-]+)"')
# One collection declaration: { TYPE, "name" | nameVariable, _("label"), FileSorts::x, "folder"
_COLLECTION_DECL_RE: Final = re.compile(
    r'\{\s*\w+,\s*(?:"(\w+)"|(\w+)),\s*_\("[^"]*"\),\s*FileSorts::\w+,\s*"([\w-]+)"'
)
_STRING_CONST_RE: Final = re.compile(r'std::string (\w+) = "(\w+)";')
_ALIAS_RE: Final = re.compile(
    r'compatibleFolder == "([\w-]+)"\)\s*\n\s*compatibleFolder = "([\w-]+)"'
)


class ExtractionError(RuntimeError):
    """Raised when the upstream source no longer matches the expected shape."""


def _quoted(body: str) -> frozenset[str]:
    return frozenset(re.findall(r'"([^"]+)"', body))


def _string_sets(source: str) -> dict[str, frozenset[str]]:
    return {m.group(1): _quoted(m.group(2)) for m in _STRING_SET_RE.finditer(source)}


def _element_map(source: str) -> dict[str, dict[str, PropertyType]]:
    match = _ELEMENT_MAP_RE.search(source)
    if match is None:
        raise ExtractionError("sElementMap not found in ThemeData.cpp")
    body = _LINE_COMMENT_RE.sub("", match.group(1))

    elements: dict[str, dict[str, PropertyType]] = {}
    current: dict[str, PropertyType] | None = None
    for line in body.splitlines():
        head = _ELEMENT_HEAD_RE.match(line)
        props_text = line
        if head is not None:
            current = elements.setdefault(head.group(1), {})
            props_text = line[head.end() :]
        if current is None:
            continue
        for prop, type_name in _PROPERTY_RE.findall(props_text):
            if type_name not in _PROPERTY_TYPES:
                raise ExtractionError(f"unknown property type {type_name!r} for {prop!r}")
            current[prop] = PropertyType(type_name)
    if not elements:
        raise ExtractionError("sElementMap parsed to zero elements")
    return elements


def _engine_variables(
    theme_data_cpp: str, system_data_cpp: str
) -> tuple[frozenset[str], frozenset[str]]:
    """Exact engine variable names and open prefix families (``settings.``, ``subset.``)."""
    names = set(_THEME_VAR_RE.findall(theme_data_cpp))
    prefixes = set(_PREFIX_VAR_RE.findall(theme_data_cpp))
    if system_data_cpp:
        names |= set(_SYSDATA_VAR_RE.findall(system_data_cpp))
        props = _SYSTEM_PROPS_RE.search(system_data_cpp)
        if props is None:
            raise ExtractionError("SystemData properties table not found in SystemData.cpp")
        names |= {f"system.{key}" for key in _PROP_KEY_RE.findall(props.group(1))}
    return frozenset(names), frozenset(prefixes)


def _collections(collection_cpp: str) -> tuple[str, ...]:
    if not collection_cpp:
        return ()
    folders = tuple(dict.fromkeys(_COLLECTION_RE.findall(collection_cpp)))
    if not folders:
        raise ExtractionError("no collection systems found in CollectionSystemManager.cpp")
    return folders


def _collection_names(collection_cpp: str) -> dict[str, str]:
    """Theme folder -> declared collection name, resolving names given as string constants."""
    constants = dict(_STRING_CONST_RE.findall(collection_cpp))
    names: dict[str, str] = {}
    for literal, variable, folder in _COLLECTION_DECL_RE.findall(collection_cpp):
        name = literal or constants.get(variable)
        if name is None:
            raise ExtractionError(f"collection {folder!r}: can't resolve its name {variable!r}")
        names[folder] = name
    return names


def extract(
    theme_data_cpp: str, html_color_cpp: str, system_data_cpp: str, collection_cpp: str = ""
) -> Grammar:
    """Build a :class:`Grammar` from the text of the upstream source files.

    ``system_data_cpp`` and ``collection_cpp`` may be empty (unit tests); the real run
    always passes them.
    """
    sets = _string_sets(theme_data_cpp)
    for required in ("sSupportedViews", "sSupportedFeatures", "sSupportedItemTemplate"):
        if required not in sets:
            raise ExtractionError(f"{required} not found in ThemeData.cpp")

    base_match = _BASE_CLASSES_RE.search(theme_data_cpp)
    if base_match is None:
        raise ExtractionError("sBaseClasses not found in ThemeData.cpp")

    versions = dict(_DEFINE_RE.findall(theme_data_cpp))
    try:
        minimum = int(versions["MINIMUM_THEME_FORMAT_VERSION"])
        current = int(versions["CURRENT_THEME_FORMAT_VERSION"])
    except KeyError as exc:
        raise ExtractionError(f"{exc.args[0]} not found in ThemeData.cpp") from exc

    colors = frozenset(_COLOR_NAME_RE.findall(html_color_cpp))
    if not colors:
        raise ExtractionError("no named colours found in HtmlColor.cpp")

    engine_variables, engine_variable_prefixes = _engine_variables(theme_data_cpp, system_data_cpp)
    return Grammar(
        source_commit=_read_commit(),
        minimum_format_version=minimum,
        current_format_version=current,
        views=sets["sSupportedViews"],
        features=sets["sSupportedFeatures"],
        item_template_parents=sets["sSupportedItemTemplate"],
        auto_extra_types=sets.get("_autoExtraTypes", frozenset()),
        base_classes=dict(_PAIR_RE.findall(base_match.group(1))),
        elements=_element_map(theme_data_cpp),
        color_names=colors,
        engine_variables=engine_variables,
        engine_variable_prefixes=engine_variable_prefixes,
        collection_systems=_collections(collection_cpp),
        collection_names=_collection_names(collection_cpp),
        system_folder_aliases=dict(_ALIAS_RE.findall(theme_data_cpp)),
    )


def _read_commit() -> str:
    source_txt = VENDOR_DIR / "SOURCE.txt"
    for line in source_txt.read_text(encoding="utf-8").splitlines():
        if line.startswith("Commit:"):
            return line.removeprefix("Commit:").strip().split()[0]
    raise ExtractionError(f"no 'Commit:' line in {source_txt}")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; regenerates the committed grammar JSON."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--out", type=Path, default=GRAMMAR_PATH)
    args = parser.parse_args(argv)
    out: Path = args.out

    grammar = extract(
        (VENDOR_DIR / "ThemeData.cpp").read_text(encoding="utf-8"),
        (VENDOR_DIR / "HtmlColor.cpp").read_text(encoding="utf-8"),
        (VENDOR_DIR / "SystemData.cpp").read_text(encoding="utf-8"),
        (VENDOR_DIR / "CollectionSystemManager.cpp").read_text(encoding="utf-8"),
    )
    out.write_text(json.dumps(grammar_to_json(grammar), indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {out} ({len(grammar.elements)} element types, "
        f"{sum(len(p) for p in grammar.elements.values())} properties)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
