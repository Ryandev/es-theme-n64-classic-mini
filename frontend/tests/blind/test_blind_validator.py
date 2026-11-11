"""Spec-blind black-box tests for ``n64mini-validate-theme``.

Written by the blind approver from ``docs/requirements/validator.md`` and the vendored engine
source only (``frontend/vendor/batocera-emulationstation``). The validator is run as a
subprocess and only its output and exit code are inspected. A strict xfail here is an open
disagreement between the spec, the engine and the validator, for a human to settle.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

CLI = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "n64mini-validate-theme"

_ISSUE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+): (?P<severity>error|warning) (?P<code>[EW]\d{3}): (?P<msg>.*)$"
)
_SUMMARY = re.compile(r"^\[(?P<system>[^\]]+)\] (?P<status>ok|FAILED): ")

# Built-in collection theme folders, CollectionSystemManager.cpp getSystemDecls() lines 39-56.
COLLECTIONS = (
    "auto-allgames",
    "auto-lastplayed",
    "auto-favorites",
    "auto-at2players",
    "auto-at4players",
    "auto-neverplayed",
    "auto-retroachievements",
    "arcade",
    "auto-verticalarcade",
    "auto-lightgun",
    "auto-wheel",
    "auto-trackball",
    "auto-spinner",
    "custom-collections",
)


@dataclass(frozen=True, slots=True)
class Issue:
    file: str
    line: int
    severity: str
    code: str
    msg: str


@dataclass(frozen=True, slots=True)
class Result:
    returncode: int
    stdout: str
    issues: tuple[Issue, ...]
    summaries: dict[str, str]

    @property
    def codes(self) -> list[str]:
        return [issue.code for issue in self.issues]

    def with_code(self, code: str) -> list[Issue]:
        return [issue for issue in self.issues if issue.code == code]


def run(target: Path, *args: str) -> Result:
    proc = subprocess.run(
        [str(CLI), *args, str(target)], capture_output=True, text=True, check=False, timeout=30
    )
    issues: list[Issue] = []
    summaries: dict[str, str] = {}
    for raw in proc.stdout.splitlines():
        match = _ISSUE.match(raw)
        if match:
            issues.append(
                Issue(
                    file=match["file"],
                    line=int(match["line"]),
                    severity=match["severity"],
                    code=match["code"],
                    msg=match["msg"],
                )
            )
            continue
        summary = _SUMMARY.match(raw)
        if summary:
            summaries[summary["system"]] = summary["status"]
    return Result(proc.returncode, proc.stdout + proc.stderr, tuple(issues), summaries)


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def touch(root: Path, rels: Iterable[str]) -> None:
    for rel in rels:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")


def doc(body: str) -> str:
    """An included file: a ``<theme>`` with no formatVersion."""
    return f"<theme>\n{body}\n</theme>\n"


def make(
    root: Path,
    body: str,
    *,
    version: str | None = "7",
    files: Mapping[str, str] | None = None,
    assets: Iterable[str] = (),
) -> Path:
    """Write ``theme.xml`` (line 1 ``<theme>``, line 2 formatVersion, body from line 3)."""
    root.mkdir(parents=True, exist_ok=True)
    version_line = f"<formatVersion>{version}</formatVersion>" if version is not None else ""
    write(root, "theme.xml", f"<theme>\n{version_line}\n{body}\n</theme>\n")
    for rel, text in (files or {}).items():
        write(root, rel, text)
    touch(root, assets)
    return root


def sysview(inner: str) -> str:
    return f'<view name="system">\n{inner}\n</view>'


def assert_clean(result: Result) -> None:
    assert result.returncode == 0, result.stdout
    assert result.issues == (), result.stdout


def assert_has(result: Result, code: str) -> None:
    assert code in result.codes, result.stdout
    assert result.returncode == (1 if code.startswith("E") else 0), result.stdout


def assert_lacks(result: Result, code: str) -> None:
    assert code not in result.codes, result.stdout


# --------------------------------------------------------------------------------- AC-V01


def test_ac_v01_unclosed_document_is_e001(tmp_path: Path) -> None:
    write(
        tmp_path, "theme.xml", "<theme>\n<formatVersion>7</formatVersion>\n<view name='system'>\n"
    )
    result = run(tmp_path)
    assert_has(result, "E001")


def test_ac_v01_reports_the_parser_line(tmp_path: Path) -> None:
    write(
        tmp_path,
        "theme.xml",
        "<theme>\n<formatVersion>7</formatVersion>\n<view name='system'>\n"
        "<image name='a'/>\n</vieww>\n</theme>\n",
    )
    result = run(tmp_path)
    [issue] = result.with_code("E001")
    assert issue.line == 5, result.stdout


def test_ac_v01_nothing_else_is_checked_in_a_malformed_file(tmp_path: Path) -> None:
    write(
        tmp_path,
        "theme.xml",
        "<theme>\n<formatVersion>1</formatVersion>\n<bogus/>\n<view name='nope'>\n"
        "<image name='a'><foo>1</foo></image>\n",
    )
    result = run(tmp_path)
    assert result.codes == ["E001"], result.stdout


def test_ac_v01_malformed_included_file_is_reported_against_it(tmp_path: Path) -> None:
    make(tmp_path, "<include>./part.xml</include>", files={"part.xml": "<theme>\n<view>\n"})
    result = run(tmp_path)
    [issue] = result.with_code("E001")
    assert issue.file.endswith("part.xml"), result.stdout
    assert result.returncode == 1


# --------------------------------------------------------------------------------- AC-V02


@pytest.mark.parametrize("root", ["themes", "Theme", "x:theme"])
def test_ac_v02_root_must_be_exactly_theme(tmp_path: Path, root: str) -> None:
    ns = ' xmlns:x="urn:x"' if ":" in root else ""
    write(tmp_path, "theme.xml", f"<{root}{ns}><formatVersion>7</formatVersion></{root}>\n")
    assert_has(run(tmp_path), "E002")


def test_ac_v02_included_file_without_theme_root_is_e002(tmp_path: Path) -> None:
    make(tmp_path, "<include>./part.xml</include>", files={"part.xml": "<view/>\n"})
    result = run(tmp_path)
    [issue] = result.with_code("E002")
    assert issue.file.endswith("part.xml"), result.stdout


def test_ac_v02_namespaced_theme_in_included_file_is_e002(tmp_path: Path) -> None:
    make(
        tmp_path,
        "<include>./part.xml</include>",
        files={"part.xml": '<x:theme xmlns:x="urn:x"></x:theme>\n'},
    )
    assert_has(run(tmp_path), "E002")


# --------------------------------------------------------------------------------- AC-V03


@pytest.mark.parametrize("version", ["3", "3.0", "6.5", "7", "10"])
def test_ac_v03_usable_versions_pass(tmp_path: Path, version: str) -> None:
    make(tmp_path, sysview(""), version=version)
    assert_clean(run(tmp_path))


@pytest.mark.parametrize(
    "version", ["2", "2.99", "-7", "0", "abc", "1e1", "nan", "", "   ", "7abc"]
)
def test_ac_v03_unusable_versions_are_e003(tmp_path: Path, version: str) -> None:
    make(tmp_path, sysview(""), version=version)
    assert_has(run(tmp_path), "E003")


def test_ac_v03_missing_format_version_is_e003(tmp_path: Path) -> None:
    make(tmp_path, sysview(""), version=None)
    assert_has(run(tmp_path), "E003")


def test_ac_v03_engine_reads_only_the_first_format_version(tmp_path: Path) -> None:
    # ThemeData.cpp: root.child("formatVersion") is the first such child.
    make(tmp_path, "<formatVersion>7</formatVersion>\n" + sysview(""), version="2")
    assert_has(run(tmp_path), "E003")


def test_ac_v03_included_files_may_omit_it(tmp_path: Path) -> None:
    make(tmp_path, "<include>./part.xml</include>", files={"part.xml": doc(sysview(""))})
    assert_clean(run(tmp_path))


def test_ac_v03_e003_does_not_stop_other_checks(tmp_path: Path) -> None:
    make(tmp_path, '<view name="bogus"/>', version="2")
    result = run(tmp_path)
    assert "E003" in result.codes, result.stdout
    assert "E021" in result.codes, result.stdout


# --------------------------------------------------------------------------------- AC-V04


def test_ac_v04_unknown_top_level_element_is_e004(tmp_path: Path) -> None:
    make(tmp_path, "<bogus/>")
    result = run(tmp_path)
    assert_has(result, "E004")
    assert result.with_code("E004")[0].line == 3


def test_ac_v04_all_known_top_level_elements_pass(tmp_path: Path) -> None:
    make(
        tmp_path,
        "<variables><a>1</a></variables>\n<include>./p.xml</include>\n"
        '<view name="system"/>\n<customView name="mine" inherits="system"/>\n'
        '<subset name="colorset"><include name="dark">./p.xml</include></subset>\n'
        '<feature supported="video"><view name="detailed"/></feature>',
        files={"p.xml": doc("")},
    )
    assert_clean(run(tmp_path))


def test_ac_v04_subset_is_ignored_in_format_6(tmp_path: Path) -> None:
    make(
        tmp_path,
        '<subset name="colorset"><include name="dark">./p.xml</include></subset>',
        version="6",
        files={"p.xml": doc("")},
    )
    assert_has(run(tmp_path), "E004")


def test_ac_v04_subset_is_read_in_format_6_point_5(tmp_path: Path) -> None:
    # ThemeData.cpp parseTheme: `if (mVersion <= 6)`; 6.5 takes the in-order branch.
    make(
        tmp_path,
        '<subset name="colorset"><include name="dark">./p.xml</include></subset>',
        version="6.5",
        files={"p.xml": doc("")},
    )
    assert_clean(run(tmp_path))


def test_ac_v04_included_file_follows_the_entry_files_version(tmp_path: Path) -> None:
    # appendFile() never re-reads <formatVersion>; mVersion is the entry file's.
    make(
        tmp_path,
        "<include>./p.xml</include>",
        version="6",
        files={
            "p.xml": doc(
                "<formatVersion>7</formatVersion>\n"
                '<subset name="colorset"><include name="dark">./q.xml</include></subset>'
            ),
            "q.xml": doc(""),
        },
    )
    assert_has(run(tmp_path), "E004")


def test_ac_v04_included_file_version_does_not_disable_subsets(tmp_path: Path) -> None:
    make(
        tmp_path,
        "<include>./p.xml</include>",
        version="7",
        files={
            "p.xml": doc(
                "<formatVersion>6</formatVersion>\n"
                '<subset name="colorset"><include name="dark">./q.xml</include></subset>'
            ),
            "q.xml": doc(""),
        },
    )
    assert_lacks(run(tmp_path), "E004")


def test_ac_v04_feature_reads_only_views(tmp_path: Path) -> None:
    make(tmp_path, '<feature supported="video"><image name="a"/></feature>')
    assert_has(run(tmp_path), "E004")


def test_ac_v04_stray_text_under_theme_is_e004(tmp_path: Path) -> None:
    make(tmp_path, "oops\n" + sysview(""))
    assert_has(run(tmp_path), "E004")


def test_ac_v04_subset_child_other_than_include_is_e004(tmp_path: Path) -> None:
    make(tmp_path, '<subset name="colorset"><view name="system"/></subset>')
    assert_has(run(tmp_path), "E004")


# --------------------------------------------------------------------------------- AC-V05


def test_ac_v05_missing_include_is_e010(tmp_path: Path) -> None:
    make(tmp_path, "<include>./missing.xml</include>")
    result = run(tmp_path)
    assert_has(result, "E010")
    assert result.with_code("E010")[0].line == 3


@pytest.mark.parametrize("text", [" ./p.xml", "./p.xml ", "./p.xml\n", "\n./p.xml\n"])
def test_ac_v05_include_text_is_not_trimmed(tmp_path: Path, text: str) -> None:
    make(tmp_path, f"<include>{text}</include>", files={"p.xml": doc("")})
    assert_has(run(tmp_path), "E010")


@pytest.mark.parametrize("text", ["", "   ", "\n  \n"])
def test_ac_v05_empty_include_is_skipped(tmp_path: Path, text: str) -> None:
    make(tmp_path, f"<include>{text}</include>")
    assert_clean(run(tmp_path))


def test_ac_v05_include_inside_subset_is_checked(tmp_path: Path) -> None:
    make(tmp_path, '<subset name="colorset"><include name="dark">./missing.xml</include></subset>')
    assert_has(run(tmp_path), "E010")


def test_ac_v05_include_path_uses_variables(tmp_path: Path) -> None:
    body = "<variables><dir>parts</dir></variables>\n<include>./${dir}/a.xml</include>"
    make(tmp_path, body, files={"parts/a.xml": doc("")})
    assert_clean(run(tmp_path))
    (tmp_path / "parts" / "a.xml").unlink()
    assert_has(run(tmp_path), "E010")


def test_ac_v05_include_resolves_against_the_including_file(tmp_path: Path) -> None:
    make(
        tmp_path,
        "<include>./sub/a.xml</include>",
        files={"sub/a.xml": doc("<include>./b.xml</include>"), "sub/b.xml": doc("")},
    )
    assert_clean(run(tmp_path))


def test_ac_v05_include_has_no_theme_root_fallback(tmp_path: Path) -> None:
    # parseInclude resolves only against mPaths.back(); the mPaths.front() fallback
    # exists for PATH properties (processElement), not for includes.
    make(
        tmp_path,
        "<include>./sub/a.xml</include>",
        files={"sub/a.xml": doc("<include>./b.xml</include>"), "b.xml": doc("")},
    )
    result = run(tmp_path)
    [issue] = result.with_code("E010")
    assert issue.file.endswith("a.xml"), result.stdout


# --------------------------------------------------------------------------------- AC-V06


def test_ac_v06_two_file_cycle_is_reported_once(tmp_path: Path) -> None:
    make(
        tmp_path,
        "<include>./a.xml</include>",
        files={
            "a.xml": doc("<include>./b.xml</include>"),
            "b.xml": doc("<include>./a.xml</include>"),
        },
    )
    result = run(tmp_path)
    assert result.codes.count("E011") == 1, result.stdout
    assert result.returncode == 1


def test_ac_v06_self_include_is_reported_once(tmp_path: Path) -> None:
    make(tmp_path, "<include>./theme.xml</include>")
    result = run(tmp_path)
    assert result.codes.count("E011") == 1, result.stdout


def test_ac_v06_diamond_is_not_a_cycle(tmp_path: Path) -> None:
    make(
        tmp_path,
        "<include>./a.xml</include>\n<include>./b.xml</include>",
        files={
            "a.xml": doc("<include>./d.xml</include>"),
            "b.xml": doc("<include>./d.xml</include>"),
            "d.xml": doc(""),
        },
    )
    assert_clean(run(tmp_path))


def test_ac_v06_repeated_include_is_not_a_cycle(tmp_path: Path) -> None:
    make(
        tmp_path,
        "<include>./a.xml</include>\n<include>./a.xml</include>",
        files={"a.xml": doc("")},
    )
    assert_clean(run(tmp_path))


# --------------------------------------------------------------------------------- AC-V07


def test_ac_v07_view_without_name_is_e020(tmp_path: Path) -> None:
    make(tmp_path, "<view><image name='a'/></view>")
    assert_has(run(tmp_path), "E020")


def test_ac_v07_custom_view_without_name_is_e020(tmp_path: Path) -> None:
    make(tmp_path, '<customView inherits="system"/>')
    assert_has(run(tmp_path), "E020")


def test_ac_v07_nameless_element_reports_only_e020(tmp_path: Path) -> None:
    make(tmp_path, sysview("<bogus><foo>1</foo></bogus>"))
    result = run(tmp_path)
    assert result.codes == ["E020"], result.stdout
    assert result.issues[0].line == 4


def test_ac_v07_nested_element_may_omit_name(tmp_path: Path) -> None:
    make(tmp_path, sysview('<container name="c"><image><pos>0 0</pos></image></container>'))
    assert_clean(run(tmp_path))


def test_ac_v07_empty_view_name_says_empty(tmp_path: Path) -> None:
    make(tmp_path, '<view name=""/>')
    result = run(tmp_path)
    [issue] = result.with_code("E020")
    assert "empty" in issue.msg, result.stdout


@pytest.mark.parametrize("name", ["", " , ", "\t"])
def test_ac_v07_element_with_blank_name_is_e020(tmp_path: Path, name: str) -> None:
    # parseView splits the name on " \t\r\n,"; nothing left means the element is dropped.
    make(tmp_path, sysview(f'<image name="{name}"/>'))
    assert_has(run(tmp_path), "E020")


def test_ac_v07_text_directly_in_a_view_is_e020(tmp_path: Path) -> None:
    make(tmp_path, sysview("stray"))
    assert_has(run(tmp_path), "E020")


def test_ac_v07_view_inside_feature_needs_a_name(tmp_path: Path) -> None:
    make(tmp_path, '<feature supported="video"><view/></feature>')
    assert_has(run(tmp_path), "E020")


# --------------------------------------------------------------------------------- AC-V08


@pytest.mark.parametrize("name", ["system", "system, detailed", "system,,detailed", "basic grid"])
def test_ac_v08_supported_view_lists_pass(tmp_path: Path, name: str) -> None:
    make(tmp_path, f'<view name="{name}"/>')
    assert_clean(run(tmp_path))


@pytest.mark.parametrize("name", ["bogus", "System", "system, bogus", "gamelist"])
def test_ac_v08_unsupported_view_is_e021(tmp_path: Path, name: str) -> None:
    make(tmp_path, f'<view name="{name}"/>')
    assert_has(run(tmp_path), "E021")


def test_ac_v08_custom_view_name_is_not_a_view_name(tmp_path: Path) -> None:
    make(
        tmp_path,
        '<view name="system"/>\n<customView name="mine" inherits="system"/>\n<view name="mine"/>',
    )
    assert_has(run(tmp_path), "E021")


def test_ac_v08_view_in_feature_is_checked(tmp_path: Path) -> None:
    make(tmp_path, '<feature supported="video"><view name="bogus"/></feature>')
    assert_has(run(tmp_path), "E021")


def test_ac_v08_inherits_must_be_defined_earlier(tmp_path: Path) -> None:
    make(tmp_path, '<customView name="mine" inherits="system"/>\n<view name="system"/>')
    assert_has(run(tmp_path), "E021")


def test_ac_v08_inherits_a_supported_but_undefined_view_is_e021(tmp_path: Path) -> None:
    # parseCustomViewBaseClass: mViews.find(baseClass) fails silently if never defined.
    make(tmp_path, '<view name="system"/>\n<customView name="mine" inherits="basic"/>')
    assert_has(run(tmp_path), "E021")


def test_ac_v08_inherits_an_earlier_custom_view_passes(tmp_path: Path) -> None:
    make(
        tmp_path,
        '<view name="system"/>\n<customView name="a" inherits="system"/>\n'
        '<customView name="b" inherits="a"/>',
    )
    assert_clean(run(tmp_path))


# BLIND-27: adjudicated 2026-11-05 (engine is right); fixed in the validator.
def test_ac_v08_format_6_reads_views_before_custom_views(tmp_path: Path) -> None:
    # parseTheme (mVersion <= 6) walks all <view> nodes, then all <customView> nodes.
    make(
        tmp_path, '<customView name="mine" inherits="system"/>\n<view name="system"/>', version="6"
    )
    assert_lacks(run(tmp_path), "E021")


# --------------------------------------------------------------------------------- AC-V09


@pytest.mark.parametrize("tag", ["bogus", "Image", "systemcarousel"])
def test_ac_v09_unknown_element_type_is_e030(tmp_path: Path, tag: str) -> None:
    make(tmp_path, sysview(f'<{tag} name="x"/>'))
    result = run(tmp_path)
    assert_has(result, "E030")
    assert result.with_code("E030")[0].line == 4


@pytest.mark.parametrize("tag", ["control", "sound", "shader", "gameextras", "rectangle", "splash"])
def test_ac_v09_engine_element_types_pass(tmp_path: Path, tag: str) -> None:
    make(tmp_path, sysview(f'<{tag} name="x"/>'))
    assert_clean(run(tmp_path))


def test_ac_v09_unknown_nested_element_is_e031_not_e030(tmp_path: Path) -> None:
    make(tmp_path, sysview('<image name="a"><bogus name="b"/></image>'))
    result = run(tmp_path)
    assert "E031" in result.codes, result.stdout
    assert "E030" not in result.codes, result.stdout


# --------------------------------------------------------------------------------- AC-V10


def test_ac_v10_unknown_property_is_e031(tmp_path: Path) -> None:
    make(tmp_path, sysview('<image name="a">\n<foo>1</foo>\n</image>'))
    result = run(tmp_path)
    assert_has(result, "E031")
    assert result.with_code("E031")[0].line == 5


@pytest.mark.parametrize(
    "xml",
    [
        '<clock name="c"><fontSize>0.1</fontSize><color>FFFFFF</color></clock>',
        '<batteryIcon name="b"><path>./a.png</path><full>./a.png</full></batteryIcon>',
        '<networkIcon name="n"><opacity>1</opacity></networkIcon>',
        '<batteryText name="t"><text>x</text></batteryText>',
    ],
)
def test_ac_v10_base_class_properties_pass(tmp_path: Path, xml: str) -> None:
    make(tmp_path, sysview(xml), assets=["a.png"])
    assert_clean(run(tmp_path))


def test_ac_v10_text_inside_text_with_element_first_is_nested(tmp_path: Path) -> None:
    make(tmp_path, sysview('<text name="t"><text><pos>0 0</pos><text>x</text></text></text>'))
    assert_clean(run(tmp_path))


def test_ac_v10_text_inside_text_with_text_first_drops_children(tmp_path: Path) -> None:
    make(tmp_path, sysview('<text name="t"><text>hello<pos>0 0</pos></text></text>'))
    assert_has(run(tmp_path), "E031")


@pytest.mark.parametrize(
    ("xml", "ok"),
    [
        (
            (
                '<menuBackground name="m"><menuShader><path>./a.png</path></menuShader>'
                "</menuBackground>"
            ),
            True,
        ),
        ('<menuBackground name="m"><fadeShader>./a.png</fadeShader></menuBackground>', True),
        ('<image name="m"><menuShader><path>./a.png</path></menuShader></image>', False),
        ('<image name="m"><fadeShader>./a.png</fadeShader></image>', False),
    ],
)
def test_ac_v10_menu_shaders_only_under_menu_background(
    tmp_path: Path, xml: str, *, ok: bool
) -> None:
    make(tmp_path, f'<view name="menu">\n{xml}\n</view>', assets=["a.png"])
    result = run(tmp_path)
    if ok:
        assert_clean(result)
    else:
        assert_has(result, "E031")


@pytest.mark.parametrize(
    ("xml", "ok"),
    [
        ('<textlist name="l"><itemTemplate><pos>0 0</pos></itemTemplate></textlist>', True),
        ('<image name="l"><itemTemplate><pos>0 0</pos></itemTemplate></image>', False),
        ('<imagegrid name="g"><animate>true</animate></imagegrid>', True),
        ('<image name="g"><animate>true</animate></image>', False),
        ('<menuIcons name="i"><iconWhatever>./a.png</iconWhatever></menuIcons>', True),
        (
            (
                '<image name="s"><shader><path>./a.png</path><anyUniform>1</anyUniform></shader>'
                "</image>"
            ),
            True,
        ),
        ('<image name="a"><text name="t"><fontSize>0.1</fontSize></text></image>', True),
    ],
)
def test_ac_v10_special_children(tmp_path: Path, xml: str, *, ok: bool) -> None:
    make(tmp_path, sysview(xml), assets=["a.png"])
    result = run(tmp_path)
    if ok:
        assert_clean(result)
    else:
        assert_has(result, "E031")


def test_ac_v10_text_directly_inside_an_element_is_e031(tmp_path: Path) -> None:
    make(tmp_path, sysview('<image name="a">junk<pos>0 0</pos></image>'))
    assert_has(run(tmp_path), "E031")


# --------------------------------------------------------------------------------- AC-V11


def _value_case(tmp_path: Path, prop: str, value: str) -> Result:
    make(tmp_path, sysview(f'<image name="a"><{prop}>{value}</{prop}></image>'))
    return run(tmp_path)


@pytest.mark.parametrize("value", ["5", "-0.5", ".5", "5.", "+5", "0", "123.456", "{game:rating}"])
def test_ac_v11_float_accepts_engine_numbers(tmp_path: Path, value: str) -> None:
    assert_clean(_value_case(tmp_path, "opacity", value))


@pytest.mark.parametrize(
    "value",
    [
        "1e3",
        "inf",
        "nan",
        "0.5,0.5",
        "1 2",
        " 5",
        "5 ",
        "",
        "   ",
        "-",
        ".",
        "1.2.3",
        "0x10",
        "--5",
        "+-5",
        "5&#9;",
        "1,5",
        "5\n",
    ],
)
def test_ac_v11_float_rejects_what_the_engine_reads_as_zero(tmp_path: Path, value: str) -> None:
    assert_has(_value_case(tmp_path, "opacity", value), "E040")


@pytest.mark.parametrize("value", ["0.5 0.5", "1", "-1 +2", "0.5  0.5", ".5 5."])
def test_ac_v11_pair_accepts_one_or_two_numbers(tmp_path: Path, value: str) -> None:
    assert_clean(_value_case(tmp_path, "pos", value))


@pytest.mark.parametrize(
    "value",
    [
        " 0.5 0.5",
        "0.5 ",
        "0.5 0.5 ",
        "0.5 0.5 0.5",
        "0.5,0.5",
        "0.5&#9;0.5",
        "0.5&#10;0.5",
        "",
        "{game:x}",
        "1e1 1",
        "a b",
        "- 1",
    ],
)
def test_ac_v11_pair_rejects_bad_text(tmp_path: Path, value: str) -> None:
    assert_has(_value_case(tmp_path, "pos", value), "E040")


@pytest.mark.parametrize("value", ["1", "1 2", "1 2 3 4", "0 0.5 -1 +1", "1  2  3  4"])
def test_ac_v11_rect_accepts_one_two_or_four_numbers(tmp_path: Path, value: str) -> None:
    assert_clean(_value_case(tmp_path, "padding", value))


@pytest.mark.parametrize(
    "value",
    ["1 2 3", "1 2 3 4 5", " 1 2 3 4", "1 2 3 4 ", "1,2,3,4", "1 2 3 x", "{game:x}", "", "1e1"],
)
def test_ac_v11_rect_rejects_bad_text(tmp_path: Path, value: str) -> None:
    assert_has(_value_case(tmp_path, "padding", value), "E040")


@pytest.mark.parametrize("value", ["true", "false", "1", "0", "yes", "no", "TRUE", "No", "YeS"])
def test_ac_v11_boolean_accepts_the_six_words(tmp_path: Path, value: str) -> None:
    assert_clean(_value_case(tmp_path, "visible", value))


@pytest.mark.parametrize(
    "value", [" true", "true ", "y", "t", "on", "2", "10", "", "tru", "truex", "off"]
)
def test_ac_v11_boolean_rejects_other_text(tmp_path: Path, value: str) -> None:
    assert_has(_value_case(tmp_path, "visible", value), "E040")


@pytest.mark.parametrize(
    "value",
    ["FF00FF", "ff00ff80", "#FF00FF80", "red", "rebeccapurple", "transparent", "", "abcdef"],
)
def test_ac_v11_color_accepts_engine_colours(tmp_path: Path, value: str) -> None:
    assert_clean(_value_case(tmp_path, "color", value))


@pytest.mark.parametrize(
    "value",
    [
        "Red",
        "RED",
        "#FFF",
        "FFF",
        "0xFF00FF",
        " FF00FF",
        "FF00FF ",
        "GG0000",
        "Transparent",
        "12 456",
        "FF00FF0",
        "#FF00FF0",
        "#",
        "notacolour",
    ],
)
def test_ac_v11_color_rejects_bad_text(tmp_path: Path, value: str) -> None:
    assert_has(_value_case(tmp_path, "color", value), "E040")


@pytest.mark.parametrize(
    ("attrs", "ok"),
    [
        ('pos="0.5 0.5"', True),
        ('pos="0.5,0.5"', False),
        ('opacity=" 1"', False),
        ('visible="y"', False),
        # Literal newlines in attributes are normalised to spaces by XML (and by pugixml's
        # parse_wconv_attribute), so the engine reads "0.5 0.5".
        ('pos="0.5\n0.5"', True),
        ('pos="0.5&#10;0.5"', False),
    ],
)
def test_ac_v11_property_attributes_are_checked(tmp_path: Path, attrs: str, *, ok: bool) -> None:
    make(tmp_path, sysview(f'<image name="a" {attrs}/>'))
    result = run(tmp_path)
    if ok:
        assert_clean(result)
    else:
        assert_has(result, "E040")


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("0.5<!--c--> 0.7", True),
        ("0.5 <!--c-->0.5", False),
        ("<![CDATA[0.5 0.5]]>", True),
        # BLIND-28: adjudicated 2026-11-05 (engine is right); fixed in the validator.
        pytest.param("0.5<![CDATA[ 0.7]]>", True),
        ("<!--c-->0.5 0.5", True),
        ("x<!--c-->0.5", False),
        ("   ", False),
    ],
)
def test_ac_v11_split_values_are_read_like_the_engine(
    tmp_path: Path, text: str, *, ok: bool
) -> None:
    result = _value_case(tmp_path, "pos", text)
    if ok:
        assert_clean(result)
    else:
        assert_has(result, "E040")


def test_ac_v11_variables_are_resolved_before_checking(tmp_path: Path) -> None:
    body = "<variables><p>0.5 0.5</p><bad>0.5,0.5</bad></variables>\n" + sysview(
        '<image name="a"><pos>${p}</pos></image>\n<image name="b"><pos>${bad}</pos></image>'
    )
    make(tmp_path, body)
    result = run(tmp_path)
    [issue] = result.with_code("E040")
    assert issue.line == 6, result.stdout


# --------------------------------------------------------------------------------- AC-V12


def test_ac_v12_missing_asset_message(tmp_path: Path) -> None:
    make(tmp_path, sysview('<image name="a"><path>./art/nope.png</path></image>'))
    result = run(tmp_path)
    [issue] = result.with_code("E041")
    assert issue.msg.startswith("path='./art/nope.png' not found ("), result.stdout
    assert str(tmp_path / "art" / "nope.png") in issue.msg, result.stdout
    assert result.returncode == 1


@pytest.mark.parametrize(
    "value", ["./art/bg.png", "none", "", "{game:image}", "${themePath}/art/bg.png"]
)
def test_ac_v12_resolvable_or_exempt_paths_pass(tmp_path: Path, value: str) -> None:
    make(tmp_path, sysview(f'<image name="a"><path>{value}</path></image>'), assets=["art/bg.png"])
    assert_clean(run(tmp_path))


def test_ac_v12_attribute_path_is_checked(tmp_path: Path) -> None:
    make(tmp_path, sysview('<image name="a" path="./nope.png"/>'))
    assert_has(run(tmp_path), "E041")


def test_ac_v12_included_file_falls_back_to_theme_root(tmp_path: Path) -> None:
    make(
        tmp_path,
        "<include>./sub/a.xml</include>",
        files={"sub/a.xml": doc(sysview('<image name="a"><path>./art/bg.png</path></image>'))},
        assets=["art/bg.png"],
    )
    assert_clean(run(tmp_path))


def test_ac_v12_entry_file_has_no_fallback(tmp_path: Path) -> None:
    make(tmp_path, sysview('<image name="a"><path>./bg.png</path></image>'), assets=["sub/bg.png"])
    assert_has(run(tmp_path), "E041")


def test_ac_v12_included_miss_names_both_tried_paths(tmp_path: Path) -> None:
    make(
        tmp_path,
        "<include>./sub/a.xml</include>",
        files={"sub/a.xml": doc(sysview('<image name="a"><path>./nope.png</path></image>'))},
    )
    result = run(tmp_path)
    [issue] = result.with_code("E041")
    assert " or " in issue.msg, result.stdout
    assert str(tmp_path / "sub" / "nope.png") in issue.msg, result.stdout
    assert str(tmp_path / "nope.png") in issue.msg, result.stdout


def test_ac_v12_fallback_is_the_entry_folder_not_an_intermediate_one(tmp_path: Path) -> None:
    # processElement falls back to mPaths.front() only, never to a middle include's folder.
    make(
        tmp_path,
        "<include>./sub/a.xml</include>",
        files={
            "sub/a.xml": doc("<include>./deeper/b.xml</include>"),
            "sub/deeper/b.xml": doc(sysview('<image name="a"><path>./bg.png</path></image>')),
        },
        assets=["sub/bg.png"],
    )
    assert_has(run(tmp_path), "E041")


def test_ac_v12_theme_path_variable_is_checked_not_warned(tmp_path: Path) -> None:
    make(tmp_path, sysview('<image name="a"><path>${themePath}/nope.png</path></image>'))
    result = run(tmp_path)
    assert_has(result, "E041")
    assert_lacks(result, "W002")


# --------------------------------------------------------------------------------- AC-V13


@pytest.mark.parametrize("value", ["#FF00FF", "#ff00ff"])
def test_ac_v13_hash_six_digit_colour_warns(tmp_path: Path, value: str) -> None:
    make(tmp_path, sysview(f'<image name="a"><color>{value}</color></image>'))
    result = run(tmp_path)
    assert result.codes == ["W001"], result.stdout
    assert result.returncode == 0
    assert run(tmp_path, "-W").returncode == 1


def test_ac_v13_attribute_and_variable_forms_warn(tmp_path: Path) -> None:
    body = "<variables><c>#00FF00</c></variables>\n" + sysview(
        '<image name="a" color="#FF00FF"/>\n<image name="b"><color>${c}</color></image>'
    )
    make(tmp_path, body)
    assert run(tmp_path).codes == ["W001", "W001"]


def test_ac_v13_storyboard_colour_with_hash_six_warns(tmp_path: Path) -> None:
    # ThemeStoryboard.cpp parses COLOR from/to with HtmlColor::parse: same alpha-0 result.
    make(
        tmp_path,
        sysview(
            '<image name="a"><storyboard event="x">'
            '<animation property="color" from="#FF0000" to="FF0000FF"/></storyboard></image>'
        ),
    )
    assert "W001" in run(tmp_path).codes


# --------------------------------------------------------------------------------- AC-V14


def test_ac_v14_absolute_path_warns(tmp_path: Path) -> None:
    make(tmp_path / "t", sysview('<image name="a"><path>/usr/share/x.png</path></image>'))
    result = run(tmp_path / "t")
    assert result.codes == ["W002"], result.stdout
    assert result.returncode == 0


def test_ac_v14_home_path_warns(tmp_path: Path) -> None:
    make(tmp_path / "t", sysview('<image name="a"><path>~/x.png</path></image>'))
    assert run(tmp_path / "t").codes == ["W002"]


def test_ac_v14_existing_absolute_file_outside_theme_still_warns(tmp_path: Path) -> None:
    touch(tmp_path, ["outside/x.png"])
    outside = tmp_path / "outside" / "x.png"
    make(tmp_path / "t", sysview(f'<image name="a"><path>{outside}</path></image>'))
    assert run(tmp_path / "t").codes == ["W002"]


def test_ac_v14_absolute_include_outside_theme_warns(tmp_path: Path) -> None:
    write(tmp_path, "outside/x.xml", doc(""))
    make(tmp_path / "t", f"<include>{tmp_path / 'outside' / 'x.xml'}</include>")
    assert "W002" in run(tmp_path / "t").codes


# --------------------------------------------------------------------------------- AC-V15


def test_ac_v15_undefined_variable_warns(tmp_path: Path) -> None:
    make(tmp_path, sysview('<text name="t"><text>${nope}</text></text>'))
    result = run(tmp_path)
    assert result.codes == ["W003"], result.stdout


def test_ac_v15_both_definition_forms_and_late_blocks(tmp_path: Path) -> None:
    body = (
        "<variables><a>x</a><variable name='b' value='y'/></variables>\n"
        + sysview('<text name="t"><text>${a}${b}${c}</text></text>')
        + "\n<variables><c>z</c></variables>"
    )
    make(tmp_path, body)
    assert_clean(run(tmp_path))


def test_ac_v15_variable_from_an_earlier_include_is_shared(tmp_path: Path) -> None:
    make(
        tmp_path,
        "<include>./v.xml</include>\n" + sysview('<text name="t"><text>${a}</text></text>'),
        files={"v.xml": doc("<variables><a>x</a></variables>")},
    )
    assert_clean(run(tmp_path))


def test_ac_v15_variable_from_a_later_include_is_not_yet_defined(tmp_path: Path) -> None:
    # Format 7 walks <theme> children in order; resolvePlaceholders runs while the view is
    # parsed, before the later include's parseVariables has run.
    make(
        tmp_path,
        sysview('<text name="t"><text>${a}</text></text>') + "\n<include>./v.xml</include>",
        files={"v.xml": doc("<variables><a>x</a></variables>")},
    )
    assert "W003" in run(tmp_path).codes


@pytest.mark.parametrize(
    "var",
    [
        "lang",
        "themePath",
        "root",
        "currentPath",
        "global.help",
        "screen.ratio",
        "system.fullName",
        "system.theme",
        "system.name",
        "settings.ThemeSet",
        "subset.colorset",
        "region",
        "global.language",
    ],
)
def test_ac_v15_engine_variables_are_defined(tmp_path: Path, var: str) -> None:
    make(tmp_path, sysview(f'<text name="t"><text>${{{var}}}</text></text>'))
    assert_clean(run(tmp_path))


@pytest.mark.parametrize(
    "var", ["settings.a.b", "system.zzz", "screen.bogus", "global.bogus", "Lang", "subset"]
)
def test_ac_v15_non_engine_variables_are_undefined(tmp_path: Path, var: str) -> None:
    make(tmp_path, sysview(f'<text name="t"><text>${{{var}}}</text></text>'))
    assert "W003" in run(tmp_path).codes


# --------------------------------------------------------------------------------- AC-V16


def test_ac_v16_unknown_attribute_warns(tmp_path: Path) -> None:
    make(tmp_path, sysview('<image name="a" foo="1"/>'))
    result = run(tmp_path)
    assert result.codes == ["W004"], result.stdout
    assert result.issues[0].line == 4


@pytest.mark.parametrize(
    "attr",
    [
        'extra="true"',
        'importProperties="b"',
        'lang="fr"',
        'region="eu"',
        'ifSubset="colorset:dark"',
        'verticalScreen="true"',
        'tinyScreen="false"',
        'ifHelpPrompts="true"',
        'ifCheevos="true"',
        'ifArch="x86_64"',
        'ifNotArch="rpi4"',
        'pos="0 0"',
    ],
)
def test_ac_v16_known_attributes_do_not_warn(tmp_path: Path, attr: str) -> None:
    make(tmp_path, sysview(f'<image name="b"/>\n<image name="a" {attr}/>'))
    assert_clean(run(tmp_path))


def test_ac_v16_base_class_property_attribute_does_not_warn(tmp_path: Path) -> None:
    make(tmp_path, sysview('<clock name="c" color="FFFFFF" fontSize="0.1"/>'))
    assert_clean(run(tmp_path))


def test_ac_v16_attribute_names_are_case_sensitive(tmp_path: Path) -> None:
    make(tmp_path, sysview('<image name="a" Extra="true"/>'))
    assert "W004" in run(tmp_path).codes


# --------------------------------------------------------------------------------- AC-V17


def test_ac_v17_runtime_include_warns(tmp_path: Path) -> None:
    make(tmp_path, "<include>./${screen.ratio}/x.xml</include>")
    result = run(tmp_path)
    assert result.codes == ["W005"], result.stdout
    assert result.returncode == 0


@pytest.mark.parametrize("token", ["$language", "$country"])
def test_ac_v17_runtime_dollar_tokens_in_include_warn(tmp_path: Path, token: str) -> None:
    make(tmp_path, f"<include>./{token}/x.xml</include>")
    assert run(tmp_path).codes == ["W005"]


@pytest.mark.parametrize(
    ("prop", "var"), [("pos", "screen.ratio"), ("visible", "global.help"), ("color", "lang")]
)
def test_ac_v17_runtime_typed_value_warns_and_is_not_checked(
    tmp_path: Path, prop: str, var: str
) -> None:
    make(tmp_path, sysview(f'<image name="a"><{prop}>${{{var}}}</{prop}></image>'))
    assert run(tmp_path).codes == ["W005"]


def test_ac_v17_runtime_string_value_is_silent(tmp_path: Path) -> None:
    make(tmp_path, sysview('<text name="t"><text>${screen.ratio} ${global.help}</text></text>'))
    assert_clean(run(tmp_path))


def test_ac_v17_if_expression_warns_but_empty_if_does_not(tmp_path: Path) -> None:
    make(
        tmp_path, sysview('<image name="a" if="${global.help} == true"/>\n<image name="b" if=""/>')
    )
    result = run(tmp_path)
    assert result.codes == ["W005"], result.stdout
    assert result.issues[0].line == 4


def test_ac_v17_manufacturer_feature_warns(tmp_path: Path) -> None:
    make(tmp_path, '<feature supported="manufacturer"><view name="system"/></feature>')
    assert run(tmp_path).codes == ["W005"]


def test_ac_v17_known_system_variables_never_warn(tmp_path: Path) -> None:
    make(
        tmp_path,
        "<variables><o>1</o></variables>\n"
        + sysview('<image name="a"><path>${themePath}/a.png</path><opacity>${o}</opacity></image>'),
        assets=["a.png"],
    )
    assert_clean(run(tmp_path))


# --------------------------------------------------------------------------------- AC-V18


def _random(tmp_path: Path, xml: str, view: str = 'view name="system"') -> Result:
    tag = view.split(maxsplit=1)[0]
    make(tmp_path, f'<view name="system"/>\n<{view}>\n{xml}\n</{tag}>')
    return run(tmp_path)


@pytest.mark.parametrize(
    "xml",
    [
        '<image name="a" extra="true"><path>{random}</path></image>',
        '<image name="a" extra="static"><path>{random:fanart}</path></image>',
        '<image name="a" extra="true"><path>{random:titleshot}</path></image>',
        '<video name="v" extra="true"><path>{random}</path></video>',
        # parseElement lower-cases the extra attribute before comparing.
        '<image name="a" extra="True"><path>{random}</path></image>',
    ],
)
def test_ac_v18_random_where_supported_passes(tmp_path: Path, xml: str) -> None:
    assert_clean(_random(tmp_path, xml))


@pytest.mark.parametrize(
    "xml",
    [
        '<image name="a"><path>{random}</path></image>',
        '<image name="a" extra="false"><path>{random}</path></image>',
        '<ninepatch name="a" extra="true"><path>{random}</path></ninepatch>',
        '<video name="v" extra="true"><path>{random:image}</path></video>',
        '<image name="a" extra="true"><path>{random:boxart}</path></image>',
        (
            '<container name="c"><image name="a" extra="true"><path>{random}</path></image>'
            "</container>"
        ),
    ],
)
def test_ac_v18_random_elsewhere_is_e042(tmp_path: Path, xml: str) -> None:
    assert_has(_random(tmp_path, xml), "E042")


@pytest.mark.parametrize("view", ['view name="system, grid"', 'view name="detailed"'])
def test_ac_v18_random_needs_a_view_named_exactly_system(tmp_path: Path, view: str) -> None:
    assert_has(
        _random(tmp_path, '<image name="a" extra="true"><path>{random}</path></image>', view),
        "E042",
    )


def test_ac_v18_random_in_custom_view_is_e042(tmp_path: Path) -> None:
    # The parent's tag is "customView", not "view".
    assert_has(
        _random(
            tmp_path,
            '<image name="a" extra="true"><path>{random}</path></image>',
            'customView name="system" inherits="system"',
        ),
        "E042",
    )


# --------------------------------------------------------------------------------- AC-V19


@pytest.mark.parametrize("supported", ["video", "carousel", "gamecarousel", "z-index", "visible"])
def test_ac_v19_supported_features_pass(tmp_path: Path, supported: str) -> None:
    make(tmp_path, f'<feature supported="{supported}"><view name="system"/></feature>')
    assert_clean(run(tmp_path))


@pytest.mark.parametrize(
    "attr",
    [
        "",
        'supported=""',
        'supported="bogus"',
        'supported="video,carousel"',
        'supported="Video"',
        'supported=" video"',
    ],
)
def test_ac_v19_bad_feature_is_e005(tmp_path: Path, attr: str) -> None:
    make(tmp_path, f'<feature {attr}><view name="system"/></feature>')
    assert_has(run(tmp_path), "E005")


# --------------------------------------------------------------------------------- AC-V20


def _storyboard(tmp_path: Path, inner: str, element: str = "image") -> Result:
    make(
        tmp_path,
        sysview(f'<{element} name="a"><storyboard event="x">{inner}</storyboard></{element}>'),
        assets=["s.wav"],
    )
    return run(tmp_path)


@pytest.mark.parametrize(
    "inner",
    [
        '<animation property="opacity" from="0" to="1"/>',
        '<animation property="pos" from="0.5" to="0 0"/>',
        '<animation property="shader.blur" from="0" to="1"/>',
        '<sound path="./s.wav"/>',
        "<!-- nothing yet -->",
        "   ",
    ],
)
def test_ac_v20_usable_or_empty_storyboards_pass(tmp_path: Path, inner: str) -> None:
    assert_clean(_storyboard(tmp_path, inner))


@pytest.mark.parametrize(
    "inner",
    [
        '<animation property="bogus"/>',
        "<animation/>",
        '<animation property=""/>',
        '<sound path="./missing.wav"/>',
        "<sound/>",
        "<foo/>",
        "just text",
        '<animation property="opacity"/><animation property="bogus"/>',
    ],
)
def test_ac_v20_unusable_storyboard_is_e032(tmp_path: Path, inner: str) -> None:
    assert_has(_storyboard(tmp_path, inner), "E032")


def test_ac_v20_property_lookup_ignores_base_classes(tmp_path: Path) -> None:
    # clock's own type map is empty: fromXmlNode gets typeMap without base classes.
    assert_has(_storyboard(tmp_path, '<animation property="opacity"/>', "clock"), "E032")


def test_ac_v20_nested_element_uses_its_own_type(tmp_path: Path) -> None:
    make(
        tmp_path,
        sysview(
            '<image name="a"><text name="t"><storyboard event="x">'
            '<animation property="fontSize"/></storyboard></text></image>\n'
            '<image name="b"><text name="t"><storyboard event="x">'
            '<animation property="path"/></storyboard></text></image>'
        ),
    )
    result = run(tmp_path)
    lines = {issue.line for issue in result.with_code("E032")}
    assert lines == {5}, result.stdout


@pytest.mark.parametrize(
    "inner",
    [
        '<animation property="opacity" from="abc" to="1"/>',
        '<animation property="pos" from="0 0 0" to="1 1"/>',
        '<animation property="color" from="Red" to="FF0000"/>',
        '<animation property="shader.blur" from="1e3"/>',
    ],
)
def test_ac_v20_from_and_to_use_the_property_grammar(tmp_path: Path, inner: str) -> None:
    assert_has(_storyboard(tmp_path, inner), "E040")


def test_ac_v20_sound_path_must_start_with_dot_slash(tmp_path: Path) -> None:
    result = _storyboard(tmp_path, '<sound path="s.wav"/>')
    assert {"E043", "E032"} & set(result.codes), result.stdout


# --------------------------------------------------------------------------------- AC-V21


@pytest.mark.parametrize("value", ["art/bg.png", "../bg.png", ".art/bg.png", "bg.png"])
def test_ac_v21_bare_relative_path_is_e043(tmp_path: Path, value: str) -> None:
    make(
        tmp_path / "t",
        sysview(f'<image name="a"><path>{value}</path></image>'),
        assets=["art/bg.png", ".art/bg.png", "bg.png"],
    )
    touch(tmp_path, ["bg.png"])
    assert_has(run(tmp_path / "t"), "E043")


@pytest.mark.parametrize("value", ["./art/bg.png", ".\\art\\bg.png", "./../shared/bg.png"])
def test_ac_v21_dot_slash_paths_pass(tmp_path: Path, value: str) -> None:
    touch(tmp_path, ["shared/bg.png"])
    make(
        tmp_path / "t",
        sysview(f'<image name="a"><path>{value}</path></image>'),
        assets=["art/bg.png"],
    )
    assert_clean(run(tmp_path / "t"))


def test_ac_v21_bare_include_is_e043(tmp_path: Path) -> None:
    make(tmp_path, "<include>p.xml</include>", files={"p.xml": doc("")})
    assert_has(run(tmp_path), "E043")


def test_ac_v21_rule_applies_after_variable_substitution(tmp_path: Path) -> None:
    make(
        tmp_path,
        "<variables><d>art</d></variables>\n"
        + sysview('<image name="a"><path>${d}/bg.png</path></image>'),
        assets=["art/bg.png"],
    )
    assert_has(run(tmp_path), "E043")


def test_ac_v21_theme_path_include_resolves(tmp_path: Path) -> None:
    make(tmp_path, "<include>${themePath}/p.xml</include>", files={"p.xml": doc("")})
    assert_clean(run(tmp_path))
    (tmp_path / "p.xml").unlink()
    assert_has(run(tmp_path), "E010")


# --------------------------------------------------------------------------------- AC-V22


def _folders(names: Iterable[str]) -> dict[str, str]:
    return {f"{name}/x.xml": doc("") for name in names}


def test_ac_v22_braced_system_include_fails_for_missing_collections(tmp_path: Path) -> None:
    make(tmp_path, "<include>./${system.theme}/x.xml</include>", files=_folders(["n64"]))
    result = run(tmp_path)
    [issue] = result.with_code("E010")
    assert "auto-favorites" in issue.msg, result.stdout
    assert "arcade" in issue.msg, result.stdout


def test_ac_v22_braced_system_include_with_every_folder_passes(tmp_path: Path) -> None:
    make(
        tmp_path,
        "<include>./${system.theme}/x.xml</include>",
        files=_folders(["n64", *COLLECTIONS]),
    )
    assert_clean(run(tmp_path))


def test_ac_v22_arcade_collection_is_not_auto_prefixed(tmp_path: Path) -> None:
    folders = [name for name in COLLECTIONS if name != "arcade"]
    make(tmp_path, "<include>./${system.theme}/x.xml</include>", files=_folders(["n64", *folders]))
    result = run(tmp_path)
    [issue] = result.with_code("E010")
    assert "arcade" in issue.msg, result.stdout


def test_ac_v22_dollar_system_with_default_passes(tmp_path: Path) -> None:
    make(tmp_path, "<include>./$system/x.xml</include>", files=_folders(["default"]))
    assert_clean(run(tmp_path))


def test_ac_v22_dollar_system_without_default_fails_for_collections(tmp_path: Path) -> None:
    make(tmp_path, "<include>./$system/x.xml</include>", files=_folders(["n64"]))
    assert_has(run(tmp_path), "E010")


def test_ac_v22_favorites_alias_is_used(tmp_path: Path) -> None:
    folders = [name for name in COLLECTIONS if name != "auto-favorites"]
    make(
        tmp_path,
        "<include>./$system/x.xml</include>",
        files=_folders(["n64", "favorites", *folders]),
    )
    assert_clean(run(tmp_path))


# BLIND-29: adjudicated 2026-11-05 (engine is right); fixed in the validator.
def test_ac_v22_aliased_system_never_finds_its_own_folder(tmp_path: Path) -> None:
    # resolveSystemVariable tests Utils::FileSystem::exists("./auto-favorites/x.xml") before
    # the path is resolved against the theme folder, i.e. relative to the process's working
    # directory. That fails, so the alias "favorites" is substituted and the theme's own
    # auto-favorites/ folder is never tried; with no default/ the include is not found.
    make(tmp_path, "<include>./$system/x.xml</include>", files=_folders(["n64", *COLLECTIONS]))
    result = run(tmp_path)
    [issue] = result.with_code("E010")
    assert "auto-favorites" in issue.msg, result.stdout


def test_ac_v22_every_validated_system_is_checked(tmp_path: Path) -> None:
    make(
        tmp_path,
        "<include>./${system.theme}/x.xml</include>",
        files=_folders(["n64", *COLLECTIONS]),
    )
    result = run(tmp_path, "--system", "n64", "--system", "snes")
    assert result.summaries == {"n64": "ok", "snes": "FAILED"}, result.stdout
    assert result.returncode == 1


# BLIND-30: adjudicated 2026-11-05 (engine is right); fixed in the validator.
def test_ac_v22_default_fallback_in_entry_file_sticks_for_later_includes(tmp_path: Path) -> None:
    # parseInclude: after a `$system` include falls back to default/ from the entry file
    # (mPaths.size() == 1), mSystemThemeFolder becomes "default" for the rest of the load,
    # so a later `$system` include no longer looks in n64/.
    make(
        tmp_path,
        "<include>./$system/a.xml</include>\n<include>./$system/b.xml</include>",
        files={
            "default/a.xml": doc(""),
            "n64/b.xml": doc(""),
            **{f"{name}/b.xml": doc("") for name in COLLECTIONS},
            "favorites/b.xml": doc(""),
        },
    )
    assert_has(run(tmp_path), "E010")


# --------------------------------------------------------------------------------- AC-V23
# GridTileComponent.cpp applyThemeToProperties (lines 561-567): the padding is read with
# Vector4f::parseString and, when small, multiplied by a Vector2f. Vector4f.cpp's
# operator*=(const Vector2f&) evaluates the products and discards them, so the gridtile
# padding is always pixels. ImageGridComponent.h lines 822-828 scale the imagegrid's own
# padding with a Vector4f multiply, which does work.


def _gridtile(tmp_path: Path, padding: str, *, name: str = "default") -> Result:
    make(
        tmp_path,
        f'<view name="grid">\n<gridtile name="{name}">\n'
        f"<padding>{padding}</padding>\n</gridtile>\n</view>",
    )
    return run(tmp_path)


@pytest.mark.parametrize(
    "padding",
    [
        "0.01",
        "0.5",
        "0.99",
        "0.9999",
        ".5",
        "-0.5",
        "-.01",
        "+0.5",
        "0.01 0.02",
        "0 0.5",
        "0.5 0",
        "20 0.5",
        "0.5 0 0 0",
        "0 0.5 0 0",
        "0 0 0.5 0",
        "0 0 0 0.5",
        "10 10 10 0.5",
        "-0.5 1 1 1",
        "0.01  0.02",
    ],
)
def test_ac_v23_sub_pixel_gridtile_padding_is_w006(tmp_path: Path, padding: str) -> None:
    result = _gridtile(tmp_path, padding)
    assert result.codes == ["W006"], result.stdout
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize(
    "padding",
    [
        "0",
        "0.0",
        "-0",
        "0 0",
        "0 0 0 0",
        "1",
        "1.0",
        "1.",
        "-1",
        "1 1",
        "1 0",
        "0 1 0 1",
        "2 4 6 8",
        "10 20 30 40",
        "-1 -2 -3 -4",
        "1.5 2.25",
        "100",
    ],
)
def test_ac_v23_zero_or_whole_pixel_gridtile_padding_passes(tmp_path: Path, padding: str) -> None:
    assert_clean(_gridtile(tmp_path, padding))


@pytest.mark.parametrize("name", ["default", "selected"])
def test_ac_v23_applies_to_default_and_selected_gridtiles(tmp_path: Path, name: str) -> None:
    result = _gridtile(tmp_path, "0.02", name=name)
    assert result.codes == ["W006"], result.stdout


def test_ac_v23_reports_once_per_padding_with_file_and_line(tmp_path: Path) -> None:
    make(
        tmp_path,
        '<view name="grid">\n<gridtile name="default">\n<padding>0.02</padding>\n</gridtile>\n'
        '<gridtile name="selected">\n<padding>0.03 0.04</padding>\n</gridtile>\n</view>',
    )
    result = run(tmp_path)
    issues = result.with_code("W006")
    assert len(issues) == 2, result.stdout
    assert all(issue.file.endswith("theme.xml") for issue in issues), result.stdout
    # theme.xml: line 3 view, 4 gridtile, 5 padding; 7 gridtile, 8 padding.
    assert {issue.line for issue in issues} <= {4, 5, 7, 8}, result.stdout
    assert len({issue.line for issue in issues}) == 2, result.stdout


def test_ac_v23_is_a_warning_and_fails_only_with_warnings_as_errors(tmp_path: Path) -> None:
    result = _gridtile(tmp_path, "0.01")
    [issue] = result.with_code("W006")
    assert issue.severity == "warning", result.stdout
    assert result.returncode == 0, result.stdout
    assert run(tmp_path, "-W").returncode == 1
    assert run(tmp_path, "--warnings-as-errors").returncode == 1


def test_ac_v23_whole_pixel_padding_passes_with_warnings_as_errors(tmp_path: Path) -> None:
    _gridtile(tmp_path, "4 8")
    result = run(tmp_path, "-W")
    assert result.returncode == 0, result.stdout
    assert result.issues == (), result.stdout


@pytest.mark.parametrize("padding", ["0.5 0.5 0.5", "0.01 0.02 0.03", "0.5 0.5 0.5 0.5 0.5"])
def test_ac_v23_invalid_rect_gets_e040_not_w006(tmp_path: Path, padding: str) -> None:
    result = _gridtile(tmp_path, padding)
    assert "E040" in result.codes, result.stdout
    assert "W006" not in result.codes, result.stdout
    assert result.returncode == 1


@pytest.mark.parametrize("padding", ["0.5e0", " 0.5", "0.5 ", "0,5", "{game:x}"])
def test_ac_v23_unparsable_padding_gets_e040_not_w006(tmp_path: Path, padding: str) -> None:
    result = _gridtile(tmp_path, padding)
    assert "E040" in result.codes, result.stdout
    assert "W006" not in result.codes, result.stdout


@pytest.mark.parametrize(
    ("value", "expected"), [("0.02", ["W006"]), ("0.02 4", ["W006"]), ("4", []), ("0", [])]
)
def test_ac_v23_checked_after_variable_substitution(
    tmp_path: Path, value: str, expected: list[str]
) -> None:
    make(
        tmp_path,
        f"<variables><pad>{value}</pad></variables>\n"
        '<view name="grid">\n<gridtile name="default">\n'
        "<padding>${pad}</padding>\n</gridtile>\n</view>",
    )
    result = run(tmp_path)
    assert result.codes == expected, result.stdout


def test_ac_v23_variable_attribute_form_is_substituted(tmp_path: Path) -> None:
    make(
        tmp_path,
        '<variables><variable name="pad" value="0 0.25"/></variables>\n'
        '<view name="grid">\n<gridtile name="default">\n'
        "<padding>${pad}</padding>\n</gridtile>\n</view>",
    )
    assert run(tmp_path).codes == ["W006"]


def test_ac_v23_partly_variable_padding_is_checked(tmp_path: Path) -> None:
    make(
        tmp_path,
        "<variables><a>8</a></variables>\n"
        '<view name="grid">\n<gridtile name="default">\n'
        "<padding>${a} 0.5</padding>\n</gridtile>\n</view>",
    )
    assert run(tmp_path).codes == ["W006"]


def test_ac_v23_runtime_variable_is_w005_not_w006(tmp_path: Path) -> None:
    make(
        tmp_path,
        '<view name="grid">\n<gridtile name="default">\n'
        "<padding>${screen.ratio}</padding>\n</gridtile>\n</view>",
    )
    result = run(tmp_path)
    assert "W005" in result.codes, result.stdout
    assert "W006" not in result.codes, result.stdout


# BLIND-31 ruled 2026-11-05 by the owner: validator was wrong; attribute form now reports W006.
def test_ac_v23_padding_attribute_form_is_reported(tmp_path: Path) -> None:
    # ThemeData.cpp line 1990 reads property-named attributes like child properties.
    make(tmp_path, '<view name="grid">\n<gridtile name="default" padding="0.05"/>\n</view>')
    assert run(tmp_path).codes == ["W006"]


def test_ac_v23_gridtile_in_included_file_is_reported_against_it(tmp_path: Path) -> None:
    make(
        tmp_path,
        "<include>./grid.xml</include>",
        files={
            "grid.xml": doc(
                '<view name="grid">\n<gridtile name="default">\n'
                "<padding>0.1</padding>\n</gridtile>\n</view>"
            )
        },
    )
    result = run(tmp_path)
    [issue] = result.with_code("W006")
    assert issue.file.endswith("grid.xml"), result.stdout


def test_ac_v23_gridtile_in_another_view_is_reported(tmp_path: Path) -> None:
    make(
        tmp_path,
        '<view name="basic, grid">\n<gridtile name="default">\n'
        "<padding>0.1</padding>\n</gridtile>\n</view>",
    )
    assert "W006" in run(tmp_path).codes


def test_ac_v23_imagegrid_padding_scales_and_is_not_reported(tmp_path: Path) -> None:
    make(
        tmp_path,
        '<view name="grid">\n<imagegrid name="gamegrid">\n<padding>0.01 0.02</padding>\n'
        '</imagegrid>\n<gridtile name="default">\n<padding>4</padding>\n</gridtile>\n</view>',
    )
    assert_clean(run(tmp_path))


@pytest.mark.parametrize("element", ["text", "image", "rectangle", "container", "ninepatch"])
@pytest.mark.parametrize("padding", ["0.01", "0.01 0.02", "0.01 0.02 0.03 0.04"])
def test_ac_v23_other_elements_paddings_are_not_reported(
    tmp_path: Path, element: str, padding: str
) -> None:
    make(
        tmp_path,
        f'<view name="grid">\n<{element} name="x" extra="true">\n'
        f"<padding>{padding}</padding>\n</{element}>\n</view>",
    )
    assert "W006" not in run(tmp_path).codes


def test_ac_v23_other_gridtile_properties_below_one_are_not_reported(tmp_path: Path) -> None:
    make(
        tmp_path,
        '<view name="grid">\n<gridtile name="default">\n<size>0.2 0.3</size>\n'
        "<backgroundCornerSize>0.5 0.5</backgroundCornerSize>\n<reflexion>0.1 0.1</reflexion>\n"
        "<padding>2</padding>\n</gridtile>\n</view>",
    )
    assert_clean(run(tmp_path))


# AC-V23 re-run against spec commit ae1d7e1: attribute form and reported line.


def _gridtile_attr(tmp_path: Path, padding: str, *, name: str = "default") -> Result:
    make(tmp_path, f'<view name="grid">\n<gridtile name="{name}" padding="{padding}"/>\n</view>')
    return run(tmp_path)


@pytest.mark.parametrize(
    "padding",
    [
        "0.01",
        "0.5",
        "0.99",
        ".5",
        "-0.5",
        "+0.5",
        "0.01 0.02",
        "0 0.5",
        "20 0.5",
        "0.5 0 0 0",
        "0 0 0 0.5",
        "10 10 10 0.5",
        "-0.5 1 1 1",
        "0.01  0.02",
    ],
)
def test_ac_v23_sub_pixel_padding_attribute_is_w006_in_every_shape(
    tmp_path: Path, padding: str
) -> None:
    result = _gridtile_attr(tmp_path, padding)
    assert result.codes == ["W006"], result.stdout
    assert result.returncode == 0, result.stdout
    [issue] = result.issues
    assert issue.severity == "warning", result.stdout
    # theme.xml: line 3 view, line 4 gridtile.
    assert issue.line == 4, result.stdout


@pytest.mark.parametrize(
    "padding", ["0", "0.0", "-0", "0 0", "0 0 0 0", "1", "1.", "-1", "1 0", "2 4 6 8", "1.5 2.25"]
)
def test_ac_v23_zero_or_whole_pixel_padding_attribute_passes(tmp_path: Path, padding: str) -> None:
    assert_clean(_gridtile_attr(tmp_path, padding))


@pytest.mark.parametrize("name", ["default", "selected"])
def test_ac_v23_padding_attribute_in_default_and_selected(tmp_path: Path, name: str) -> None:
    result = _gridtile_attr(tmp_path, "0.02 0.03", name=name)
    assert result.codes == ["W006"], result.stdout


@pytest.mark.parametrize("padding", ["0.5 0.5 0.5", "0.5e0", " 0.5", "0.5 ", "0,5", "{game:x}"])
def test_ac_v23_invalid_padding_attribute_gets_e040_not_w006(tmp_path: Path, padding: str) -> None:
    result = _gridtile_attr(tmp_path, padding)
    assert "E040" in result.codes, result.stdout
    assert "W006" not in result.codes, result.stdout
    assert result.returncode == 1, result.stdout


@pytest.mark.parametrize(
    ("value", "expected"), [("0.02", ["W006"]), ("0.02 4", ["W006"]), ("4", []), ("0", [])]
)
def test_ac_v23_padding_attribute_checked_after_variable_substitution(
    tmp_path: Path, value: str, expected: list[str]
) -> None:
    make(
        tmp_path,
        f"<variables><pad>{value}</pad></variables>\n"
        '<view name="grid">\n<gridtile name="default" padding="${pad}"/>\n</view>',
    )
    result = run(tmp_path)
    assert result.codes == expected, result.stdout


def test_ac_v23_padding_attribute_from_variable_attribute_form(tmp_path: Path) -> None:
    make(
        tmp_path,
        '<variables><variable name="pad" value="0 0.25"/></variables>\n'
        '<view name="grid">\n<gridtile name="selected" padding="${pad}"/>\n</view>',
    )
    result = run(tmp_path)
    assert result.codes == ["W006"], result.stdout
    # theme.xml: line 3 variables, 4 view, 5 gridtile.
    assert result.issues[0].line == 5, result.stdout


def test_ac_v23_partly_variable_padding_attribute_is_checked(tmp_path: Path) -> None:
    make(
        tmp_path,
        "<variables><a>8</a></variables>\n"
        '<view name="grid">\n<gridtile name="default" padding="${a} 0.5 ${a} ${a}"/>\n</view>',
    )
    assert run(tmp_path).codes == ["W006"]


def test_ac_v23_runtime_variable_padding_attribute_is_w005_not_w006(tmp_path: Path) -> None:
    make(
        tmp_path,
        '<view name="grid">\n<gridtile name="default" padding="${screen.ratio}"/>\n</view>',
    )
    result = run(tmp_path)
    assert "W005" in result.codes, result.stdout
    assert "W006" not in result.codes, result.stdout


def test_ac_v23_child_padding_reported_on_the_padding_elements_line(tmp_path: Path) -> None:
    make(
        tmp_path,
        '<view name="grid">\n'  # 3
        "<!-- spacer -->\n"  # 4
        '<gridtile name="default">\n'  # 5
        "<size>0.2 0.3</size>\n"  # 6
        "\n"  # 7
        "<padding>0.02</padding>\n"  # 8
        "</gridtile>\n"  # 9
        '<gridtile name="selected">\n'  # 10
        "<padding>2</padding>\n"  # 11
        "<padding>0.03 0.04</padding>\n"  # 12
        "</gridtile>\n"  # 13
        "</view>",
    )
    result = run(tmp_path)
    assert result.codes == ["W006", "W006"], result.stdout
    assert sorted(issue.line for issue in result.issues) == [8, 12], result.stdout


def test_ac_v23_attribute_padding_reported_on_the_gridtiles_line(tmp_path: Path) -> None:
    make(
        tmp_path,
        '<view name="grid">\n'  # 3
        "\n"  # 4
        '<gridtile name="default" padding="0.05">\n'  # 5
        "<size>0.2 0.3</size>\n"  # 6
        "</gridtile>\n"  # 7
        "<!-- spacer -->\n"  # 8
        '<gridtile name="selected" padding="2 0.5"/>\n'  # 9
        "</view>",
    )
    result = run(tmp_path)
    assert result.codes == ["W006", "W006"], result.stdout
    assert sorted(issue.line for issue in result.issues) == [5, 9], result.stdout


# BLIND-32 ruled 2026-11-05 by the owner: validator was wrong; issues use the start-tag line.
def test_ac_v23_multiline_gridtile_tag_reports_the_gridtiles_line(tmp_path: Path) -> None:
    make(
        tmp_path,
        '<view name="grid">\n'  # 3
        "<gridtile\n"  # 4
        '  name="default"\n'  # 5
        '  padding="0.05"\n'  # 6
        ">\n"  # 7
        "</gridtile>\n"  # 8
        "</view>",
    )
    result = run(tmp_path)
    [issue] = result.with_code("W006")
    # The element starts on line 4; the spec names "the <gridtile>'s" line.
    assert issue.line == 4, result.stdout


def test_ac_v23_attribute_and_child_each_reported_on_their_own_line(tmp_path: Path) -> None:
    make(
        tmp_path,
        '<view name="grid">\n'  # 3
        '<gridtile name="default" padding="0.5">\n'  # 4
        "<padding>0.25</padding>\n"  # 5
        "</gridtile>\n"  # 6
        "</view>",
    )
    result = run(tmp_path)
    assert result.codes == ["W006", "W006"], result.stdout
    assert sorted(issue.line for issue in result.issues) == [4, 5], result.stdout


def test_ac_v23_padding_attribute_in_included_file_reported_against_it(tmp_path: Path) -> None:
    make(
        tmp_path,
        "<include>./grid.xml</include>",
        files={
            "grid.xml": doc(
                '<view name="grid">\n\n<gridtile name="selected" padding="0.1"/>\n</view>'
            )
        },
    )
    result = run(tmp_path)
    [issue] = result.with_code("W006")
    assert issue.file.endswith("grid.xml"), result.stdout
    # grid.xml: line 1 theme, 2 view, 3 blank, 4 gridtile.
    assert issue.line == 4, result.stdout


@pytest.mark.parametrize("element", ["imagegrid", "text", "image", "rectangle"])
def test_ac_v23_other_elements_padding_attribute_is_not_w006(tmp_path: Path, element: str) -> None:
    name = "gamegrid" if element == "imagegrid" else "x"
    extra = "" if element == "imagegrid" else ' extra="true"'
    make(
        tmp_path,
        f'<view name="grid">\n<{element} name="{name}"{extra} padding="0.01 0.02"/>\n</view>',
    )
    assert "W006" not in run(tmp_path).codes


def test_ac_v23_padding_attribute_is_w006_only_with_warnings_as_errors_failing(
    tmp_path: Path,
) -> None:
    _gridtile_attr(tmp_path, "0.01")
    assert run(tmp_path).returncode == 0
    assert run(tmp_path, "-W").returncode == 1


# ------------------------------------------------------------------ line rule (Format, BLIND-32)
# Spec commit 108fcf3: "An element's line is the line where its start tag begins (the `<`),
# even when the tag spans several lines." That applies to every issue reported on an element.


def _lines(result: Result, code: str) -> list[int]:
    return sorted(issue.line for issue in result.with_code(code))


def _assert_lines(result: Result, expected: Mapping[str, list[int]]) -> None:
    for code, lines in expected.items():
        assert _lines(result, code) == sorted(lines), (code, result.stdout)


# Each case: body for make() (theme.xml line 3 onwards), {code: [start-tag lines]}.
_MULTILINE_CASES: list[tuple[str, dict[str, list[int]]]] = [
    # W004, unknown attribute (AC-V16)
    (sysview('<image\n  name="a"\n  bogus="1"\n/>'), {"W004": [4]}),
    # E040, bad attribute value (AC-V11)
    (sysview('<image\n  name="a"\n  pos="x y"\n/>'), {"E040": [4]}),
    # E040, bad child value whose start tag spans lines
    (sysview('<image name="a">\n<pos\n>x</pos>\n</image>'), {"E040": [5]}),
    # W001, a hash-prefixed six-digit colour, attribute and child forms (AC-V13)
    (sysview('<text\n  name="t"\n  color="#FFFFFF"\n/>'), {"W001": [4]}),
    (sysview('<text name="t">\n<color\n>#FFFFFF</color>\n</text>'), {"W001": [5]}),
    # E030, unknown element (AC-V09)
    (sysview('<bogus\n  name="x"\n/>'), {"E030": [4]}),
    # E020, element missing name (AC-V07)
    (sysview('<image\n  pos="0 0"\n/>'), {"E020": [4]}),
    # E020, view with an empty name
    ('<view\n  name=""\n/>', {"E020": [3]}),
    # E031, unknown property element (AC-V10)
    (sysview('<image name="a">\n<foo\n  >1</foo>\n</image>'), {"E031": [5]}),
    # E041, missing path, attribute and child forms (AC-V12)
    (sysview('<image\n  name="a"\n  path="./nope.png"\n/>'), {"E041": [4]}),
    (sysview('<image name="a">\n<path\n>./nope.png</path>\n</image>'), {"E041": [5]}),
    # E021, unsupported view name (AC-V08)
    ('<view\n  name="bogus"\n/>', {"E021": [3]}),
    # E004, unknown top-level element (AC-V04)
    ("<bogus\n/>", {"E004": [3]}),
    # E010, include not found (AC-V05)
    ("<include\n>./nope.xml</include>", {"E010": [3]}),
    # W003, undefined variable in a STRING attribute (AC-V15)
    (sysview('<text\n  name="t"\n  text="${nope}"\n/>'), {"W003": [4]}),
    # W005, if expression (AC-V17)
    (sysview('<image\n  name="a"\n  if="1"\n/>'), {"W005": [4]}),
    # E042, random outside an extra (AC-V18)
    (sysview('<image\n  name="a"\n  path="{random}"\n/>'), {"E042": [4]}),
    # E005, feature missing supported (AC-V19)
    ('<feature\n>\n<view name="system"/>\n</feature>', {"E005": [3]}),
    # W006, child padding with a multi-line start tag (AC-V23)
    (
        (
            '<view name="grid">\n<gridtile name="default">\n<padding\n>0.05</padding>\n'
            "</gridtile>\n</view>"
        ),
        {"W006": [5]},
    ),
]


@pytest.mark.parametrize(("body", "expected"), _MULTILINE_CASES)
def test_line_multiline_start_tag_reports_the_first_line(
    tmp_path: Path, body: str, expected: dict[str, list[int]]
) -> None:
    make(tmp_path, body)
    _assert_lines(run(tmp_path), expected)


def test_line_multiline_format_version_reports_its_start(tmp_path: Path) -> None:
    make(tmp_path, "<formatVersion\n>1</formatVersion>", version=None)
    _assert_lines(run(tmp_path), {"E003": [3]})


def test_line_self_closing_multiline_tags_in_sequence(tmp_path: Path) -> None:
    make(
        tmp_path,
        sysview(
            "<image\n"  # 4
            '  name="a"\n'  # 5
            '  bogus="1"\n'  # 6
            "/>\n"  # 7
            '<image name="b"\n'  # 8
            '  bogus="2" /><image\n'  # 9
            '  name="c" bogus="3"/>'  # 10
        ),
    )
    _assert_lines(run(tmp_path), {"W004": [4, 8, 9]})


def test_line_gt_inside_attribute_value_does_not_end_the_tag(tmp_path: Path) -> None:
    make(
        tmp_path,
        sysview(
            '<text name="a"\n'  # 4
            '  text="x > y\n'  # 5
            '  >" bogus="1"\n'  # 6
            "/>\n"  # 7
            '<text name="b" text="a>b"/><text name="c" bogus="2"/>'  # 8
        ),
    )
    _assert_lines(run(tmp_path), {"W004": [4, 8]})


def test_line_multiline_attribute_value_is_normalised_like_the_engine(tmp_path: Path) -> None:
    # pugixml parse_default includes parse_wconv_attribute: a newline in an attribute value
    # becomes a space, so pos="0<LF>0" is the pair "0 0" (XML 1.0 3.3.3 says the same).
    make(tmp_path, sysview('<image name="a"\n  pos="0\n0"\n/>'))
    assert_clean(run(tmp_path))


def test_line_comment_with_tag_like_text_is_not_an_element(tmp_path: Path) -> None:
    make(
        tmp_path,
        sysview(
            "<!-- <image\n"  # 4
            '  name="z"\n'  # 5
            '  bogus="1"/> -->\n'  # 6
            '<image name="a" bogus="1"/>\n'  # 7
            '<!-- <bogus name="q"/> --><image\n'  # 8
            '  name="b" bogus="2"/>'  # 9
        ),
    )
    result = run(tmp_path)
    assert "E030" not in result.codes, result.stdout
    _assert_lines(result, {"W004": [7, 8]})


def test_line_cdata_containing_lt_does_not_shift_lines(tmp_path: Path) -> None:
    make(
        tmp_path,
        sysview(
            '<text name="t">\n'  # 4
            "<text><![CDATA[1 < 2\n"  # 5
            "<image\n"  # 6
            'bogus="1"/>]]></text>\n'  # 7
            "<foo\n"  # 8
            ">1</foo>\n"  # 9
            "</text>"  # 10
        ),
    )
    result = run(tmp_path)
    assert "W004" not in result.codes, result.stdout
    _assert_lines(result, {"E031": [8]})


def test_line_several_elements_on_one_line(tmp_path: Path) -> None:
    make(
        tmp_path,
        sysview(
            '<image name="a" bogus="1"/><image name="b" bogus="2"/><bogus name="c"/>'
            '<image name="d"><foo>1</foo></image>'  # all on line 4
        ),
    )
    _assert_lines(run(tmp_path), {"W004": [4, 4], "E030": [4], "E031": [4]})


def test_line_multiline_tags_in_an_included_file(tmp_path: Path) -> None:
    make(
        tmp_path,
        "<include>./part.xml</include>",
        files={
            "part.xml": doc(
                '<view name="system">\n'  # 2
                "<image\n"  # 3
                '  name="a"\n'  # 4
                '  pos="x"\n'  # 5
                "/>\n"  # 6
                "<image\n"  # 7
                '  name="b">\n'  # 8
                "<foo\n"  # 9
                ">1</foo>\n"  # 10
                "</image>\n"  # 11
                "</view>"
            )
        },
    )
    result = run(tmp_path)
    for issue in result.issues:
        assert issue.file.endswith("part.xml"), result.stdout
    _assert_lines(result, {"E040": [3], "E031": [9]})


_PROLOGUE_TAIL = (
    "<theme>\n"  # 6
    "<formatVersion>7</formatVersion>\n"  # 7
    '<view name="system">\n'  # 8
    "<image\n"  # 9
    '  name="a" bogus="1"/>\n'  # 10
    "</view>\n"
    "</theme>\n"
)


def test_line_xml_declaration_pi_and_comment_before_root(tmp_path: Path) -> None:
    write(
        tmp_path,
        "theme.xml",
        '<?xml version="1.0" encoding="UTF-8"?>\n'  # 1
        "<?n64 some\n"  # 2
        "  data?>\n"  # 3
        "<!-- <image name='q'\n"  # 4
        "  bogus='1'/> -->\n" + _PROLOGUE_TAIL,  # 5
    )
    _assert_lines(run(tmp_path), {"W004": [9]})


def test_line_doctype_before_root(tmp_path: Path) -> None:
    write(
        tmp_path,
        "theme.xml",
        '<?xml version="1.0"?>\n'  # 1
        "<!DOCTYPE theme\n"  # 2
        "  [\n"  # 3
        "  ]>\n"  # 4
        "\n" + _PROLOGUE_TAIL,  # 5
    )
    _assert_lines(run(tmp_path), {"W004": [9]})


def test_line_wrong_root_after_a_declaration(tmp_path: Path) -> None:
    write(
        tmp_path,
        "theme.xml",
        '<?xml version="1.0"?>\n<!-- c -->\n<themes\n>\n<formatVersion>7</formatVersion>\n'
        "</themes>\n",
    )
    _assert_lines(run(tmp_path), {"E002": [3]})


def test_line_crlf_line_endings(tmp_path: Path) -> None:
    text = (
        "<theme>\n"  # 1
        "<formatVersion>7</formatVersion>\n"  # 2
        '<view name="system">\n'  # 3
        "<image\n"  # 4
        '  name="a"\n'  # 5
        '  bogus="1"/>\n'  # 6
        '<image name="b">\n'  # 7
        "<foo\n"  # 8
        ">1</foo>\n"  # 9
        "<pos>x</pos>\n"  # 10
        "</image>\n"  # 11
        "</view>\n"
        "</theme>\n"
    )
    (tmp_path / "theme.xml").write_bytes(text.replace("\n", "\r\n").encode())
    _assert_lines(run(tmp_path), {"W004": [4], "E031": [8], "E040": [10]})


def test_line_crlf_malformed_reports_the_parser_line(tmp_path: Path) -> None:
    text = "<theme>\n<formatVersion>7</formatVersion>\n<view name='system'>\n\n</vieww>\n</theme>\n"
    (tmp_path / "theme.xml").write_bytes(text.replace("\n", "\r\n").encode())
    _assert_lines(run(tmp_path), {"E001": [5]})


def test_line_deep_nesting_with_multiline_tags(tmp_path: Path) -> None:
    depth = 12
    opening = "".join(f'<container\n  name="c{i}">\n' for i in range(depth))
    closing = "</container>\n" * depth
    # view on 3; containers occupy 4 .. 3 + 2 * depth; then the leaves.
    first_leaf = 4 + 2 * depth
    make(
        tmp_path,
        sysview(
            opening + "<image\n"  # first_leaf
            '  bogus="1"\n'  # first_leaf + 1
            "/>\n"  # first_leaf + 2
            "<bogus\n"  # first_leaf + 3
            '  name="b"/>\n'  # first_leaf + 4
            "<image><pos\n>x</pos></image>\n" + closing.rstrip("\n")  # first_leaf + 5
        ),
    )
    result = run(tmp_path)
    _assert_lines(
        result,
        {"W004": [first_leaf], "E031": [first_leaf + 3], "E040": [first_leaf + 5]},
    )


def test_line_lone_cr_line_endings(tmp_path: Path) -> None:
    # XML 1.0 section 2.11: a lone CR is a line break; pugixml parse_eol normalises it too.
    text = (
        '<theme>\n<formatVersion>7</formatVersion>\n<view name="system">\n'
        '<image\n  name="a" bogus="1"/>\n</view>\n</theme>\n'
    )
    (tmp_path / "theme.xml").write_bytes(text.replace("\n", "\r").encode())
    _assert_lines(run(tmp_path), {"W004": [4]})


def test_line_numbers_beyond_65535_are_not_truncated(tmp_path: Path) -> None:
    blank = 70000
    make(
        tmp_path,
        sysview(
            "\n" * blank + '<image\n  name="a" bogus="1"/>\n<image name="b"><pos>x</pos></image>'
        ),
    )
    _assert_lines(run(tmp_path), {"W004": [4 + blank], "E040": [6 + blank]})


def test_line_storyboard_animations_use_their_start_tag_line(tmp_path: Path) -> None:
    make(
        tmp_path,
        sysview(
            '<image name="a">\n'  # 4
            "<storyboard\n"  # 5
            '  event="x">\n'  # 6
            "<animation\n"  # 7
            '  property="opacity"\n'  # 8
            '  from="x"\n'  # 9
            '  to="1"/>\n'  # 10
            "<animation\n"  # 11
            '  property="bogus"/>\n'  # 12
            "</storyboard>\n"  # 13
            "</image>"
        ),
    )
    _assert_lines(run(tmp_path), {"E040": [7], "E032": [11]})
