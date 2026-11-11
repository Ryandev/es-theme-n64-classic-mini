"""Gate: tests can't be weakened silently (workspace3 testing.md, "Never Weaken Tests").

Every skip or expected failure must state why, and an expected failure must be
strict: once the bug is fixed, the suite fails until someone removes the marker.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Final

import pytest

from n64mini.paths import FRONTEND_ROOT

if TYPE_CHECKING:
    from pathlib import Path

TESTS: Final = sorted((FRONTEND_ROOT / "tests").rglob("*.py"))
_MARKERS: Final = {"skip", "skipif", "xfail"}


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return node.id if isinstance(node, ast.Name) else ""


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _has_text(node: ast.expr | None) -> bool:
    """A non-empty string literal, or an f-string with non-empty literal text (BLIND-{n}: …)."""
    if isinstance(node, ast.Constant):
        return bool(str(node.value).strip())
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.Constant) and str(v.value).strip() for v in node.values)
    return False


def weakenings(source: str) -> list[str]:
    """Each skip/xfail in ``source`` that lacks a reason, or an xfail that isn't strict."""
    problems: list[str] = []
    tree = ast.parse(source)
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for node in ast.walk(tree):
        # A bare marker (`@pytest.mark.skip`, no call) can't carry a reason at all.
        if isinstance(node, ast.Attribute) and id(node) not in called:
            name = _dotted(node)
            if name.startswith("pytest.mark.") and name.rsplit(".", 1)[-1] in _MARKERS:
                problems.append(f"line {node.lineno}: bare {name} without a reason")
            continue
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        kind = name.rsplit(".", 1)[-1]
        if not (name.startswith("pytest.") and kind in _MARKERS):
            continue
        reason = _kw(node, "reason")
        if reason is None and name in {"pytest.skip", "pytest.xfail"} and node.args:
            reason = node.args[0]
        if not _has_text(reason):
            problems.append(f"line {node.lineno}: {name} without a reason")
        if kind == "xfail" and name.startswith("pytest.mark."):
            strict = _kw(node, "strict")
            if not (isinstance(strict, ast.Constant) and strict.value is True):
                problems.append(f"line {node.lineno}: {name} must be strict=True")
    return problems


@pytest.mark.parametrize("path", TESTS, ids=lambda p: str(p.relative_to(FRONTEND_ROOT)))
def test_no_silent_skips_or_lenient_xfails(path: Path) -> None:
    assert weakenings(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import pytest\n@pytest.mark.skip\ndef test_a(): ...", 1),
        ("import pytest\n@pytest.mark.skip(reason='')\ndef test_a(): ...", 1),
        ("import pytest\n@pytest.mark.xfail(reason='BLIND-9: x')\ndef test_a(): ...", 1),
        ("import pytest\n@pytest.mark.xfail(strict=True)\ndef test_a(): ...", 1),
        ("import pytest\ndef test_a():\n    pytest.skip()", 1),
        (
            "import pytest\n@pytest.mark.xfail(strict=True, reason='BLIND-9')\ndef test_a(): ...",
            0,
        ),
        ("import pytest\n@pytest.mark.skipif(True, reason='needs X')\ndef test_a(): ...", 0),
        ("import pytest\ndef test_a():\n    pytest.skip('needs X')", 0),
        ("import pytest\nn = 3\nm = pytest.mark.xfail(strict=True, reason=f'BLIND-{n}: x')", 0),
        ("import pytest\nn = 3\nm = pytest.mark.xfail(strict=True, reason=f'{n}')", 1),
    ],
)
def test_the_gate_can_fail(source: str, expected: int) -> None:
    """Red-confirmation: each weakening form is caught, and the legitimate forms pass."""
    assert len(weakenings(source)) == expected
