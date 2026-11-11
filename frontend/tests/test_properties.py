"""Property-based tests (Hypothesis): invariants over generated inputs."""

from __future__ import annotations

import tempfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from hypothesis import given, settings
from hypothesis import strategies as st

from n64mini.harness.render import expand_vars
from n64mini.harness.values import parse_hex_color, parse_pair
from n64mini.theme_schema import Code, PropertyType, load_grammar, validate_theme
from tests.conftest import wrap

GRAMMAR = load_grammar()
finite = st.floats(allow_nan=False, allow_infinity=False, width=32)
byte = st.integers(0, 255)
xml_text = st.text(st.characters(codec="utf-8", exclude_categories=("Cs", "Cc")), max_size=20)


@given(finite, finite)
def test_parse_pair_round_trips(x: float, y: float) -> None:
    assert parse_pair(f"{x!r} {y!r}", (0.0, 0.0)) == (x, y)


@given(finite)
def test_parse_pair_single_value_fills_both(x: float) -> None:
    assert parse_pair(repr(x), (9.0, 9.0)) == (x, x)


@given(byte, byte, byte, byte, st.sampled_from(["", "#"]))
def test_parse_hex_color_round_trips(r: int, g: int, b: int, a: int, prefix: str) -> None:
    text = prefix + f"{r:02x}{g:02X}{b:02x}{a:02X}"
    assert parse_hex_color(text) == (r, g, b, a)
    assert parse_hex_color(text[:-2]) == (r, g, b, 255)


@given(st.text(alphabet=st.characters(exclude_characters="$"), max_size=40))
def test_expand_vars_is_identity_without_placeholders(text: str) -> None:
    assert expand_vars(text, {"a": "b"}) == text


def _validate(body: str) -> list[Code]:
    with tempfile.TemporaryDirectory() as tmp:
        theme = Path(tmp) / "theme.xml"
        theme.write_text(wrap(body), encoding="utf-8")
        return [i.code for i in validate_theme(theme).issues]


# The engine's number grammar (StringUtil.cpp toFloat): sign, digits, at most one '.'.
engine_number = st.from_regex(r"[+-]?(?:[0-9]{1,6}(?:\.[0-9]{0,6})?|\.[0-9]{1,6})", fullmatch=True)


@settings(max_examples=60, deadline=None)
@given(engine_number, engine_number)
def test_any_engine_number_pair_is_a_valid_pos(x: str, y: str) -> None:
    assert _validate(f"<view name='grid'><image name='i'><pos>{x} {y}</pos></image></view>") == []


@settings(max_examples=60, deadline=None)
@given(byte, byte, byte, st.one_of(st.none(), byte))
def test_any_bare_hex_colour_is_valid(r: int, g: int, b: int, a: int | None) -> None:
    hex_ = f"{r:02X}{g:02x}{b:02X}" + ("" if a is None else f"{a:02x}")
    assert _validate(f"<view name='grid'><text name='t'><color>{hex_}</color></text></view>") == []


# Python float syntax the engine reads as 0: exponents, inf, nan.
python_only_number = st.one_of(
    st.sampled_from(["inf", "-inf", "nan", "Infinity"]),
    st.floats(allow_nan=False, allow_infinity=False).map(repr).filter(lambda r: "e" in r),
)


@settings(max_examples=60, deadline=None)
@given(python_only_number)
def test_non_engine_number_syntax_is_rejected(z: str) -> None:
    found = _validate(f"<view name='grid'><image name='i'><zIndex>{z}</zIndex></image></view>")
    assert found == [Code.BAD_VALUE]


element_types = st.sampled_from(sorted(GRAMMAR.elements))


@st.composite
def random_element(draw: st.DrawFn) -> str:
    """A grammar element with a mix of real and junk properties holding junk text."""
    etype = draw(element_types)
    known = sorted(GRAMMAR.elements[etype]) or ["pos"]
    junk = st.from_regex(r"[a-z][a-zA-Z]{0,8}", fullmatch=True)
    props = draw(st.lists(st.one_of(st.sampled_from(known), junk), max_size=5))
    children = "".join(f"<{p}>{escape(draw(xml_text))}</{p}>" for p in props)
    name = draw(st.one_of(st.just(""), st.from_regex(r"[a-z_]{1,8}", fullmatch=True)))
    attr = f" name={quoteattr(name)}" if name else ""
    return f"<{etype}{attr}>{children}</{etype}>"


@settings(max_examples=150, deadline=None)
@given(st.sampled_from(["grid", "system", "nope"]), st.lists(random_element(), max_size=4))
def test_validator_never_crashes_and_only_reports_known_codes(
    view: str, elements: list[str]
) -> None:
    found = _validate(f"<view name='{view}'>{''.join(elements)}</view>")
    assert set(found) <= set(Code)
    if view == "nope":
        assert Code.UNKNOWN_VIEW in found


def test_every_property_type_is_exercised() -> None:
    """Guards the strategies above against grammar drift."""
    used = {t for props in GRAMMAR.elements.values() for t in props.values()}
    assert used == set(PropertyType)


# ---------------------------------------------------------------- metamorphic invariants
# Contract-grounded rather than example-grounded: each relates two validator runs, so
# no hand-picked expected issue list is involved.


def _codes_for(files: dict[str, str]) -> list[Code]:
    with tempfile.TemporaryDirectory() as tmp:
        for name, text in files.items():
            (Path(tmp) / name).write_text(text, encoding="utf-8")
        return sorted(i.code for i in validate_theme(Path(tmp) / "theme.xml").issues)


@settings(max_examples=60, deadline=None)
@given(st.lists(random_element(), max_size=4), st.from_regex(r"zz[a-z]{1,6}", fullmatch=True))
def test_adding_one_unknown_property_adds_exactly_one_e031(elements: list[str], junk: str) -> None:
    probe = f"<image name='probe'><{junk}>1</{junk}></image>"
    clean = "<image name='probe'/>"
    before = _codes_for({"theme.xml": wrap(f"<view name='grid'>{''.join(elements)}{clean}</view>")})
    after = _codes_for({"theme.xml": wrap(f"<view name='grid'>{''.join(elements)}{probe}</view>")})
    assert after == sorted([*before, Code.UNKNOWN_PROPERTY])


@settings(max_examples=60, deadline=None)
@given(st.lists(random_element(), min_size=1, max_size=4))
def test_moving_views_into_an_include_changes_no_issue(elements: list[str]) -> None:
    view = f"<view name='grid'>{''.join(elements)}</view>"
    inline = _codes_for({"theme.xml": wrap(view)})
    split = _codes_for(
        {"theme.xml": wrap("<include>./inc.xml</include>"), "inc.xml": f"<theme>{view}</theme>"}
    )
    assert split == inline
