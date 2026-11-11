from __future__ import annotations

import pytest

from n64mini.theme_schema import Code, Severity, load_grammar, validate_theme
from tests.conftest import ThemeWriter, wrap

PNG = b"\x89PNG\r\n\x1a\n"  # contents are irrelevant; only existence is checked


def codes(writer: ThemeWriter, files: dict[str, str | bytes]) -> list[Code]:
    return [issue.code for issue in validate_theme(writer(files)).issues]


def test_minimal_valid_theme_has_no_issues(write_theme: ThemeWriter) -> None:
    theme = write_theme(
        {
            "theme.xml": wrap(
                """
  <variables><accent>EE2A30</accent></variables>
  <view name="system, grid">
    <image name="background" extra="true">
      <pos>0 0</pos><size>1 1</size><path>./art/bg.png</path><zIndex>5</zIndex>
    </image>
    <text name="md_name" pos="0.5 0.2">
      <color>${accent}</color><forceUppercase>true</forceUppercase>
      <padding>0.01</padding>
    </text>
  </view>
"""
            ),
            "art/bg.png": PNG,
        }
    )
    result = validate_theme(theme)
    assert result.issues == ()
    assert result.ok(warnings_as_errors=True)


V = "<view name='grid'>{}</view>"


@pytest.mark.parametrize(
    ("xml", "expected", "message"),
    [
        ("<theme><view name='grid'/></theme>", Code.FORMAT_VERSION, "<formatVersion> is missing"),
        (
            wrap("", version="2"),
            Code.FORMAT_VERSION,
            "formatVersion 2 is below the engine minimum 3",
        ),
        (wrap("", version="seven"), Code.FORMAT_VERSION, "'seven' is not a plain decimal number"),
        (
            "<themes><formatVersion>7</formatVersion></themes>",
            Code.MISSING_THEME_ROOT,
            "root element is <themes>, expected <theme>",
        ),
        ("<theme><unclosed></theme>", Code.XML_SYNTAX, "mismatch"),
        (wrap("<bogus/>"), Code.UNKNOWN_TOP_LEVEL, "<bogus> is not a valid child of <theme>"),
        (wrap("<view/>"), Code.MISSING_NAME, '<view> is missing its "name" attribute'),
        (wrap("<customView/>"), Code.MISSING_NAME, '<customView> is missing its "name" attribute'),
        (
            wrap("<view name='carousel_typo'/>"),
            Code.UNKNOWN_VIEW,
            "view 'carousel_typo' is not supported; expected one of basic, detailed",
        ),
        (wrap(V.format("<image/>")), Code.MISSING_NAME, '<image> is missing its "name" attribute'),
        (
            wrap(V.format("<picture name='x'/>")),
            Code.UNKNOWN_ELEMENT,
            "unknown element type <picture>",
        ),
        (
            wrap(V.format("<image name='x'><colour>FFF</colour></image>")),
            Code.UNKNOWN_PROPERTY,
            "<colour> is not a property of <image> (the engine ignores it)",
        ),
        (
            wrap(V.format("<image name='x'><pos>0.5</pos><size>1 2 3</size></image>")),
            Code.BAD_VALUE,
            "size='1 2 3' is not a valid pair ('x y')",
        ),
        (
            wrap(V.format("<image name='x'><zIndex>high</zIndex></image>")),
            Code.BAD_VALUE,
            "zIndex='high' is not a valid float",
        ),
        (
            wrap(V.format("<image name='x'><zIndex>1 2</zIndex></image>")),
            Code.BAD_VALUE,
            "zIndex='1 2' is not a valid float",
        ),
        (
            wrap(V.format("<imagegrid name='g'><padding>1 2 3</padding></imagegrid>")),
            Code.BAD_VALUE,
            "padding='1 2 3' is not a valid rect (1, 2 or 4 floats)",
        ),
        (
            wrap(V.format("<imagegrid name='g'><scrollLoop>yep</scrollLoop></imagegrid>")),
            Code.BAD_VALUE,
            "scrollLoop='yep' is not a valid boolean",
        ),
        (
            wrap(V.format("<imagegrid name='g'><animate>maybe</animate></imagegrid>")),
            Code.BAD_VALUE,
            "animate='maybe' is not a valid boolean",
        ),
        (
            wrap(V.format("<text name='t'><color>FFF</color></text>")),
            Code.BAD_VALUE,
            "color='FFF' is not a valid colour (RRGGBB or RRGGBBAA)",
        ),
        (
            wrap(V.format("<text name='t'><color>#FFF</color></text>")),
            Code.BAD_VALUE,
            "color='#FFF' is not a valid colour (#RRGGBB[AA])",
        ),
        (
            wrap(V.format("<text name='t'><color>#GGGGGG</color></text>")),
            Code.BAD_VALUE,
            "colour (#RRGGBB[AA])",
        ),
        (
            wrap(V.format("<text name='t'><color>notacolour</color></text>")),
            Code.BAD_VALUE,
            "color='notacolour' is not a valid colour name",
        ),
        (
            wrap(V.format("<image name='x'><path>./missing.png</path></image>")),
            Code.PATH_NOT_FOUND,
            "path='./missing.png' not found",
        ),
        (
            wrap(V.format("<menuIcons name='m'><anyIcon>./gone.png</anyIcon></menuIcons>")),
            Code.PATH_NOT_FOUND,
            "anyIcon='./gone.png' not found",
        ),
        (
            wrap(V.format("<image name='x'><shader>./crt.glsl</shader></image>")),
            Code.PATH_NOT_FOUND,
            "shader='./crt.glsl' not found",
        ),
        (
            wrap("<include>./nope.xml</include>"),
            Code.INCLUDE_NOT_FOUND,
            "included file './nope.xml' not found",
        ),
        (
            wrap("<subset name='s'><include name='a'>./nope.xml</include></subset>"),
            Code.INCLUDE_NOT_FOUND,
            "included file './nope.xml' not found",
        ),
        (
            wrap("<feature supported='video'><view name='nope'/></feature>"),
            Code.UNKNOWN_VIEW,
            "view 'nope' is not supported",
        ),
        (
            wrap(V.format("<text name='t'><color>#EE2A30</color></text>")),
            Code.HASH_COLOR_ALPHA,
            "write EE2A30 or EE2A30FF instead",
        ),
        (
            wrap(V.format("<text name='t'><fontPath>/usr/x.ttf</fontPath></text>")),
            Code.ABSOLUTE_PATH,
            "fontPath='/usr/x.ttf' is an absolute device path",
        ),
        (
            wrap(V.format("<text name='t'><color>${undefinedVar}</color></text>")),
            Code.UNDEFINED_VARIABLE,
            "variable ${undefinedVar} is not defined",
        ),
        (
            wrap(V.format("<image name='x' wobble='1'/>")),
            Code.UNKNOWN_ATTRIBUTE,
            "attribute 'wobble' is ignored on <image>",
        ),
        (
            wrap(V.format("<image name='x' pos='left'/>")),
            Code.BAD_VALUE,
            "pos='left' is not a valid pair",
        ),
    ],
)
def test_each_rule_fires_alone(
    write_theme: ThemeWriter, xml: str, expected: Code, message: str
) -> None:
    (issue,) = validate_theme(write_theme({"theme.xml": xml})).issues
    assert issue.code is expected
    assert message in issue.message


