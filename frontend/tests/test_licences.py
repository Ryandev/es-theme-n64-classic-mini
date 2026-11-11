"""Gate: every direct dependency has an approved licence (workspace3 dependencies.md).

Runtime dependencies ship, so they must be on the allowlist. A dev-only tool with
any other licence needs a written exception in pyproject.toml. A package that
bundles a native program must declare that program's licence too, because the
wrapper's metadata can hide it (shellcheck-py is MIT; the shellcheck it ships is
GPL-3.0).
"""

from __future__ import annotations

import re
import tomllib
from importlib.metadata import PackageNotFoundError, distribution, metadata
from pathlib import Path
from typing import Final, TypedDict, cast

import pytest

from n64mini.paths import FRONTEND_ROOT

_NAME_RE: Final = re.compile(r"^[A-Za-z0-9_.-]+")
_COPYLEFT_RE: Final = re.compile(r"\b(A?GPL|LGPL)", re.IGNORECASE)
# ELF, PE (Windows), Mach-O 64-bit, Mach-O universal.
_EXECUTABLE_MAGIC: Final = (b"\x7fELF", b"MZ", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")


class _Licences(TypedDict):
    allowed: list[str]
    bundled: dict[str, str]
    exceptions: dict[str, str]


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _names(specs: list[str]) -> list[str]:
    return [_norm(m.group(0)) for s in specs if (m := _NAME_RE.match(s))]


_PYPROJECT = tomllib.loads((FRONTEND_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
RUNTIME: Final = _names(cast("dict[str, list[str]]", _PYPROJECT["project"])["dependencies"])
DEV: Final = _names(cast("dict[str, list[str]]", _PYPROJECT["dependency-groups"])["dev"])
POLICY: Final = cast("_Licences", _PYPROJECT["tool"]["n64mini"]["licences"])


def licence_of(dist: str) -> str:
    """The SPDX expression if declared, else the trove classifiers, else the License field."""
    meta = metadata(dist)
    if expr := meta.get("License-Expression"):
        return expr
    classifiers = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::")
    ]
    if classifiers:
        return "; ".join(classifiers)
    field = meta.get("License") or ""
    return field.splitlines()[0] if field else ""


def ships_native_program(dist: str) -> bool:
    """True if the installed distribution puts a native executable in bin/ (or Scripts/)."""
    for f in distribution(dist).files or []:
        if "bin" in f.parts or "Scripts" in f.parts:
            path = Path(str(f.locate()))
            if path.is_file() and path.read_bytes()[:4].startswith(_EXECUTABLE_MAGIC):
                return True
    return False


def effective_licences(dist: str) -> list[str]:
    """The package's own licence, plus that of any native program it bundles."""
    bundled = POLICY["bundled"].get(dist)
    return [licence_of(dist), *([bundled] if bundled else [])]


def _approved(licence: str) -> bool:
    return not _COPYLEFT_RE.search(licence) and any(
        a.lower() in licence.lower() for a in POLICY["allowed"]
    )


@pytest.mark.parametrize("dist", [*RUNTIME, *DEV])
def test_bundled_native_programs_are_declared(dist: str) -> None:
    ships = ships_native_program(dist)
    declared = dist in POLICY["bundled"]
    assert not (ships and not declared), (
        f"{dist} bundles a native program: declare its licence in [tool.n64mini.licences.bundled]"
    )
    assert not (declared and not ships), f"{dist} is listed in bundled but ships no native program"


@pytest.mark.parametrize("dist", RUNTIME)
def test_runtime_dependencies_are_permissive(dist: str) -> None:
    for licence in effective_licences(dist):
        assert _approved(licence), f"runtime dependency {dist} has licence {licence!r}"
    assert dist not in POLICY["exceptions"], f"{dist} ships, so it can't be an exception"


@pytest.mark.parametrize("dist", DEV)
def test_dev_dependencies_are_approved_or_excepted(dist: str) -> None:
    try:
        licences = effective_licences(dist)
    except PackageNotFoundError:
        pytest.fail(f"{dist} is declared but not installed; run uv sync")
    if dist in POLICY["exceptions"]:
        assert POLICY["exceptions"][dist].strip(), f"{dist}: an exception needs a reason"
        return
    for licence in licences:
        assert _approved(licence), (
            f"dev dependency {dist} has licence {licence!r}: add an exception or replace it"
        )


def test_policy_entries_are_not_stale() -> None:
    deps = {*RUNTIME, *DEV}
    assert set(POLICY["exceptions"]) <= deps, "an exception names a package that isn't a dependency"
    assert set(POLICY["bundled"]) <= deps, "bundled names a package that isn't a dependency"


def test_the_policy_can_fail() -> None:
    """Red-confirmation: copyleft, unknown and undeclared-bundle cases really are rejected."""
    assert not _approved("GPL-2.0-only")
    assert not _approved("GNU Lesser General Public License v3 (LGPLv3)")
    assert not _approved("UNKNOWN")
    assert _approved("BSD-3-Clause")
    assert _approved("MPL-2.0")
    assert ships_native_program("ruff")  # the detector really sees a bundled binary
    assert not ships_native_program("pytest")  # and doesn't see one in a pure-Python package
