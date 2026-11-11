from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from n64mini.paths import VENDOR_DIR
from n64mini.theme_schema.extract import ExtractionError, extract
from n64mini.theme_schema.extract import main as extract_main

if TYPE_CHECKING:
    from pathlib import Path

from n64mini.theme_schema.grammar import (
    GRAMMAR_PATH,
    PropertyType,
    grammar_from_json,
    grammar_to_json,
    load_grammar,
)


def test_committed_grammar_matches_vendored_source() -> None:
    """Fails if ThemeData.cpp was bumped without re-running n64mini-extract-grammar."""
    fresh = extract(
        (VENDOR_DIR / "ThemeData.cpp").read_text(encoding="utf-8"),
        (VENDOR_DIR / "HtmlColor.cpp").read_text(encoding="utf-8"),
        (VENDOR_DIR / "SystemData.cpp").read_text(encoding="utf-8"),
        (VENDOR_DIR / "CollectionSystemManager.cpp").read_text(encoding="utf-8"),
    )
    committed = json.loads(GRAMMAR_PATH.read_text(encoding="utf-8"))
    assert grammar_to_json(fresh) == committed


def test_grammar_has_the_elements_our_theme_relies_on() -> None:
    grammar = load_grammar()
    assert {"system", "basic", "detailed", "grid"} <= grammar.views
    assert grammar.elements["imagegrid"]["autoLayout"] is PropertyType.NORMALIZED_PAIR
    assert grammar.elements["imagegrid"]["scrollLoop"] is PropertyType.BOOLEAN
    assert grammar.elements["gridtile"]["backgroundImage"] is PropertyType.PATH
    assert grammar.elements["text"]["color"] is PropertyType.COLOR


def test_base_class_lookup_falls_through_to_parent() -> None:
    grammar = load_grammar()
    assert "fontPath" not in grammar.elements["clock"]
    assert grammar.property_type("clock", "fontPath") is PropertyType.PATH
    assert grammar.property_type("clock", "noSuchProperty") is None


def test_extract_reads_a_minimal_source() -> None:
    cpp = """
#define MINIMUM_THEME_FORMAT_VERSION 3
#define CURRENT_THEME_FORMAT_VERSION 6
std::set<std::string> ThemeData::sSupportedItemTemplate { "imagegrid" };
std::set<std::string> ThemeData::sSupportedViews        { "system", "grid" };
std::set<std::string> ThemeData::sSupportedFeatures     { "video" };
std::map<std::string, std::string> ThemeData::sBaseClasses {
	{ "clock", "text" }
};
ElementMap ThemeData::sElementMap {
	{ "text", {
		{ "pos", NORMALIZED_PAIR }, // trailing comment { "fake", FLOAT }
		{ "color", COLOR } } },
	{ "clock", {} },
};
"""
    colors = 'static std::map<std::string, unsigned int> htmlColors { { "red", 0xFF0000 } };'
    grammar = extract(cpp, colors, "")
    assert grammar.views == {"system", "grid"}
    assert grammar.elements == {
        "text": {"pos": PropertyType.NORMALIZED_PAIR, "color": PropertyType.COLOR},
        "clock": {},
    }
    assert grammar.base_classes == {"clock": "text"}
    assert grammar.color_names == {"red"}


def test_extract_fails_loudly_when_upstream_shape_changes() -> None:
    with pytest.raises(ExtractionError, match="sSupportedViews"):
        extract("// nothing here", "", "")


def test_json_round_trip_is_lossless() -> None:
    grammar = load_grammar()
    assert grammar_from_json(grammar_to_json(grammar)) == grammar


def test_json_is_sorted_for_stable_diffs() -> None:
    data = grammar_to_json(load_grammar())
    assert data["views"] == sorted(data["views"])
    assert list(data["elements"]) == sorted(data["elements"])
    assert list(data["elements"]["text"]) == sorted(data["elements"]["text"])


def test_base_class_cycle_terminates() -> None:
    grammar = replace(load_grammar(), base_classes={"a": "b", "b": "a"})
    assert grammar.property_type("a", "pos") is None