@pytest.mark.parametrize(
    "fragment",
    [
        "<image name='x'><path>none</path></image>",  # "none" clears a path
        "<image name='x'><path></path></image>",
        "<image name='x'><path>{game:image}</path></image>",  # runtime binding
        "<text name='t'><color>transparent</color></text>",
        "<text name='t'><color>red</color></text>",
        "<text name='t'><color>c0ffee</color></text>",  # lowercase hex, not a name
        "<text name='t'><color>#EE2A30FF</color></text>",
        "<text name='t'><text>${screen.ratio}</text></text>",  # engine variable in a STRING
        "<image name='x'><zIndex>-1.5</zIndex><size>0.5</size></image>",
        "<imagegrid name='g'><padding>0.1</padding><margin>0.1 0.2</margin></imagegrid>",
        "<imagegrid name='g'><padding>1 2 3 4</padding><scrollLoop>FALSE</scrollLoop></imagegrid>",
        "<image name='x' lang='fr' region='eu' importProperties='y'/>",
        "<text name='t'><text>plain property</text></text>",
    ],
)
def test_values_the_engine_accepts(write_theme: ThemeWriter, fragment: str) -> None:
    assert codes(write_theme, {"theme.xml": wrap(V.format(fragment))}) == []


def test_variables_are_resolved_before_value_checks(write_theme: ThemeWriter) -> None:
    """A variable's *value* is validated, not the placeholder text."""
    xml = wrap(
        "<variables><bad>notacolour</bad><good>EE2A30</good><nested>${good}</nested></variables>"
        + V.format(
            "<text name='a'><color>${bad}</color></text>"
            "<text name='b'><color>${nested}</color></text>"
        )
    )
    (issue,) = validate_theme(write_theme({"theme.xml": xml})).issues
    assert issue.code is Code.BAD_VALUE
    assert issue.message == "color='${bad}' is not a valid colour name"


@pytest.mark.parametrize("path", ["./${screen.ratio}/x.xml", "./$language/x.xml"])
def test_runtime_include_is_reported_not_skipped(write_theme: ThemeWriter, path: str) -> None:
    """AC-V17: an include that depends on a console-only variable is W005, never silent."""
    xml = wrap(f"<include>{path}</include><include></include>")
    result = validate_theme(write_theme({"theme.xml": xml}))
    (issue,) = result.issues
    assert issue.code is Code.RUNTIME_UNCHECKED
    assert "not checked" in issue.message
    assert len(result.files) == 1


def test_runtime_value_is_reported_not_skipped(write_theme: ThemeWriter) -> None:
    xml = wrap(V.format("<text name='t'><color>${global.help}</color></text>"))
    (issue,) = validate_theme(write_theme({"theme.xml": xml})).issues
    assert issue.code is Code.RUNTIME_UNCHECKED
    assert "${global.help}" in issue.message


def test_known_system_variables_never_cause_w005(write_theme: ThemeWriter) -> None:
    files: dict[str, str | bytes] = {
        "theme.xml": wrap("<include>./${system.theme}/x.xml</include>"),
        "n64/x.xml": "<theme>"
        + V.format("<text name='t'><text>${system.name}</text></text>")
        + "</theme>",
    }
    found = codes(write_theme, files)
    assert Code.RUNTIME_UNCHECKED not in found
    assert found == [Code.INCLUDE_NOT_FOUND]  # only the collections, which lack a folder (AC-V22)


def test_included_files_may_omit_format_version(write_theme: ThemeWriter) -> None:
    theme = write_theme(
        {"theme.xml": wrap("<include>./inc.xml</include>"), "inc.xml": "<theme></theme>"}
    )
    assert validate_theme(theme).issues == ()


def test_fractional_format_version_is_accepted(write_theme: ThemeWriter) -> None:
    assert codes(write_theme, {"theme.xml": wrap("", version="3")}) == []
    assert codes(write_theme, {"theme.xml": wrap("", version="6.5")}) == []


def test_ok_respects_warnings_as_errors(write_theme: ThemeWriter) -> None:
    xml = wrap(V.format("<text name='t'><color>#EE2A30</color></text>"))
    result = validate_theme(write_theme({"theme.xml": xml}))
    assert result.ok()
    assert not result.ok(warnings_as_errors=True)


def test_extra_variables_are_used(write_theme: ThemeWriter) -> None:
    xml = wrap(V.format("<text name='t'><color>${my.colour}</color></text>"))
    theme = write_theme({"theme.xml": xml})
    assert [i.code for i in validate_theme(theme).issues] == [Code.UNDEFINED_VARIABLE]
    ok = validate_theme(theme, extra_variables={"my.colour": "FFFFFF"})
    assert ok.issues == ()


def test_errors_and_warnings_are_classified(write_theme: ThemeWriter) -> None:
    theme = write_theme(
        {
            "theme.xml": wrap(
                "<view name='grid'><text name='t'><color>#EE2A30</color>"
                "<bogus>1</bogus></text></view>"
            )
        }
    )
    result = validate_theme(theme)
    assert [i.severity for i in result.errors] == [Severity.ERROR]
    assert [i.severity for i in result.warnings] == [Severity.WARNING]
    assert not result.ok()


