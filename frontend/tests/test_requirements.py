"""Gate: docs/requirements/validator.md stays executable and cannot silently rot.

Workspace3's "living requirements layer", applied to the validator. Checks:

1. every validator rule code is specified by at least one requirement;
2. every requirement cites at least one test, and every cited test exists;
3. every ``engine`` grounding quotes a string that still exists verbatim in the
   vendored Batocera source, so an upstream change that removes the behaviour
   turns this red;
4. requirement ids are unique and well-formed.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Final

import pytest

from n64mini.paths import FRONTEND_ROOT, REPO_ROOT, VENDOR_DIR
from n64mini.theme_schema import Code

REQUIREMENTS: Final = REPO_ROOT / "docs" / "requirements" / "validator.md"
TESTS_DIR: Final = FRONTEND_ROOT / "tests"

_HEADING_RE: Final = re.compile(r"^### (AC-V\d{2}): (.+) \(([EW]\d{3})\)\s*$")
_ENGINE_RE: Final = re.compile(r"^- \*\*Grounding:\*\* engine `([\w.]+)`: `(.+)`\s*$")
_PROJECT_RE: Final = re.compile(r"^- \*\*Grounding:\*\* project rule\. \S")
_TESTS_RE: Final = re.compile(r"^- \*\*Tests:\*\* (.+)$")
_TEST_REF_RE: Final = re.compile(r"`([\w]+\.py)::(test_\w+)`")


@dataclass(slots=True)
class Requirement:
    ac: str
    code: str
    engine: list[tuple[str, str]]
    project_rule: bool
    tests: list[tuple[str, str]]


@cache
def _requirements() -> tuple[Requirement, ...]:
    reqs: list[Requirement] = []
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        if heading := _HEADING_RE.match(line):
            reqs.append(Requirement(heading[1], heading[3], [], project_rule=False, tests=[]))
        elif reqs and (engine := _ENGINE_RE.match(line)):
            reqs[-1].engine.append((engine[1], engine[2]))
        elif reqs and _PROJECT_RE.match(line):
            reqs[-1].project_rule = True
        elif reqs and (tests := _TESTS_RE.match(line)):
            reqs[-1].tests.extend(_TEST_REF_RE.findall(tests[1]))
    return tuple(reqs)


@cache
def _test_functions(file: str) -> frozenset[str]:
    tree = ast.parse((TESTS_DIR / file).read_text(encoding="utf-8"))
    return frozenset(
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
    )


REQS = _requirements()


def test_requirements_were_parsed() -> None:
    assert len(REQS) >= len(Code)


def test_ids_are_unique() -> None:
    ids = [r.ac for r in REQS]
    assert len(ids) == len(set(ids))


def test_every_rule_code_is_specified() -> None:
    assert {c.value for c in Code} - {r.code for r in REQS} == set()


def test_no_requirement_names_an_unknown_code() -> None:
    assert {r.code for r in REQS} - {c.value for c in Code} == set()


@pytest.mark.parametrize("req", REQS, ids=lambda r: r.ac)
def test_requirement_is_grounded(req: Requirement) -> None:
    assert req.engine or req.project_rule, f"{req.ac} has no Grounding line"


@pytest.mark.parametrize("req", REQS, ids=lambda r: r.ac)
def test_engine_grounding_still_exists_upstream(req: Requirement) -> None:
    for file, needle in req.engine:
        source = (VENDOR_DIR / file).read_text(encoding="utf-8")
        assert needle in source, f"{req.ac}: {needle!r} no longer in vendored {file}"


@pytest.mark.parametrize("req", REQS, ids=lambda r: r.ac)
def test_cited_tests_exist(req: Requirement) -> None:
    assert req.tests, f"{req.ac} cites no tests"
    for file, func in req.tests:
        assert (TESTS_DIR / file).is_file(), f"{req.ac}: no test file {file}"
        assert func in _test_functions(file), f"{req.ac}: {file}::{func} does not exist"


def test_doc_is_found_where_the_blind_approver_reads_it() -> None:
    assert REQUIREMENTS.is_relative_to(REPO_ROOT / "docs" / "requirements")
    assert Path(".claude/agents/blind-approver.md") in [
        p.relative_to(REPO_ROOT) for p in (REPO_ROOT / ".claude" / "agents").glob("*.md")
    ]


# ---------------------------------------------------------------- docs/requirements/theme.md

THEME_REQUIREMENTS: Final = REPO_ROOT / "docs" / "requirements" / "theme.md"
_THEME_HEADING_RE: Final = re.compile(r"^### (AC-T\d{2}): (.+)$")
_STATUS_RE: Final = re.compile(r"^- \*\*Status:\*\* (done|planned)\b")
_VERIFY_RE: Final = re.compile(
    r"^- \*\*Verify:\*\* ((?:gate|render|engine|look)(?:, (?:gate|render|engine|look))*)$"
)


@dataclass(slots=True)
class ThemeRequirement:
    ac: str
    status: str | None
    verify: list[str]
    tests: list[tuple[str, str]]


@cache
def _theme_requirements() -> tuple[ThemeRequirement, ...]:
    reqs: list[ThemeRequirement] = []
    for line in THEME_REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        if heading := _THEME_HEADING_RE.match(line):
            reqs.append(ThemeRequirement(heading[1], None, [], []))
        elif reqs and (status := _STATUS_RE.match(line)):
            reqs[-1].status = status[1]
        elif reqs and (verify := _VERIFY_RE.match(line)):
            reqs[-1].verify = verify[1].split(", ")
        elif reqs and (tests := _TESTS_RE.match(line)):
            reqs[-1].tests.extend(_TEST_REF_RE.findall(tests[1]))
    return tuple(reqs)


THEME_REQS = _theme_requirements()


def test_theme_requirements_were_parsed() -> None:
    assert len(THEME_REQS) >= 10
    assert len({r.ac for r in THEME_REQS}) == len(THEME_REQS)


@pytest.mark.parametrize("req", THEME_REQS, ids=lambda r: r.ac)
def test_theme_requirement_has_status_and_verification(req: ThemeRequirement) -> None:
    assert req.status in {"done", "planned"}, f"{req.ac}: missing or bad Status line"
    assert req.verify, f"{req.ac}: missing or bad Verify line"


@pytest.mark.parametrize("req", [r for r in THEME_REQS if r.status == "done"], ids=lambda r: r.ac)
def test_done_theme_requirements_cite_existing_tests(req: ThemeRequirement) -> None:
    assert req.tests, f"{req.ac} is done but cites no tests"
    for file, func in req.tests:
        assert func in _test_functions(file), f"{req.ac}: {file}::{func} does not exist"
