"""Static validator for Batocera EmulationStation theme XML.

The rules copy what ``ThemeData.cpp`` does at load time. Where the engine
throws, this module reports an ERROR. Where the engine only logs a warning and
silently drops the node (unknown element, unknown property, missing include),
this module *also* reports an ERROR. A dropped node is always a theme bug, even
though the engine keeps running. WARNING is kept for things that load but are
probably wrong (for example ``#RRGGBB`` colours, which lose their alpha
channel in ``HtmlColor::parse``).
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast
from xml.sax.saxutils import escape

from lxml import etree

from n64mini.theme_schema.grammar import Grammar, PropertyType, load_grammar

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping


class Severity(StrEnum):
    """How bad an issue is: errors fail validation, warnings only with ``-W``."""

    ERROR = "error"
    WARNING = "warning"


class Code(StrEnum):
    """Stable rule identifiers (``E***`` errors, ``W***`` warnings)."""

    XML_SYNTAX = "E001"
    MISSING_THEME_ROOT = "E002"
    FORMAT_VERSION = "E003"
    UNKNOWN_TOP_LEVEL = "E004"
    BAD_FEATURE = "E005"
    INCLUDE_NOT_FOUND = "E010"
    INCLUDE_CYCLE = "E011"
    MISSING_NAME = "E020"
    UNKNOWN_VIEW = "E021"
    UNKNOWN_ELEMENT = "E030"
    UNKNOWN_PROPERTY = "E031"
    BAD_STORYBOARD = "E032"
    BAD_VALUE = "E040"
    PATH_NOT_FOUND = "E041"
    BAD_RANDOM = "E042"
    NOT_DOT_RELATIVE = "E043"
    HASH_COLOR_ALPHA = "W001"
    ABSOLUTE_PATH = "W002"
    UNDEFINED_VARIABLE = "W003"
    UNKNOWN_ATTRIBUTE = "W004"
    RUNTIME_UNCHECKED = "W005"
    SUBPIXEL_GRIDTILE_PADDING = "W006"


_SEVERITY: Final[Mapping[Code, Severity]] = {
    code: Severity.ERROR if code.value.startswith("E") else Severity.WARNING for code in Code
}


@dataclass(frozen=True, slots=True)
class Issue:
    """One problem found in a theme file."""

    code: Code
    file: Path
    line: int | None
    message: str

    @property
    def severity(self) -> Severity:
        """Severity implied by the code's prefix."""
        return _SEVERITY[self.code]

    def format(self, root: Path | None = None) -> str:
        """Render as ``file:line: severity CODE: message`` (compiler style)."""
        shown = self.file
        if root is not None and self.file.is_relative_to(root):
            shown = self.file.relative_to(root)
        where = f"{shown}:{self.line}" if self.line is not None else str(shown)
        return f"{where}: {self.severity} {self.code}: {self.message}"


# Attributes ThemeData::parseElement / parseFilterAttributes consume itself.
_FILTER_ATTRIBUTES: Final = frozenset(
    {
        "if",
        "lang",
        "region",
        "ifSubset",
        "verticalScreen",
        "tinyScreen",
        "ifHelpPrompts",
        "ifCheevos",
        "ifArch",
        "ifNotArch",
    }
)
_ELEMENT_META_ATTRIBUTES: Final = _FILTER_ATTRIBUTES | {"name", "extra", "importProperties"}
_SHADER_TYPES: Final = frozenset({"shader", "screenshader", "menuShader", "fadeShader"})
_MENU_SHADERS: Final = frozenset({"menuShader", "fadeShader"})  # special only on menuBackground
_FORMAT_VERSION_RE: Final = re.compile(r"\s*[0-9]+(?:\.[0-9]+)?\s*")  # AC-V03 / BLIND-16
_CDATA_RE: Final = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
# Markup that can contain "<" without starting an element, then an element's start tag. "<" is
# illegal inside attribute values, so outside these constructs every "<name" is a start tag.
_START_TAG_RE: Final = re.compile(
    r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|<!DOCTYPE(?:[^\[>]|\[.*?\])*>"
    r"|<(?P<tag>[^\s/>!?][^\s/>]*)",
    re.DOTALL,
)
# Float-list property types: allowed counts and the name used in messages.
# Counts mirror Vector2f/Vector4f::parseString.
_FLOAT_ARITY: Final[Mapping[PropertyType, tuple[frozenset[int], str]]] = {
    PropertyType.FLOAT: (frozenset({1}), "float"),
    PropertyType.NORMALIZED_PAIR: (frozenset({1, 2}), "pair ('x y')"),
    PropertyType.NORMALIZED_RECT: (frozenset({1, 2, 4}), "rect (1, 2 or 4 floats)"),
}
_NAME_DELIMS_RE: Final = re.compile(r"[\s,]+")
_PLACEHOLDER_RE: Final = re.compile(r"\$\{([^}]+)\}")
# The engine's own "$name" placeholders (resolveSystemVariable), always runtime-only.
_DOLLAR_WORD_RE: Final = re.compile(r"\$(?!\{)\w+")
# One number as StringUtil.cpp toFloat / Vector4f parseNextFloat accept it: optional sign,
# digits with at most one '.', and (our rule) at least one digit. Anything else reads as 0.
_NUMBER: Final = r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)"
_NUMBER_LIST_RE: Final = re.compile(rf"{_NUMBER}(?: +{_NUMBER})*")
_BOOL_VALUES: Final = frozenset({"true", "false", "1", "0", "yes", "no"})
_HEX_RE: Final = re.compile(r"[0-9A-Fa-f]+")