def test_issue_reports_file_and_line(write_theme: ThemeWriter) -> None:
    theme = write_theme(
        {"theme.xml": wrap("<view name='grid'>\n<image name='x'>\n<nope/>\n</image></view>")}
    )
    (issue,) = validate_theme(theme).issues
    assert issue.file == theme
    assert issue.line == 6
    assert issue.format(theme.parent) == (
        "theme.xml:6: error E031: <nope> is not a property of <image> (the engine ignores it)"
    )


def test_includes_resolve_system_variable_and_share_variables(write_theme: ThemeWriter) -> None:
    theme = write_theme(
        {
            "theme.xml": wrap(
                "<include>./config.xml</include><include>./${system.theme}/custom.xml</include>"
            ),
            "config.xml": wrap("<variables><gold>FFDC38</gold></variables>"),
            "n64/custom.xml": "<theme><view name='grid'><text name='t'>"
            "<color>${gold}</color><text>hi</text></text></view></theme>",
        }
    )
    result = validate_theme(theme, system="n64")
    (collections,) = result.issues  # the built-in collections have no folder (AC-V22)
    assert "auto-allgames" in collections.message
    assert [p.name for p in result.files] == ["theme.xml", "config.xml", "custom.xml"]

    other = validate_theme(theme, system="snes")
    assert [i.code for i in other.issues] == [Code.INCLUDE_NOT_FOUND] * 2  # snes + collections


def test_view_cannot_target_a_custom_view_name(write_theme: ThemeWriter) -> None:
    """ThemeData::parseViewElement only accepts sSupportedViews; custom ones are dropped."""
    xml = wrap("<view name='grid'/><customView name='cards' inherits='grid'/><view name='cards'/>")
    assert codes(write_theme, {"theme.xml": xml}) == [Code.UNKNOWN_VIEW]


def test_include_cycle_is_reported_once(write_theme: ThemeWriter) -> None:
    theme = write_theme(
        {
            "theme.xml": wrap("<include>./a.xml</include>"),
            "a.xml": wrap("<include>./theme.xml</include>"),
        }
    )
    assert [i.code for i in validate_theme(theme).issues] == [Code.INCLUDE_CYCLE]


def test_nested_structures_follow_engine_rules(write_theme: ThemeWriter) -> None:
    body = """
  <view name="grid"/>
  <customView name="n64cards" inherits="grid">
    <clock name="clk"><fontPath>./f.ttf</fontPath><color>FFFFFF</color></clock>
    <imagegrid name="gamegrid">
      <animate>true</animate>
      <itemTemplate><image name="frame"><path>./f.ttf</path></image></itemTemplate>
    </imagegrid>
    <stackpanel name="sp"><image name="inner"><pos>0 0</pos></image></stackpanel>
    <image name="art"><storyboard event="activate"><animation property="x"/></storyboard>
      <path>{game:image}</path></image>
    <screenshader name="crt"><path>./f.ttf</path><anyUniform>0.5</anyUniform></screenshader>
  </customView>
"""
    theme = write_theme({"theme.xml": wrap(body), "f.ttf": b""})
    assert [i.format() for i in validate_theme(theme).issues] == []


def test_include_asset_falls_back_to_theme_root(write_theme: ThemeWriter) -> None:
    """AC-V12 / BLIND-1: inside an include, './x' falls back to the entry theme's directory."""
    files: dict[str, str | bytes] = {
        "theme.xml": wrap("<include>./sub/inc.xml</include>"),
        "sub/inc.xml": "<theme>"
        + V.format("<image name='i'><path>./bg.png</path></image>")
        + "</theme>",
        "bg.png": PNG,
    }
    assert codes(write_theme, files) == []


def test_theme_root_fallback_is_only_for_includes_and_dot_paths(write_theme: ThemeWriter) -> None:
    """The fallback is the engine's, not a general search: bare paths don't get it."""
    files: dict[str, str | bytes] = {
        "theme.xml": wrap("<include>./sub/inc.xml</include>"),
        "sub/inc.xml": "<theme>"
        + V.format("<image name='i'><path>art/bg.png</path></image>")
        + "</theme>",
        "art/bg.png": PNG,
    }
    (issue,) = validate_theme(write_theme(files)).issues
    assert issue.code is Code.NOT_DOT_RELATIVE  # a bare path never gets the fallback (AC-V21)
    assert "art/bg.png" in issue.message


def test_missing_include_asset_lists_every_location_tried(write_theme: ThemeWriter) -> None:
    files: dict[str, str | bytes] = {
        "theme.xml": wrap("<include>./sub/inc.xml</include>"),
        "sub/inc.xml": "<theme>"
        + V.format("<image name='i'><path>./gone.png</path></image>")
        + "</theme>",
    }
    theme = write_theme(files)
    (issue,) = validate_theme(theme).issues
    assert issue.code is Code.PATH_NOT_FOUND
    assert str(theme.parent / "sub" / "gone.png") in issue.message
    assert str(theme.parent / "gone.png") in issue.message


def test_variable_attribute_form_defines_a_variable(write_theme: ThemeWriter) -> None:
    """AC-V15 / BLIND-2: <variable name=.. value=../> defines a variable (placeholders resolved)."""
    body = (
        "<variables><base>EE2A30</base><variable name='accent' value='${base}'/>"
        "<variable name='bad' value='notacolour'/></variables>"
        + V.format("<text name='a'><color>${accent}</color></text>")
    )
    assert codes(write_theme, {"theme.xml": wrap(body)}) == []
    bad = wrap(
        "<variables><variable name='bad' value='notacolour'/></variables>"
        + V.format("<text name='a'><color>${bad}</color></text>")
    )
    (issue,) = validate_theme(write_theme({"theme.xml": bad})).issues
    assert issue.code is Code.BAD_VALUE  # the value is checked, so the variable was defined


def test_nested_elements_may_omit_name(write_theme: ThemeWriter) -> None:
    """AC-V07: only direct children of a view need a name; the engine keys nested ones by tag."""
    xml = wrap(V.format("<stackpanel name='sp'><image><pos>0 0</pos></image></stackpanel>"))
    assert codes(write_theme, {"theme.xml": xml}) == []


