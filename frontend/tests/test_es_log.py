from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from tools import es_log

if TYPE_CHECKING:
    from pathlib import Path

# Real line shapes from a RetroBat build-42 run (2026-11-05), shortened.
LOG = (
    "2026-11-05 14:10:58\t[24080]\tERROR\tCould not find appropriate font face for character 32"
    " for font :/opensans_hebrew_condensed_regular.ttf\r\n"
    "2026-11-05 14:10:58\t[24080]\tDEBUG\tResource path not found: :/frame.png\r\n"
    '2026-11-05 14:10:59\t[24080]\tWARNING\tIncluded file "./auto-favorites/custom.xml" not found!'
    ' (resolved to "C:/t/auto-favorites/custom.xml")\r\n'
    '2026-11-05 14:10:59\t[24080]\tWARNING\tIncluded file "./auto-favorites/custom.xml" not found!'
    ' (resolved to "C:/t/auto-favorites/custom.xml")\r\n'
    "2026-11-05 14:10:59\t[24080]\tWARNING\tUnknown property : image.colour\r\n"
    "2026-11-05 14:10:59\t[24080]\tERROR\tError - File type unknown!\r\n"
    "  continued detail\r\n"
    "2026-11-05 14:11:00\t[24080]\tINFO\tLoaded 5 games\r\n"
)


def test_parse_folds_continuation_lines() -> None:
    entries = es_log.parse(LOG)
    assert len(entries) == 7
    assert entries[5] == es_log.Entry("ERROR", "Error - File type unknown! continued detail")


_MISMATCH = 'requested mismatched theme type for [grid.{}] - expected "ninepatch", got "text"'


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ('Included file "./x.xml" not found! (resolved to "y")', "E010"),
        ('Unknown element of type "picture"!', "E030"),
        ("Unknown property : image.colour", "E031"),
        ('Element of type "" missing "name" attribute!', "E020"),
        ('Feature missing "supported" attribute!', "E005"),
        ('Storyboard "x" has no <animation> items !', "E032"),
        ("random is only supported in extras", "E042"),
        ('if "(((" expression is invalid : x', "W005"),
        ("Could not find appropriate font face for character 9 for font :/x.ttf", "noise"),
        ("Resource path not found: :/help/button_a.svg", "noise"),
        (_MISMATCH.format("gridtile:selected"), "noise"),  # the engine's own GuiSaveState theme
        (_MISMATCH.format("md_name"), None),  # anything else stays visible
        ("Error - File type unknown!", None),
    ],
)
def test_classify(message: str, kind: str | None) -> None:
    assert es_log.classify(es_log.Entry("WARNING", message)) == kind


def test_summarise_dedupes_and_counts() -> None:
    s = es_log.summarise(es_log.parse(LOG))
    assert s.theme == [
        (
            "E010",
            (
                'Included file "./auto-favorites/custom.xml" not found! (resolved to '
                '"C:/t/auto-favorites/custom.xml")'
            ),
            2,
        ),
        ("E031", "Unknown property : image.colour", 1),
    ]
    assert s.unclassified == [("ERROR", "Error - File type unknown! continued detail", 1)]
    assert s.noise == 2
    assert s.levels == {"DEBUG": 1, "ERROR": 2, "INFO": 1, "WARNING": 3}


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "es_log.txt"
    log.write_text(LOG, encoding="utf-8")
    assert es_log.main([str(log)]) == 0
    out = capsys.readouterr().out
    assert "theme findings: 2" in out
    assert "E010  x2" in out
    assert "font face" not in out
    assert es_log.main([str(log), "--fail-on-theme"]) == 1
    capsys.readouterr()
    assert es_log.main([str(log), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["noise"] == 2
    assert es_log.main([str(tmp_path / "missing.txt")]) == 2


def test_clean_log_passes(tmp_path: Path) -> None:
    log = tmp_path / "es_log.txt"
    log.write_text("2026-11-05 14:10:58\t[1]\tINFO\tok\r\n", encoding="utf-8")
    assert es_log.main([str(log), "--fail-on-theme"]) == 0