def _is_binding(value: str) -> bool:
    """``{game:image}``-style bindings are resolved per game at runtime."""
    return "{" in value and ":" in value and "}" in value


def _split_names(value: str) -> list[str]:
    return [n for n in _NAME_DELIMS_RE.split(value) if n]


def _engine_numbers(value: str) -> list[str] | None:
    """Split a number list exactly as the engine can parse it, or None if it would misparse.

    No leading/trailing whitespace and only spaces between numbers: the pair parser splits at
    the first space, so a leading space zeroes X and a trailing one zeroes Y (AC-V11).
    """
    if not _NUMBER_LIST_RE.fullmatch(value):
        return None
    return value.split()


def _no_lines() -> dict[etree._Element, int]:
    return {}


@dataclass(slots=True)
class _Context:
    grammar: Grammar
    theme_root: Path
    variables: dict[str, str]
    issues: list[Issue] = field(default_factory=list[Issue])
    stack: list[Path] = field(default_factory=list[Path])
    format_version: float | None = None  # the entry file's, which governs parsing (AC-V04)
    system: str = "n64"
    # What `$system` currently means for each system's load: its own name, until an entry-file
    # include falls back to `default`, after which the engine uses `default` (BLIND-30).
    system_folders: dict[str, str] = field(default_factory=dict[str, str])
    defined_views: set[str] = field(default_factory=set[str])  # for customView inherits (AC-V08)

    # Where each element's start tag begins (BLIND-32), filled per file by _start_lines.
    start_lines: dict[etree._Element, int] = field(default_factory=_no_lines)

    def report(self, code: Code, node: etree._Element | None, message: str) -> None:
        line = self.start_lines[node] if node is not None else None
        self.issues.append(Issue(code, self.stack[-1], line, message))


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """All issues found in a theme and the files that were read."""

    theme_file: Path
    files: tuple[Path, ...]
    issues: tuple[Issue, ...]

    @property
    def errors(self) -> tuple[Issue, ...]:
        """Issues with ERROR severity."""
        return tuple(i for i in self.issues if i.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[Issue, ...]:
        """Issues with WARNING severity."""
        return tuple(i for i in self.issues if i.severity is Severity.WARNING)

    def ok(self, *, warnings_as_errors: bool = False) -> bool:
        """True when there are no errors (or no warnings either, if strict)."""
        return not (self.errors or (warnings_as_errors and self.warnings))


def validate_theme(
    theme_file: Path,
    *,
    system: str = "n64",
    extra_variables: Mapping[str, str] | None = None,
    grammar: Grammar | None = None,
) -> ValidationResult:
    """Validate ``theme_file`` and every file it includes, as seen by ``system``."""
    theme_file = theme_file.resolve()
    variables = {"system.theme": system, "system.name": system}
    # ThemeData::loadFile sets these to the entry theme's folder (AC-V15); appendFile then
    # resets currentPath for each included file (see _validate_file).
    for name in ("currentPath", "themePath", "root"):
        variables[name] = str(theme_file.parent)
    variables.update(extra_variables or {})
    ctx = _Context(
        grammar=grammar or load_grammar(),
        theme_root=theme_file.parent,
        variables=variables,
        system=system,
    )
    visited: list[Path] = []
    _validate_file(ctx, theme_file, None, visited)
    return ValidationResult(theme_file, tuple(visited), tuple(ctx.issues))


def _validate_file(
    ctx: _Context, path: Path, include_node: etree._Element | None, visited: list[Path]
) -> None:
    if path in ctx.stack:
        ctx.report(Code.INCLUDE_CYCLE, include_node, f"include cycle via {path.name}")
        return
    visited.append(path)
    ctx.stack.append(path)
    previous = ctx.variables.get("currentPath")
    ctx.variables["currentPath"] = str(path.parent)  # ThemeData::appendFile (BLIND-13/14)
    try:
        _validate_document(ctx, path, visited)
    finally:
        ctx.stack.pop()
        if previous is not None:
            ctx.variables["currentPath"] = previous


def _validate_document(ctx: _Context, path: Path, visited: list[Path]) -> None:
    try:
        # Comments are kept because they split text the way pugixml does (AC-V11 / BLIND-22),
        # and each CDATA section becomes its own comment-delimited text node, as in pugixml.
        source = _cdata_as_text_nodes(path.read_text(encoding="utf-8"))
        tree = etree.ElementTree(
            etree.fromstring(
                source.encode(), etree.XMLParser(resolve_entities=False), base_url=str(path)
            )
        )
    except etree.XMLSyntaxError as exc:
        ctx.issues.append(Issue(Code.XML_SYNTAX, path, exc.lineno, exc.msg))
        return

    root = tree.getroot()
    ctx.start_lines.update(_start_lines(source, root))
    if root.tag != "theme":
        ctx.report(Code.MISSING_THEME_ROOT, root, f"root element is <{root.tag}>, expected <theme>")
        return

    _check_format_version(ctx, root)
    _report_if_expressions(ctx, root)
    if _stray_text(root):
        ctx.report(Code.UNKNOWN_TOP_LEVEL, root, "text directly under <theme> is ignored")

    for variables in root.iterchildren("variables"):
        for var in _elements(variables):
            name, value = var.get("name"), var.get("value")
            if var.tag == "variable" and name and value is not None:
                # ThemeData::parseVariable's attribute form: <variable name=".." value=".."/>
                ctx.variables[name] = _resolve(ctx, var, value)
            else:
                ctx.variables[var.tag] = _resolve(ctx, var, _engine_text(var))

    if ctx.format_version is not None and ctx.format_version <= 6:
        # parseTheme reads every <view> before any <customView> in format <= 6 (BLIND-27).
        for view in root.iterchildren("view"):
            ctx.defined_views.update(
                n for n in _split_names(view.get("name", "")) if n in ctx.grammar.views
            )
    for child in _elements(root):
        _validate_top_level(ctx, child, visited)


def _validate_top_level(ctx: _Context, child: etree._Element, visited: list[Path]) -> None:
    match child.tag:
        case "formatVersion" | "variables":
            pass  # handled before the in-order walk
        case "include":
            _validate_include(ctx, child, visited)
        case "view":
            _validate_view(ctx, child)
        case "customView":
            _validate_custom_view(ctx, child)
        case "subset":
            if ctx.format_version is not None and ctx.format_version <= 6:
                ctx.report(
                    Code.UNKNOWN_TOP_LEVEL,
                    child,
                    "<subset> is ignored by the engine when formatVersion is 6 or lower",
                )
                return
            if _stray_text(child):
                ctx.report(Code.UNKNOWN_TOP_LEVEL, child, "text inside <subset> is ignored")
            for sub in _elements(child):
                if sub.tag == "include":
                    _validate_include(ctx, sub, visited)
                else:  # parseSubsetElement only walks <include> (BLIND-18)
                    ctx.report(
                        Code.UNKNOWN_TOP_LEVEL, sub, f"<{sub.tag}> inside <subset> is ignored"
                    )
        case "feature":
            _validate_feature(ctx, child)
        case _:
            ctx.report(
                Code.UNKNOWN_TOP_LEVEL, child, f"<{child.tag}> is not a valid child of <theme>"
            )


def _check_format_version(ctx: _Context, root: etree._Element) -> None:
    node = root.find("formatVersion")
    if node is None:
        # Only the entry file must declare it; includes inherit the parent's.
        if len(ctx.stack) == 1:
            ctx.report(Code.FORMAT_VERSION, root, "<formatVersion> is missing")
        return
    text = _engine_text(node)
    if not _FORMAT_VERSION_RE.fullmatch(text):
        ctx.report(
            Code.FORMAT_VERSION, node, f"<formatVersion> {text!r} is not a plain decimal number"
        )
        return
    version = float(text)
    if len(ctx.stack) == 1:
        ctx.format_version = version
    if version < ctx.grammar.minimum_format_version:
        ctx.report(
            Code.FORMAT_VERSION,
            node,
            f"formatVersion {version:g} is below the engine minimum "
            f"{ctx.grammar.minimum_format_version}",
        )


def _validate_include(ctx: _Context, node: etree._Element, visited: list[Path]) -> None:
    raw = _engine_text(node)  # untrimmed, as the engine reads it; whitespace-only is "" (AC-V05)
    if not raw:
        return
    if raw != raw.strip():
        ctx.report(
            Code.INCLUDE_NOT_FOUND,
            node,
            f"included file {raw!r} has surrounding whitespace; the engine doesn't trim it, "
            "so the file won't be found",
        )
        return
    resolved = _resolve(ctx, node, raw)
    if _report_runtime(ctx, node, f"include {raw!r}", resolved):
        return  # runtime-only (W005 reported)
    if "$" in resolved.replace("$system", ""):
        return  # undefined variable (W003 reported)
    _check_include_for_collections(ctx, node, raw)
    folder = ctx.system_folders.get(ctx.system, ctx.system)
    found = _include_candidates(ctx, resolved, folder, allow_default="${" not in raw)
    candidates = [text for _, text in found]
    first = candidates[0]
    if Path(first).is_absolute():
        if not Path(first).resolve().is_relative_to(ctx.theme_root):
            ctx.report(
                Code.ABSOLUTE_PATH, node, f"include {raw!r} is outside the theme; not checked"
            )
            return
    elif not _dot_relative(ctx, node, f"include {raw!r}", first):
        return
    hit = _first_existing(ctx, found)
    if hit is None:
        where = " or ".join(str((ctx.stack[-1].parent / c).resolve()) for c in candidates)
        ctx.report(Code.INCLUDE_NOT_FOUND, node, f"included file {raw!r} not found ({where})")
        return
    used_folder, target = hit
    _note_default_fallback(ctx, ctx.system, used_folder)
    _validate_file(ctx, target, node, visited)


def _first_existing(ctx: _Context, found: list[tuple[str, str]]) -> tuple[str, Path] | None:
    """The first candidate that is a file, with the folder `$system` became for it."""
    for folder, text in found:
        target = (ctx.stack[-1].parent / text).resolve()
        if target.is_file():
            return folder, target
    return None


def _note_default_fallback(ctx: _Context, system: str, used_folder: str) -> None:
    """ThemeData::parseInclude: a default fallback in the entry file sticks (BLIND-30)."""
    if used_folder == "default" and len(ctx.stack) == 1:
        ctx.system_folders[system] = "default"


def _include_candidates(
    ctx: _Context, text: str, folder: str, *, allow_default: bool
) -> list[tuple[str, str]]:
    r"""Where the engine looks for an include, in order, as (folder used, path) (AC-V22).

    ``$system`` becomes ``folder``. resolveSystemVariable then tests whether that result
    exists *before* it's made relative to the theme, which a relative path never does, so a
    system with a compatibility alias always gets the alias instead (BLIND-29). Then, only when
    the path has no ``${``, parseInclude falls back to ``default``. ``.\`` becomes ``./``.
    """
    text = _normalise_dot_backslash(text)
    if "$system" not in text:
        return [(folder, text)]
    first = folder
    if alias := ctx.grammar.system_folder_aliases.get(folder):
        own = text.replace("$system", folder, 1)
        absolute_own_exists = Path(own).is_absolute() and Path(own).exists()
        first = folder if absolute_own_exists else alias
    folders = [first, *(["default"] if allow_default else [])]
    return [(f, text.replace("$system", f, 1)) for f in dict.fromkeys(folders)]


def _check_include_for_collections(ctx: _Context, node: etree._Element, raw: str) -> None:
    """AC-V22: a system-dependent include must also exist for every built-in collection."""
    if not any(t in raw for t in ("${system.theme}", "${system.name}", "$system")):
        return
    failing: list[str] = []
    for system in ctx.grammar.collection_systems:
        name = ctx.grammar.collection_names.get(system, system)
        text = raw.replace("${system.theme}", system).replace("${system.name}", name)
        text = _resolve(ctx, node, text, report=False)
        folder = ctx.system_folders.get(system, system)
        hit = _first_existing(
            ctx, _include_candidates(ctx, text, folder, allow_default="${" not in raw)
        )
        if hit is None:
            failing.append(system)
        else:
            _note_default_fallback(ctx, system, hit[0])
    if failing:
        ctx.report(
            Code.INCLUDE_NOT_FOUND,
            node,
            f"included file {raw!r} doesn't exist for system(s) {', '.join(failing)}; the engine "
            "loads the theme for each of them. "
            + (
                "Add a 'default' folder for the fallback"
                if "$system" in raw and "${" not in raw
                else "Use './$system/…' with a 'default' folder"
            ),
        )


def _validate_feature(ctx: _Context, feature: etree._Element) -> None:
    """AC-V19: one supported value; only <view> children are read (AC-V04)."""
    supported = feature.get("supported")
    if supported is None:
        ctx.report(
            Code.BAD_FEATURE, feature, '<feature> is missing "supported"; the engine skips it'
        )
        return
    if supported not in ctx.grammar.features:
        ctx.report(
            Code.BAD_FEATURE,
            feature,
            f"<feature supported={supported!r}> isn't one supported feature "
            f"({', '.join(sorted(ctx.grammar.features))}); the engine skips its views",
        )
        return
    if supported == "manufacturer":
        ctx.report(
            Code.RUNTIME_UNCHECKED,
            feature,
            '<feature supported="manufacturer"> applies only if the console sets '
            "system.manufacturer; not checked",
        )
    if _stray_text(feature):
        ctx.report(Code.UNKNOWN_TOP_LEVEL, feature, "text inside <feature> is ignored")
    for child in _elements(feature):
        if child.tag == "view":
            _validate_view(ctx, child)
        else:
            ctx.report(
                Code.UNKNOWN_TOP_LEVEL,
                child,
                f"<{child.tag}> inside <feature> is ignored; only <view> is read",
            )


def _report_if_expressions(ctx: _Context, root: etree._Element) -> None:
    """AC-V17 (interim BLIND-10): if= expressions aren't evaluated yet, so say so."""
    for node in root.iter("*"):
        expr = node.get("if")
        if expr and expr.strip():
            ctx.report(
                Code.RUNTIME_UNCHECKED,
                node,
                f"if={expr!r} is evaluated by the engine at load time and is not checked here",
            )


def _report_missing_name(ctx: _Context, node: etree._Element) -> None:
    if node.get("name") is None:
        ctx.report(Code.MISSING_NAME, node, f'<{node.tag}> is missing its "name" attribute')
    else:
        ctx.report(
            Code.MISSING_NAME, node, f'<{node.tag}> has an empty "name"; the engine ignores it'
        )


def _validate_view(ctx: _Context, view: etree._Element) -> None:
    names = _split_names(view.get("name", ""))
    if not names:
        _report_missing_name(ctx, view)
        return
    for name in names:
        if name not in ctx.grammar.views:
            ctx.report(
                Code.UNKNOWN_VIEW,
                view,
                f"view {name!r} is not supported; expected one of "
                f"{', '.join(sorted(ctx.grammar.views))} (style custom views via <customView>)",
            )
        else:
            ctx.defined_views.add(name)
    _validate_view_children(ctx, view)


def _validate_custom_view(ctx: _Context, view: etree._Element) -> None:
    names = _split_names(view.get("name", ""))
    if not names:
        _report_missing_name(ctx, view)
        return
    inherits = view.get("inherits")
    if inherits and inherits not in ctx.defined_views:
        ctx.report(
            Code.UNKNOWN_VIEW,
            view,
            f"<customView inherits={inherits!r}> names no view defined before it; "
            "the engine ignores the inheritance",
        )
    ctx.defined_views.update(names)
    _validate_view_children(ctx, view)


def _validate_view_children(ctx: _Context, view: etree._Element) -> None:
    if _stray_text(view):  # a nameless node: 'Element of type "" missing "name"' (BLIND-19)
        ctx.report(Code.MISSING_NAME, view, f"text directly inside <{view.tag}> is a nameless node")
    for element in _elements(view):
        if not _split_names(element.get("name", "")):
            _report_missing_name(ctx, element)
            continue
        if element.tag not in ctx.grammar.elements:
            ctx.report(Code.UNKNOWN_ELEMENT, element, f"unknown element type <{element.tag}>")
            continue
        _validate_element(ctx, element, element.tag)


def _validate_element(ctx: _Context, node: etree._Element, element_type: str) -> None:
    if _stray_text(node):  # the engine logs 'Unknown property : <type>.' (BLIND-21)
        ctx.report(
            Code.UNKNOWN_PROPERTY,
            node,
            f"text directly inside <{element_type}> is a nameless child the engine ignores",
        )
    _validate_attributes(ctx, node, element_type)
    for child in _elements(node):
        _validate_child(ctx, child, element_type)


def _validate_attributes(ctx: _Context, node: etree._Element, element_type: str) -> None:
    """Attributes may carry properties too (``<text pos="0 0">``)."""
    for raw_attr, raw_value in node.attrib.items():
        attr, value = _decode(raw_attr), _decode(raw_value)
        if attr in _ELEMENT_META_ATTRIBUTES:
            continue
        attr_type = ctx.grammar.property_type(element_type, attr)
        if attr_type is None:
            ctx.report(
                Code.UNKNOWN_ATTRIBUTE, node, f"attribute {attr!r} is ignored on <{element_type}>"
            )
            continue
        _check_value(ctx, node, attr, value, attr_type)


def _special_child_type(child: etree._Element, element_type: str) -> PropertyType | None:
    """Children the engine accepts outside ``sElementMap`` (see ThemeData::parseElement)."""
    tag = child.tag
    if element_type == "menuIcons":
        return PropertyType.PATH
    if element_type in _SHADER_TYPES:
        return PropertyType.PATH if tag == "path" else PropertyType.STRING
    if tag == "animate" and element_type == "imagegrid":
        return PropertyType.BOOLEAN
    if _is_shader_child(tag, element_type) and not _has_elements(child):
        return PropertyType.PATH
    return None


def _is_shader_child(tag: str, element_type: str) -> bool:
    """``shader`` anywhere; menu/fade shaders only on menuBackground (BLIND-20)."""
    return tag == "shader" or (tag in _MENU_SHADERS and element_type == "menuBackground")


def _validate_child(ctx: _Context, child: etree._Element, element_type: str) -> None:
    tag = child.tag
    text = _engine_text(child)  # the first text node, untrimmed, as pugixml returns it (AC-V11)
    prop_type = ctx.grammar.property_type(element_type, tag)
    if prop_type is None and tag == "storyboard":
        _check_storyboard(ctx, child, element_type)
        return

    # <text><text>..</text></text>: the engine looks only at the first child (BLIND-11).
    if element_type == "text" and tag == "text" and _has_elements(child):
        if (child.text or "").strip():
            ctx.report(
                Code.UNKNOWN_PROPERTY,
                child,
                "<text> starts with text, so it's a property and its child elements are dropped",
            )
        else:
            prop_type = None

    if prop_type is None:
        prop_type = _special_child_type(child, element_type)
    if prop_type is not None:
        _check_value(ctx, child, tag, text, prop_type)
    elif tag == "itemTemplate" and element_type in ctx.grammar.item_template_parents:
        _validate_element(ctx, child, "control")
    elif _is_shader_child(tag, element_type):
        _validate_element(ctx, child, "shader")
    elif tag in ctx.grammar.elements:
        _validate_element(ctx, child, tag)
    else:
        ctx.report(
            Code.UNKNOWN_PROPERTY,
            child,
            f"<{tag}> is not a property of <{element_type}> (the engine ignores it)",
        )


def _check_storyboard(ctx: _Context, storyboard: etree._Element, element_type: str) -> None:
    """AC-V20, mirroring ThemeStoryboard::fromXmlNode."""
    children = list(_elements(storyboard))
    stray = _stray_text(storyboard)
    if not children and not stray:
        return  # <storyboard event="x"/> removes the event's storyboard: valid
    if stray:
        ctx.report(Code.BAD_STORYBOARD, storyboard, "text in a storyboard is ignored")
    own = ctx.grammar.elements.get(element_type, {})  # the engine passes the element's own map
    usable = 0
    for child in children:
        if child.tag == "sound":
            usable += _usable_sound(ctx, child)
        elif child.tag != "animation":
            ctx.report(Code.BAD_STORYBOARD, child, f"<{child.tag}> in a storyboard is ignored")
        elif not (prop := child.get("property") or ""):
            ctx.report(Code.BAD_STORYBOARD, child, "<animation> has no property; it's skipped")
        elif prop in own or prop.startswith("shader."):
            usable += 1
            prop_type = own.get(prop, PropertyType.FLOAT)  # shader.* is a FLOAT
            for end in ("from", "to"):  # parsed with the property's type (BLIND-12)
                if (value := child.get(end)) is not None:
                    _check_value(ctx, child, f"{prop}.{end}", value, prop_type)
        else:
            ctx.report(
                Code.BAD_STORYBOARD,
                child,
                f"<animation property={prop!r}> isn't a property of <{element_type}>; it's skipped",
            )
    if not usable:
        ctx.report(
            Code.BAD_STORYBOARD,
            storyboard,
            "storyboard has no usable <animation> or <sound>; the engine discards it",
        )


def _usable_sound(ctx: _Context, sound: etree._Element) -> int:
    """1 if the engine keeps this <sound>: path set, './'-relative and existing (BLIND-24)."""
    path = _normalise_dot_backslash(_resolve(ctx, sound, sound.get("path") or ""))
    if path.startswith("./") and (ctx.stack[-1].parent / path).is_file():
        return 1
    ctx.report(
        Code.BAD_STORYBOARD,
        sound,
        f"<sound path={sound.get('path')!r}> is skipped: "
        "it must be a './' path to an existing file",
    )
    return 0


_RANDOM_IMAGE_VALUES: Final = frozenset(
    {
        "{random}",
        "{random:thumbnail}",
        "{random:marquee}",
        "{random:image}",
        "{random:fanart}",
        "{random:titleshot}",
    }
)


def _check_random(ctx: _Context, node: etree._Element, prop: str, value: str) -> None:
    """AC-V18, mirroring the {random} branch of ThemeData::processElement."""
    element = node.getparent() if node.tag == prop else node
    view = element.getparent() if element is not None else None
    reason = None
    if element is None or element.get("extra", "").lower() not in {"true", "static"}:
        reason = 'only extras (extra="true" or "static") support it'
    elif element.tag not in {"image", "video"}:
        reason = "only <image> and <video> support it"
    elif view is None or view.tag != "view" or view.get("name") != "system":
        reason = 'only an element directly in <view name="system"> supports it'
    elif element.tag == "video" and value != "{random}":
        reason = "a <video> supports only {random}"
    elif element.tag == "image" and value not in _RANDOM_IMAGE_VALUES:
        reason = (
            f"unknown random value; an <image> supports {', '.join(sorted(_RANDOM_IMAGE_VALUES))}"
        )
    if reason:
        ctx.report(Code.BAD_RANDOM, node, f"{prop}={value!r}: {reason}; the engine drops it")


def _dot_relative(ctx: _Context, node: etree._Element, what: str, value: str) -> bool:
    """AC-V21: only ./ paths resolve against the theme. Reports and returns False otherwise."""
    if value.startswith(("./", ".\\")) or value == ".":
        return True
    ctx.report(
        Code.NOT_DOT_RELATIVE,
        node,
        f"{what} must start with './': the engine resolves other relative paths against the "
        "program's working directory, not the theme",
    )
    return False


def _handled_specially(
    ctx: _Context, node: etree._Element, prop: str, raw: str, prop_type: PropertyType
) -> bool:
    """STRING, {random…} and {x:y} bindings, which skip the type grammar. True if handled."""
    if prop_type is PropertyType.STRING:
        _resolve(ctx, node, raw)  # only to report undefined variables
        return True
    if prop_type is PropertyType.PATH and raw.startswith("{random"):
        _check_random(ctx, node, prop, raw)
        return True
    if not _is_binding(raw):
        return False
    if prop_type in {PropertyType.NORMALIZED_PAIR, PropertyType.NORMALIZED_RECT}:
        ctx.report(
            Code.BAD_VALUE,
            node,
            f"{prop}={raw!r}: pairs and rects don't support bindings; the engine reads 0",
        )
    return True  # per-game runtime data, e.g. {game:image}


def _check_value(
    ctx: _Context, node: etree._Element, prop: str, raw: str, prop_type: PropertyType
) -> None:
    if _handled_specially(ctx, node, prop, raw, prop_type):
        return
    value = _resolve(ctx, node, raw).replace(
        "$system", ctx.system_folders.get(ctx.system, ctx.system)
    )
    if _report_runtime(ctx, node, f"{prop}={raw!r}", value) or "$" in value:
        return  # runtime-only (W005 reported) or undefined (W003 reported)

    def bad(expected: str) -> None:
        ctx.report(Code.BAD_VALUE, node, f"{prop}={raw!r} is not a valid {expected}")

    if prop_type in _FLOAT_ARITY:
        arities, expected = _FLOAT_ARITY[prop_type]
        numbers = _engine_numbers(value)
        if numbers is None or len(numbers) not in arities:
            bad(expected + "; the engine reads anything else as 0")
        elif prop == "padding" and _is_gridtile(node):
            _check_gridtile_padding(ctx, node, raw, numbers)
        return

    match prop_type:
        case PropertyType.BOOLEAN:
            if value.lower() not in _BOOL_VALUES:
                bad("boolean (true/false/1/0)")
        case PropertyType.COLOR:
            _check_color(ctx, node, prop, value, bad)
        case PropertyType.PATH:
            _check_path(ctx, node, prop, value)
        case _:  # FLOAT/PAIR/RECT returned above; STRING returned at the top
            raise AssertionError(f"unhandled property type {prop_type}")


def _is_gridtile(node: etree._Element) -> bool:
    """A gridtile property: a child of <gridtile>, or <gridtile> itself (attribute form)."""
    if node.tag == "gridtile":
        return True  # BLIND-31: an attribute names a property just as a child element does
    parent = node.getparent()
    return parent is not None and parent.tag == "gridtile"


def _check_gridtile_padding(
    ctx: _Context, node: etree._Element, raw: str, numbers: list[str]
) -> None:
    """AC-V23: GridTileComponent scales a padding below 1 with a no-op, so it's always pixels."""
    if any(0 < abs(float(n)) < 1 for n in numbers):
        ctx.report(
            Code.SUBPIXEL_GRIDTILE_PADDING,
            node,
            f"padding={raw!r}: gridtile padding is always in pixels (the engine's scaling of "
            "values below 1 is a no-op), so this is under a pixel; write pixels, e.g. 14",
        )


def _check_color(
    ctx: _Context,
    node: etree._Element,
    prop: str,
    value: str,
    bad: Callable[[str], None],
) -> None:
    if value in {"", "transparent"}:
        return
    if value[0].islower() and not _HEX_RE.fullmatch(value):
        if value not in ctx.grammar.color_names:
            bad("colour name")
        return
    if value.startswith("#"):
        digits = value[1:]
        if len(digits) not in {6, 8} or not _HEX_RE.fullmatch(digits):
            bad("colour (#RRGGBB[AA])")
        elif len(digits) == 6:
            ctx.report(
                Code.HASH_COLOR_ALPHA,
                node,
                f"{prop}={value!r}: HtmlColor::parse leaves alpha at 0 for '#RRGGBB'; "
                f"write {digits.upper()} or {digits.upper()}FF instead",
            )
        return
    if len(value) not in {6, 8} or not _HEX_RE.fullmatch(value):
        bad("colour (RRGGBB or RRGGBBAA)")


def _check_path(ctx: _Context, node: etree._Element, prop: str, value: str) -> None:
    value = _normalise_dot_backslash(value)
    if not value or value == "none":
        return
    if Path(value).is_absolute() and Path(value).resolve().is_relative_to(ctx.theme_root):
        # Built from ${themePath}/${currentPath}/${root}: inside the theme, so check it.
        if not Path(value).is_file():
            ctx.report(Code.PATH_NOT_FOUND, node, f"{prop}={value!r} not found")
        return
    if value.startswith(("/", "~")):
        ctx.report(
            Code.ABSOLUTE_PATH,
            node,
            f"{prop}={value!r} is an absolute device path and was not checked; "
            "bundle the asset inside the theme instead",
        )
        return
    if value != value.strip():
        tried = [(ctx.stack[-1].parent / value).resolve()]
        ctx.report(Code.PATH_NOT_FOUND, node, f"{prop}={value!r} not found ({tried[0]})")
        return
    if not _dot_relative(ctx, node, f"{prop}={value!r}", value):
        return
    tried = [(ctx.stack[-1].parent / value).resolve()]
    # ThemeData::processElement: inside an include, a "."-relative path that isn't found
    # next to the include is retried against the entry theme file's directory.
    if value.startswith(".") and len(ctx.stack) > 1:
        tried.append((ctx.stack[0].parent / value).resolve())
    if not any(t.is_file() for t in tried):
        where = " or ".join(str(t) for t in dict.fromkeys(tried))
        ctx.report(Code.PATH_NOT_FOUND, node, f"{prop}={value!r} not found ({where})")


def _resolve(ctx: _Context, node: etree._Element, value: str, *, report: bool = True) -> str:
    """Substitute known variables; leave engine (runtime) ones; W003 for undefined ones."""

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in ctx.variables:
            return ctx.variables[name]
        if report and not ctx.grammar.is_engine_variable(name):
            ctx.report(Code.UNDEFINED_VARIABLE, node, f"variable ${{{name}}} is not defined")
        return match.group(0)

    return _PLACEHOLDER_RE.sub(replace, value)


def _report_runtime(ctx: _Context, node: etree._Element, what: str, resolved: str) -> bool:
    """W005 if ``resolved`` still depends on console-only variables (AC-V17). True if so."""
    runtime = [
        m.group(0)
        for m in _PLACEHOLDER_RE.finditer(resolved)
        if ctx.grammar.is_engine_variable(m.group(1))
    ] + [w for w in _DOLLAR_WORD_RE.findall(resolved) if w != "$system"]  # $system is known
    if runtime:
        ctx.report(
            Code.RUNTIME_UNCHECKED,
            node,
            f"{what} depends on runtime variable(s) {', '.join(dict.fromkeys(runtime))}; "
            "not checked",
        )
    return bool(runtime)


def _engine_text(node: etree._Element) -> str:
    """What pugixml's ``node.text()`` returns (AC-V11).

    Its first non-whitespace text node, untrimmed: whitespace-only text is dropped, and a
    comment splits the text, so only the part before it counts.
    """
    # lxml-stubs types every tag as str, but a comment's tag is the etree.Comment factory.
    comments = (c for c in node if cast("object", c.tag) is etree.Comment)
    for segment in (node.text, *(c.tail for c in comments)):
        if segment and segment.strip():
            return segment
    return ""


def _has_elements(node: etree._Element) -> bool:
    return next(_elements(node), None) is not None


def _stray_text(node: etree._Element) -> str:
    """Non-whitespace text directly inside a node that should hold only elements."""
    for segment in (node.text, *(child.tail for child in node)):
        if segment and segment.strip():
            return segment.strip()
    return ""


def _normalise_dot_backslash(path: str) -> str:
    r"""The engine's getGenericPath turns '\' into '/' after a '.\' prefix (BLIND-26)."""
    return path.replace("\\", "/") if path.startswith(".\\") else path


def _start_lines(source: str, root: etree._Element) -> dict[etree._Element, int]:
    """The line each element's start tag begins on (BLIND-32).

    lxml's ``sourceline`` is the line where a start tag *ends*, so a tag spread over several
    lines would be reported at its closing ``>``. The start tags in ``source`` and the parsed
    elements are both in document order, so they pair up one to one.
    """
    starts = [(m["tag"], m.start()) for m in _START_TAG_RE.finditer(source) if m["tag"]]
    elements = list(root.iter("*"))
    if [t.rpartition(":")[2] for t, _ in starts] != [etree.QName(e).localname for e in elements]:
        raise AssertionError("start tags in the source don't match the parsed elements")
    newlines = [i for i, c in enumerate(source) if c == "\n"]
    return {
        e: bisect_right(newlines, pos) + 1 for e, (_, pos) in zip(elements, starts, strict=True)
    }


def _cdata_as_text_nodes(source: str) -> str:
    """Each CDATA section becomes escaped text between empty comments (pugixml's model).

    pugixml makes a CDATA section its own node, and ``text()`` returns the first one; lxml
    merges CDATA into the surrounding text. Wrapping it in comments (which ``_engine_text``
    treats as node boundaries) gives the pugixml reading. Line numbers are unchanged.
    """
    return _CDATA_RE.sub(lambda m: f"<!---->{escape(m.group(1))}<!---->", source)


def _elements(node: etree._Element) -> Iterator[etree._Element]:
    """Child elements only; ``"*"`` makes lxml skip comments, PIs and entities."""
    return node.iterchildren("*")


def _decode(value: str | bytes) -> str:
    return value.decode() if isinstance(value, bytes) else value