def test_nameless_unknown_element_reports_only_e020(write_theme: ThemeWriter) -> None:
    assert codes(write_theme, {"theme.xml": wrap(V.format("<picture/>"))}) == [Code.MISSING_NAME]


def test_empty_view_name_says_empty(write_theme: ThemeWriter) -> None:
    (issue,) = validate_theme(write_theme({"theme.xml": wrap("<view name=''/>")})).issues
    assert issue.code is Code.MISSING_NAME
    assert "empty" in issue.message


@pytest.mark.parametrize(
    "value",
    ["1e3", "1E3", "inf", "-inf", "nan", "0x10", "5f", "1_000", "0,5", ".", "-", "+", "--1"],
)
def test_numbers_the_engine_reads_as_zero_are_errors(write_theme: ThemeWriter, value: str) -> None:
    """AC-V11: the engine's toFloat returns 0 for any non-digit, so these must not pass."""
    xml = wrap(V.format(f"<image name='i'><zIndex>{value}</zIndex></image>"))
    assert codes(write_theme, {"theme.xml": xml}) == [Code.BAD_VALUE]


@pytest.mark.parametrize(
    "fragment",
    [
        "<image name='i'><pos>\n0.5 0.5\n</pos></image>",
        "<image name='i'><pos>0.5\t0.5</pos></image>",
        "<image name='i'><pos> 0.5 0.5</pos></image>",  # engine reads X as 0
        "<image name='i'><pos>0.5 </pos></image>",  # engine reads Y as 0
        "<image name='i'><zIndex>\n5</zIndex></image>",
        "<imagegrid name='g'><scrollLoop> true</scrollLoop></imagegrid>",  # engine reads false
        "<text name='t'><color> FFFFFF</color></text>",
        "<text name='t'><color>FFFFFF </color></text>",
        "<image name='i' pos=' 0.5 0.5'/>",
    ],
)
def test_whitespace_the_engine_does_not_trim_is_an_error(
    write_theme: ThemeWriter, fragment: str
) -> None:
    xml = wrap(V.format(fragment))
    assert codes(write_theme, {"theme.xml": xml}) == [Code.BAD_VALUE]


def test_spaces_between_numbers_are_fine(write_theme: ThemeWriter) -> None:
    xml = wrap(V.format("<image name='i'><pos>0.5   0.5</pos><zIndex>-.5</zIndex></image>"))
    assert codes(write_theme, {"theme.xml": xml}) == []


def test_variables_after_a_view_still_apply(write_theme: ThemeWriter) -> None:
    """AC-V15: a file's <variables> are read before its views."""
    xml = wrap(
        V.format("<text name='t'><color>${late}</color></text>")
        + "<variables><late>FFFFFF</late></variables>"
    )
    assert codes(write_theme, {"theme.xml": xml}) == []


@pytest.mark.parametrize(
    ("name", "defined"),
    [
        ("themePath", True),
        ("lang", True),
        ("screen.ratio", True),
        ("global.help", True),
        ("system.fullName", True),
        ("settings.ShowHelpPrompts", True),
        ("subset.colorset", True),
        ("theme.anything", False),  # was wrongly allowed by the old hand-typed prefix list
        ("language", False),  # 'lang' is an exact name, not a prefix
        ("cheevosX", False),
        ("screen.colour", False),
    ],
)
def test_only_real_engine_variables_are_defined(
    write_theme: ThemeWriter, name: str, *, defined: bool
) -> None:
    """AC-V15: engine variables come from the vendored source, not a hand-typed list."""
    xml = wrap(V.format(f"<text name='t'><text>${{{name}}}</text></text>"))
    assert codes(write_theme, {"theme.xml": xml}) == ([] if defined else [Code.UNDEFINED_VARIABLE])


def test_nested_text_and_shader_elements_are_validated(write_theme: ThemeWriter) -> None:
    """<text> with element children is a nested element; <shader> with children is a shader."""
    body = V.format(
        "<text name='t'><text name='inner'><pos>0 0</pos><bogus/></text></text>"
        "<image name='i'><shader><path>./crt.glsl</path><anyUniform>1</anyUniform></shader></image>"
    )
    theme = write_theme({"theme.xml": wrap(body), "crt.glsl": b""})
    assert [(i.code, i.message.split(" ")[0]) for i in validate_theme(theme).issues] == [
        (Code.UNKNOWN_PROPERTY, "<bogus>")
    ]


# ---------------------------------------------------------------- BLIND-3..10 rulings, 2026-11-05


def test_subset_is_ignored_before_format_7(write_theme: ThemeWriter) -> None:
    """AC-V04 / BLIND-8: formatVersion <= 6 never loads <subset>."""
    sub = "<subset name='s'><include name='a'>./a.xml</include></subset>"
    files: dict[str, str | bytes] = {"a.xml": "<theme></theme>"}
    assert codes(write_theme, {**files, "theme.xml": wrap(sub, version="7")}) == []
    assert codes(write_theme, {**files, "theme.xml": wrap(sub, version="6")}) == [
        Code.UNKNOWN_TOP_LEVEL
    ]


def test_feature_only_reads_views(write_theme: ThemeWriter) -> None:
    xml = wrap("<feature supported='video'><image name='x'/></feature>")
    assert codes(write_theme, {"theme.xml": xml}) == [Code.UNKNOWN_TOP_LEVEL]


@pytest.mark.parametrize(
    ("feature", "expected"),
    [
        ("<feature supported='video'><view name='grid'/></feature>", []),
        ("<feature><view name='grid'/></feature>", [Code.BAD_FEATURE]),
        ("<feature supported='hologram'><view name='grid'/></feature>", [Code.BAD_FEATURE]),
        ("<feature supported='video, carousel'><view name='grid'/></feature>", [Code.BAD_FEATURE]),
        (
            "<feature supported='manufacturer'><view name='grid'/></feature>",
            [Code.RUNTIME_UNCHECKED],
        ),
    ],
)
def test_feature_needs_one_supported_value(
    write_theme: ThemeWriter, feature: str, expected: list[Code]
) -> None:
    """AC-V19 / BLIND-6, BLIND-7."""
    assert codes(write_theme, {"theme.xml": wrap(feature)}) == expected