def test_extract_main_writes_grammar(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "g.json"
    assert extract_main(["--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == json.loads(
        GRAMMAR_PATH.read_text(encoding="utf-8")
    )
    assert "38 element types, 495 properties" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("source", "match"),
    [
        ("SETS", "sBaseClasses"),
        ("SETS\nBASES", "MINIMUM_THEME_FORMAT_VERSION"),
        ("SETS\nBASES\nVERSIONS", "sElementMap not found"),
        ("SETS\nBASES\nVERSIONS\nElementMap ThemeData::sElementMap {\n};", "zero elements"),
        (
            (
                "SETS\nBASES\nVERSIONS\nElementMap ThemeData::sElementMap {\n"
                '\t{ "text", {\n\t\t{ "pos", WEIRD_TYPE } } },\n};'
            ),
            "WEIRD_TYPE",
        ),
    ],
)
def test_extract_reports_each_missing_table(source: str, match: str) -> None:
    cpp = (
        source.replace(
            "SETS",
            'std::set<std::string> ThemeData::sSupportedViews { "grid" };\n'
            'std::set<std::string> ThemeData::sSupportedFeatures { "video" };\n'
            'std::set<std::string> ThemeData::sSupportedItemTemplate { "imagegrid" };',
        )
        .replace("BASES", "std::map<std::string, std::string> ThemeData::sBaseClasses {\n};")
        .replace(
            "VERSIONS",
            "#define MINIMUM_THEME_FORMAT_VERSION 3\n#define CURRENT_THEME_FORMAT_VERSION 6",
        )
    )
    with pytest.raises(ExtractionError, match=match):
        extract(cpp, '{ "red", 0xFF0000 }', "")


def test_extract_requires_colour_names() -> None:
    cpp = (VENDOR_DIR / "ThemeData.cpp").read_text(encoding="utf-8")
    with pytest.raises(ExtractionError, match="named colours"):
        extract(cpp, "", "")


def test_engine_variables_are_extracted() -> None:
    """AC-V15: the engine's own variables come from the vendored source, never typed by hand."""
    grammar = load_grammar()
    expected = {
        "lang",
        "global.language",
        "themePath",
        "currentPath",
        "root",
        "region",
        "screen.ratio",
        "screen.width",
        "global.help",
        "cheevos.username",
        "system.fullName",
        "system.theme",
        "system.name",
        "system.manufacturer",
    }
    assert expected <= grammar.engine_variables
    assert grammar.engine_variable_prefixes == {"settings.", "subset."}
    assert not any(v.startswith("theme.") for v in grammar.engine_variables)


def test_extract_requires_the_system_properties_table() -> None:
    theme_data = (VENDOR_DIR / "ThemeData.cpp").read_text(encoding="utf-8")
    colors = (VENDOR_DIR / "HtmlColor.cpp").read_text(encoding="utf-8")
    with pytest.raises(ExtractionError, match="properties table"):
        extract(theme_data, colors, 'sysData["screen.ratio"] = "16/9";')


def test_collection_systems_are_extracted() -> None:
    """AC-V22: built-in collection theme folders come from CollectionSystemManager.cpp."""
    grammar = load_grammar()
    assert {"auto-allgames", "auto-favorites", "custom-collections", "auto-lastplayed"} <= set(
        grammar.collection_systems
    )
    assert grammar.system_folder_aliases["auto-favorites"] == "favorites"


def test_collection_names_are_extracted() -> None:
    """AC-V22: ${system.name} for a collection is its declared name, not its folder."""
    names = load_grammar().collection_names
    assert names["auto-allgames"] == "all"
    assert names["auto-favorites"] == "favorites"
    assert names["custom-collections"] == "collections"  # via the myCollectionsName variable


def test_extract_reports_unreadable_collection_tables() -> None:
    theme_data = (VENDOR_DIR / "ThemeData.cpp").read_text(encoding="utf-8")
    colors = (VENDOR_DIR / "HtmlColor.cpp").read_text(encoding="utf-8")
    system = (VENDOR_DIR / "SystemData.cpp").read_text(encoding="utf-8")
    with pytest.raises(ExtractionError, match="no collection systems"):
        extract(theme_data, colors, system, "// nothing")
    decl = '{ CUSTOM_COLLECTION, someName, _("x"), FileSorts::A, "custom-collections", true }'
    with pytest.raises(ExtractionError, match="can't resolve its name 'someName'"):
        extract(theme_data, colors, system, decl)