@pytest.mark.parametrize("path", [" ./a.xml", "./a.xml ", "\n  ./a.xml\n"])
def test_padded_include_path_is_not_found(write_theme: ThemeWriter, path: str) -> None:
    """AC-V05 / BLIND-5: the engine doesn't trim include text."""
    files: dict[str, str | bytes] = {
        "theme.xml": wrap(f"<include>{path}</include>"),
        "a.xml": "<theme/>",
    }
    assert codes(write_theme, files) == [Code.INCLUDE_NOT_FOUND]


@pytest.mark.parametrize(
    ("fragment", "expected"),
    [
        ("<image name='i'><pos>{game:x}</pos></image>", [Code.BAD_VALUE]),
        ("<imagegrid name='g'><padding>{game:x}</padding></imagegrid>", [Code.BAD_VALUE]),
        ("<image name='i'><zIndex>{game:x}</zIndex></image>", []),
        ("<text name='t'><color>{game:x}</color></text>", []),
        ("<image name='i'><path>{game:image}</path></image>", []),
    ],
)
def test_binding_in_pair_or_rect_is_an_error(
    write_theme: ThemeWriter, fragment: str, expected: list[Code]
) -> None:
    """AC-V11 / BLIND-3: pairs and rects have no binding support in the engine."""
    assert codes(write_theme, {"theme.xml": wrap(V.format(fragment))}) == expected


@pytest.mark.parametrize(
    ("view", "element", "ok"),
    [
        ("system", "<image name='r' extra='true'><path>{random}</path></image>", True),
        ("system", "<image name='r' extra='static'><path>{random:fanart}</path></image>", True),
        ("system", "<video name='r' extra='true'><path>{random}</path></video>", True),
        ("system", "<image name='r'><path>{random}</path></image>", False),  # not an extra
        ("detailed", "<image name='r' extra='true'><path>{random}</path></image>", False),
        ("system, grid", "<image name='r' extra='true'><path>{random}</path></image>", False),
        ("system", "<image name='r' extra='true'><path>{random:bogus}</path></image>", False),
        ("system", "<video name='r' extra='true'><path>{random:image}</path></video>", False),
        ("system", "<text name='r' extra='true'><fontPath>{random}</fontPath></text>", False),
    ],
)
def test_random_is_only_allowed_where_the_engine_supports_it(
    write_theme: ThemeWriter, view: str, element: str, *, ok: bool
) -> None:
    """AC-V18 / BLIND-4."""
    xml = wrap(f"<view name='{view}'>{element}</view>")
    assert codes(write_theme, {"theme.xml": xml}) == ([] if ok else [Code.BAD_RANDOM])


@pytest.mark.parametrize(
    ("storyboard", "expected"),
    [
        ("<storyboard event='x'><animation property='opacity' from='0' to='1'/></storyboard>", []),
        ("<storyboard event='x'><animation property='shader.blur'/></storyboard>", []),
        # no a.wav here, so the sound is skipped and the storyboard is empty (BLIND-24)
        ("<storyboard event='x'><sound path='./a.wav'/></storyboard>", [Code.BAD_STORYBOARD] * 2),
        ("<storyboard event='x'><foo/></storyboard>", [Code.BAD_STORYBOARD, Code.BAD_STORYBOARD]),
        ("<storyboard event='x'/>", []),  # removes the event's storyboard: valid
        (
            "<storyboard event='x'><animation/></storyboard>",
            [Code.BAD_STORYBOARD, Code.BAD_STORYBOARD],
        ),
        (
            "<storyboard event='x'><animation property='nope'/></storyboard>",
            [Code.BAD_STORYBOARD, Code.BAD_STORYBOARD],
        ),
    ],
)
def test_storyboard_needs_a_usable_animation(
    write_theme: ThemeWriter, storyboard: str, expected: list[Code]
) -> None:
    """AC-V20 / BLIND-9: one issue per unusable animation, plus one for an empty storyboard."""
    xml = wrap(V.format(f"<image name='i'>{storyboard}</image>"))
    assert sorted(codes(write_theme, {"theme.xml": xml})) == sorted(expected)


def test_storyboard_properties_use_the_elements_own_list(write_theme: ThemeWriter) -> None:
    """The engine passes the element's own type map: clock inherits color, but can't animate it."""
    sb = "<storyboard event='x'><animation property='color'/></storyboard>"
    xml = wrap(V.format(f"<clock name='c'>{sb}</clock>"))
    assert codes(write_theme, {"theme.xml": xml}) == [Code.BAD_STORYBOARD, Code.BAD_STORYBOARD]


def test_if_expressions_are_reported_unchecked(write_theme: ThemeWriter) -> None:
    """AC-V17 / BLIND-10 (interim): an if= expression can't be checked yet, so it's W005."""
    xml = wrap(V.format("<image name='i' if='${screen.width} > 100'/><image name='j' if=''/>"))
    (issue,) = validate_theme(write_theme({"theme.xml": xml})).issues
    assert issue.code is Code.RUNTIME_UNCHECKED
    assert "if" in issue.message


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("./a.png", True),
        ("a.png", False),
        ("art/a.png", False),
        ("../a.png", False),
        ("./../x/a.png", True),
    ],
)
def test_paths_must_start_with_dot_slash(write_theme: ThemeWriter, value: str, *, ok: bool) -> None:
    """AC-V21: the engine resolves only ./ paths against the theme; others against the CWD."""
    files: dict[str, str | bytes] = {
        "t/theme.xml": wrap(V.format(f"<image name='i'><path>{value}</path></image>")),
        "t/a.png": PNG,
        "t/art/a.png": PNG,
        "x/a.png": PNG,
    }
    theme = write_theme(files).parent / "t" / "theme.xml"
    assert [i.code for i in validate_theme(theme).issues] == ([] if ok else [Code.NOT_DOT_RELATIVE])


def test_include_paths_must_start_with_dot_slash(write_theme: ThemeWriter) -> None:
    files: dict[str, str | bytes] = {
        "theme.xml": wrap("<include>a.xml</include>"),
        "a.xml": "<theme/>",
    }
    assert codes(write_theme, files) == [Code.NOT_DOT_RELATIVE]


def test_theme_path_variables_are_checked_not_warned(write_theme: ThemeWriter) -> None:
    """AC-V12/V15: ${themePath} etc. are the entry theme's folder, so they're checked statically."""
    ok = wrap(V.format("<image name='i'><path>${themePath}/a.png</path></image>"))
    assert codes(write_theme, {"theme.xml": ok, "a.png": PNG}) == []
    missing = wrap(V.format("<image name='i'><path>${root}/gone.png</path></image>"))
    assert codes(write_theme, {"theme.xml": missing}) == [Code.PATH_NOT_FOUND]


@pytest.mark.parametrize("value", [" ./a.png", "./a.png ", "\n./a.png\n"])
def test_padded_asset_path_is_not_found(write_theme: ThemeWriter, value: str) -> None:
    """AC-V11/V12: the engine doesn't trim PATH text, so a padded path never resolves."""
    xml = wrap(V.format(f"<image name='i'><path>{value}</path></image>"))
    assert codes(write_theme, {"theme.xml": xml, "a.png": PNG}) == [Code.PATH_NOT_FOUND]


# ---------------------------------------------------------------- BLIND-11..26 + real-engine run


@pytest.mark.parametrize(
    ("version", "ok"), [("7", True), ("6.5", True), ("1e1", False), ("nan", False)]
)
def test_format_version_is_a_plain_decimal(
    write_theme: ThemeWriter, version: str, *, ok: bool
) -> None:
    """AC-V03 / BLIND-16."""
    assert codes(write_theme, {"theme.xml": wrap("", version=version)}) == (
        [] if ok else [Code.FORMAT_VERSION]
    )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("stray words", [Code.UNKNOWN_TOP_LEVEL]),  # BLIND-17
        ("<subset name='s'><view name='grid'/></subset>", [Code.UNKNOWN_TOP_LEVEL]),  # BLIND-18
        ("<view name='grid'>oops <image name='i'/></view>", [Code.MISSING_NAME]),  # BLIND-19
        (
            "<view name='grid'><image name='i'>oops<pos>0 0</pos></image></view>",
            [Code.UNKNOWN_PROPERTY],
        ),
        ("<view name='grid'><image name='i'>   </image></view>", []),  # whitespace-only is dropped
    ],
)
def test_stray_text_and_subset_children_are_reported(
    write_theme: ThemeWriter, body: str, expected: list[Code]
) -> None:
    """AC-V04/V07/V10: non-whitespace text where the engine expects elements."""
    assert codes(write_theme, {"theme.xml": wrap(body)}) == expected


def test_custom_view_must_inherit_a_known_view(write_theme: ThemeWriter) -> None:
    """AC-V08: an unknown inherits= is silently ignored by the engine."""
    ok = wrap("<view name='grid'/><customView name='c' inherits='grid'/>")
    bad = wrap("<customView name='c' inherits='nope'/>")
    assert codes(write_theme, {"theme.xml": ok}) == []
    assert codes(write_theme, {"theme.xml": bad}) == [Code.UNKNOWN_VIEW]


@pytest.mark.parametrize(
    ("inner", "expected"),
    [
        ("<text>plain</text>", []),
        ("<text name='n'><pos>0 0</pos></text>", []),  # first child is an element: nested text
        ("<text>hello<pos>0 0</pos></text>", [Code.UNKNOWN_PROPERTY]),  # BLIND-11: <pos> dropped
    ],
)
def test_text_in_text_follows_the_first_child(
    write_theme: ThemeWriter, inner: str, expected: list[Code]
) -> None:
    """AC-V10 / BLIND-11: the engine looks only at the first child."""
    xml = wrap(V.format(f"<text name='t'>{inner}</text>"))
    assert codes(write_theme, {"theme.xml": xml}) == expected


def test_menu_shaders_only_under_menu_background(write_theme: ThemeWriter) -> None:
    """AC-V10 / BLIND-20."""
    bad = wrap(V.format("<image name='i'><menuShader>./x.glsl</menuShader></image>"))
    ok = (
        "<theme><formatVersion>7</formatVersion><view name='menu'>"
        "<menuBackground name='b'><menuShader>./x.glsl</menuShader></menuBackground></view></theme>"
    )
    assert codes(write_theme, {"theme.xml": bad, "x.glsl": b""}) == [Code.UNKNOWN_PROPERTY]
    assert codes(write_theme, {"theme.xml": ok, "x.glsl": b""}) == []


@pytest.mark.parametrize(
    "value",
    ["0.5 <!-- c -->0.25", "0.5 <![CDATA[0.25]]>"],
)
def test_split_values_are_read_like_the_engine(write_theme: ThemeWriter, value: str) -> None:
    """AC-V11 / BLIND-22: pugixml's text() is the first text node only, so Y would be lost."""
    xml = wrap(V.format(f"<image name='i'><pos>{value}</pos></image>"))
    assert codes(write_theme, {"theme.xml": xml}) == [Code.BAD_VALUE]


def test_whitespace_only_values_are_empty(write_theme: ThemeWriter) -> None:
    """pugixml drops whitespace-only text: an empty colour is valid, an empty include is skipped."""
    xml = wrap("<include>   </include>" + V.format("<text name='t'><color>  </color></text>"))
    assert codes(write_theme, {"theme.xml": xml}) == []


def test_current_path_is_the_including_files_folder(write_theme: ThemeWriter) -> None:
    """AC-V15 / BLIND-13, BLIND-14."""
    inc = (
        "<theme>"
        + V.format("<image name='i'><path>${currentPath}/bg.png</path></image>")
        + "</theme>"
    )
    main = wrap("<include>./sub/inc.xml</include>")
    root = write_theme(
        {
            "a/theme.xml": main,
            "a/sub/inc.xml": inc,
            "a/sub/bg.png": PNG,
            "b/theme.xml": main,
            "b/sub/inc.xml": inc,
            "b/bg.png": PNG,
        }
    ).parent
    assert validate_theme(root / "a" / "theme.xml").issues == ()
    assert [i.code for i in validate_theme(root / "b" / "theme.xml").issues] == [
        Code.PATH_NOT_FOUND
    ]


@pytest.mark.parametrize(
    ("name", "defined"), [("settings.ShowHelpPrompts", True), ("settings.a.b", False)]
)
def test_settings_variables_have_one_segment(
    write_theme: ThemeWriter, name: str, *, defined: bool
) -> None:
    """AC-V15 / BLIND-15: the engine skips settings whose name contains a dot."""
    xml = wrap(V.format(f"<text name='t'><text>${{{name}}}</text></text>"))
    assert codes(write_theme, {"theme.xml": xml}) == ([] if defined else [Code.UNDEFINED_VARIABLE])


def test_arch_filters_are_known_attributes(write_theme: ThemeWriter) -> None:
    xml = wrap(V.format("<image name='i' ifArch='x86_64' ifNotArch='rpi4'/>"))
    assert codes(write_theme, {"theme.xml": xml}) == []


@pytest.mark.parametrize(
    ("storyboard", "files", "expected"),
    [
        ("<sound path='./a.wav'/>", {"a.wav": b""}, []),
        ("<sound path='./missing.wav'/>", {}, [Code.BAD_STORYBOARD, Code.BAD_STORYBOARD]),
        ("<sound/>", {}, [Code.BAD_STORYBOARD, Code.BAD_STORYBOARD]),
        ("<sound path='a.wav'/>", {"a.wav": b""}, [Code.BAD_STORYBOARD, Code.BAD_STORYBOARD]),
        ("<animation property='opacity' from='0' to='1e3'/>", {}, [Code.BAD_VALUE]),  # BLIND-12
        ("<animation property='shader.x' to='nan'/>", {}, [Code.BAD_VALUE]),
        ("words", {}, [Code.BAD_STORYBOARD] * 2),  # BLIND-25: text, and so no usable animation
    ],
)
def test_storyboard_sounds_and_values_are_checked(
    write_theme: ThemeWriter, storyboard: str, files: dict[str, bytes], expected: list[Code]
) -> None:
    """AC-V20 / BLIND-12, BLIND-24, BLIND-25."""
    xml = wrap(V.format(f"<image name='i'><storyboard event='x'>{storyboard}</storyboard></image>"))
    assert sorted(codes(write_theme, {"theme.xml": xml, **files})) == sorted(expected)


def test_backslash_dot_paths_resolve(write_theme: ThemeWriter) -> None:
    """AC-V21 / BLIND-26: the engine converts '\\' to '/' after a '.\\' prefix."""
    xml = wrap(V.format("<image name='i'><path>.\\art\\bg.png</path></image>"))
    assert codes(write_theme, {"theme.xml": xml, "art/bg.png": PNG}) == []


def test_theme_path_includes_resolve(write_theme: ThemeWriter) -> None:
    """AC-V21 / BLIND-23: ${themePath} makes an absolute path inside the theme; it's checked."""
    files: dict[str, str | bytes] = {
        "theme.xml": wrap("<include>${themePath}/n64/inc.xml</include>"),
        "n64/inc.xml": "<theme/>",
    }
    assert codes(write_theme, files) == []


def test_system_dependent_include_is_checked_for_every_collection(write_theme: ThemeWriter) -> None:
    """AC-V22: what the real engine found on 2026-11-05."""
    files: dict[str, str | bytes] = {
        "theme.xml": wrap("<include>./${system.theme}/custom.xml</include>"),
        "n64/custom.xml": "<theme/>",
    }
    (issue,) = validate_theme(write_theme(files)).issues
    assert issue.code is Code.INCLUDE_NOT_FOUND
    for collection in ("auto-allgames", "auto-favorites", "custom-collections"):
        assert collection in issue.message


def test_dollar_system_falls_back_to_default(write_theme: ThemeWriter) -> None:
    """AC-V22: the engine's designed form works for every system, known or not."""
    main = wrap("<include>./$system/custom.xml</include>")
    root = write_theme(
        {
            "a/theme.xml": main,
            "a/n64/custom.xml": "<theme/>",
            "a/default/custom.xml": "<theme/>",
            "b/theme.xml": main,
            "b/n64/custom.xml": "<theme/>",
        }
    ).parent
    assert validate_theme(root / "a" / "theme.xml").issues == ()
    (issue,) = validate_theme(root / "b" / "theme.xml").issues
    assert issue.code is Code.INCLUDE_NOT_FOUND
    assert "Add a 'default' folder" in issue.message


# ---------------------------------------------------------------- BLIND-27..30, run 4


def test_format_6_reads_views_before_custom_views(write_theme: ThemeWriter) -> None:
    """AC-V08 / BLIND-27: in format <= 6 every <view> is read before any <customView>."""
    body = "<customView name='mine' inherits='system'/><view name='system'/>"
    assert codes(write_theme, {"theme.xml": wrap(body, version="6")}) == []
    assert codes(write_theme, {"theme.xml": wrap(body, version="7")}) == [Code.UNKNOWN_VIEW]


def test_cdata_led_and_cdata_split_values(write_theme: ThemeWriter) -> None:
    """AC-V11 / BLIND-28: the first non-whitespace text or CDATA node is the value."""
    for value, expected in [
        ("0.5<![CDATA[ 0.7]]>", []),  # engine reads "0.5": a valid pair
        ("<![CDATA[0.5 0.7]]>", []),  # starts with CDATA: its content
        ("0.5 <![CDATA[0.7]]>", [Code.BAD_VALUE]),  # "0.5 ": trailing space zeroes Y
    ]:
        xml = wrap(V.format(f"<image name='i'><pos>{value}</pos></image>"))
        assert codes(write_theme, {"theme.xml": xml}) == expected, value


def test_asset_path_must_be_a_file(write_theme: ThemeWriter) -> None:
    """AC-V12: a directory doesn't count."""
    xml = wrap(V.format("<image name='i'><path>./art</path></image>"))
    assert codes(write_theme, {"theme.xml": xml, "art/x.png": PNG}) == [Code.PATH_NOT_FOUND]


def _all_folders(name: str) -> dict[str, str | bytes]:
    return {f"{f}/{name}": "<theme/>" for f in ("n64", *load_grammar().collection_systems)}


def test_aliased_systems_use_the_alias_folder(write_theme: ThemeWriter) -> None:
    """AC-V22 / BLIND-29: auto-favorites never uses its own folder for a relative $system."""
    files: dict[str, str | bytes] = {
        "theme.xml": wrap("<include>./$system/x.xml</include>"),
        **_all_folders("x.xml"),
    }
    (issue,) = validate_theme(write_theme(files)).issues  # no favorites/ and no default/
    assert issue.code is Code.INCLUDE_NOT_FOUND
    assert "auto-favorites" in issue.message
    assert "auto-allgames" not in issue.message


def test_default_fallback_sticks_for_the_rest_of_the_load(write_theme: ThemeWriter) -> None:
    """AC-V22 / BLIND-30: after a default fallback in the entry file, $system means default."""
    files: dict[str, str | bytes] = {
        "theme.xml": wrap("<include>./$system/a.xml</include><include>./$system/b.xml</include>"),
        "default/a.xml": "<theme/>",
        **_all_folders("b.xml"),
    }
    found = validate_theme(write_theme(files)).issues
    assert [i.code for i in found] == [Code.INCLUDE_NOT_FOUND, Code.INCLUDE_NOT_FOUND]
    assert all("b.xml" in i.message for i in found)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "<subset name='s'>words<include name='a'>./a.xml</include></subset>",
            [Code.UNKNOWN_TOP_LEVEL],
        ),
        ("<feature supported='video'>words<view name='grid'/></feature>", [Code.UNKNOWN_TOP_LEVEL]),
        ("<include>./${nope}/a.xml</include>", [Code.UNDEFINED_VARIABLE]),  # W003, then skipped
        ("<include>/etc/elsewhere.xml</include>", [Code.ABSOLUTE_PATH]),
    ],
)
def test_include_and_container_edge_cases(
    write_theme: ThemeWriter, body: str, expected: list[Code]
) -> None:
    assert codes(write_theme, {"theme.xml": wrap(body), "a.xml": "<theme/>"}) == expected


@pytest.mark.parametrize(
    ("element", "value", "expected"),
    [
        ("gridtile", "0.01", ["0.01"]),
        ("gridtile", "0.01 0.02", ["0.01 0.02"]),
        ("gridtile", "14 0.5 14 14", ["14 0.5 14 14"]),  # one sub-pixel value is enough
        ("gridtile", "-0.5", ["-0.5"]),
        ("gridtile", "${p}", ["${p}"]),  # checked after substitution
        ("gridtile", "0", []),
        ("gridtile", "0 0 0 0", []),
        ("gridtile", "14 14 14 14", []),
        ("gridtile", "1 1.5", []),
        ("imagegrid", "0.01 0.02", []),  # the grid's own padding does scale
    ],
)
def test_gridtile_padding_below_a_pixel_is_reported(
    write_theme: ThemeWriter, element: str, value: str, expected: list[str]
) -> None:
    """AC-V23: Vector4f::operator*=(const Vector2f&) is a no-op, so gridtile padding is pixels."""
    name = "default" if element == "gridtile" else "gamegrid"
    xml = wrap(
        "<variables><p>0.25</p></variables>"
        f"<view name='grid'><{element} name='{name}'><padding>{value}</padding></{element}></view>"
    )
    issues = validate_theme(write_theme({"theme.xml": xml})).issues
    assert [(i.code, i.message) for i in issues] == [
        (
            Code.SUBPIXEL_GRIDTILE_PADDING,
            (
                f"padding={v!r}: gridtile padding is always in pixels (the engine's scaling of "
                "values below 1 is a no-op), so this is under a pixel; write pixels, e.g. 14"
            ),
        )
        for v in expected
    ]


def test_invalid_gridtile_padding_gets_only_the_rect_error(write_theme: ThemeWriter) -> None:
    """AC-V23 defers to AC-V11: a malformed rect is E040, not W006 as well."""
    tile = "<gridtile name='default'><padding>0.1 0.2 0.3</padding></gridtile>"
    xml = wrap(V.format(tile))
    assert codes(write_theme, {"theme.xml": xml}) == [Code.BAD_VALUE]


def test_gridtile_padding_attribute_is_checked_too(write_theme: ThemeWriter) -> None:
    """AC-V23, BLIND-31: an attribute names a property just as a child element does."""
    tiles = (
        "\n<gridtile name='default' padding='0.05'/>"
        "\n<gridtile name='selected' padding='14'/>"
        "\n<imagegrid name='gamegrid' padding='0.05'/>"
    )
    result = validate_theme(write_theme({"theme.xml": wrap(V.format(tiles))}))
    assert [(i.code, i.line, i.message) for i in result.issues] == [
        (
            Code.SUBPIXEL_GRIDTILE_PADDING,
            5,  # the <gridtile> line: wrap() adds three lines, then <view>
            (
                "padding='0.05': gridtile padding is always in pixels (the engine's scaling of "
                "values below 1 is a no-op), so this is under a pixel; write pixels, e.g. 14"
            ),
        )
    ]


def test_issues_point_at_the_line_where_the_start_tag_begins(write_theme: ThemeWriter) -> None:
    """BLIND-32: lxml's sourceline is where a start tag *ends*; issues use where it begins."""
    theme = (
        '<?xml version="1.0"?>\n'  # 1
        "<theme><formatVersion>7</formatVersion>\n"  # 2
        "<!-- <gridtile padding='0.5'> in a comment is not a tag -->\n"  # 3
        "<view name='grid'>\n"  # 4
        "<text name='t'><text><![CDATA[a <b> c]]></text></text>\n"  # 5
        "<gridtile\n"  # 6
        "  name='default'\n"  # 7
        "  padding='0.05'\n"  # 8
        "  bogus='a > b'>\n"  # 9
        "</gridtile><image name='i' color='#EE2A30'/><image\n"  # 10
        "  name='j'\n"  # 11
        "  color='#EE2A31'/>\n"  # 12
        "</view></theme>\n"
    )
    issues = validate_theme(write_theme({"theme.xml": theme})).issues
    assert [(i.code, i.line) for i in issues] == [
        (Code.SUBPIXEL_GRIDTILE_PADDING, 6),
        (Code.UNKNOWN_ATTRIBUTE, 6),
        (Code.HASH_COLOR_ALPHA, 10),
        (Code.HASH_COLOR_ALPHA, 10),
    ]
