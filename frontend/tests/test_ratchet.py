from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from tools import ratchet

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def baseline(tmp_path: Path) -> Path:
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps({"coverage": 90.0, "mutation": 60.0, "suppressions": 5}), encoding="utf-8"
    )
    return path


def _stats(path: Path, killed: int, survived: int, no_tests: int = 0, timeout: int = 0) -> Path:
    data = {"killed": killed, "survived": survived, "no_tests": no_tests, "timeout": timeout}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_regression_fails_and_leaves_baseline(baseline: Path) -> None:
    ok, msg = ratchet.apply("coverage", 89.99, path=baseline)
    assert not ok
    assert msg == "coverage: REGRESSION 90.00% -> 89.99%"
    assert ratchet.load_baseline(baseline)["coverage"] == 90.0


def test_improvement_tightens_baseline(baseline: Path) -> None:
    ok, msg = ratchet.apply("mutation", 61.5, path=baseline)
    assert ok
    assert msg == "mutation: improved 60.00% -> 61.50%, baseline tightened"
    assert ratchet.load_baseline(baseline) == {
        "coverage": 90.0,
        "mutation": 61.5,
        "suppressions": 5,
    }


def test_mutation_tolerates_rerun_noise_but_not_more(baseline: Path) -> None:
    assert ratchet.apply("mutation", 59.9, path=baseline)[0]  # within epsilon 0.1
    assert not ratchet.apply("mutation", 59.89, path=baseline)[0]
    assert ratchet.load_baseline(baseline)["mutation"] == 60.0  # noise never lowers it


def test_suppressions_may_only_fall(baseline: Path) -> None:
    ok, msg = ratchet.apply("suppressions", 6, path=baseline)
    assert not ok
    assert msg == "suppressions: REGRESSION 5 -> 6"
    ok, msg = ratchet.apply("suppressions", 3, path=baseline)
    assert ok
    assert msg == "suppressions: improved 5 -> 3, baseline tightened"
    assert ratchet.load_baseline(baseline)["suppressions"] == 3


def test_check_only_never_writes(baseline: Path) -> None:
    before = baseline.read_text(encoding="utf-8")
    assert ratchet.apply("mutation", 99.0, path=baseline, check_only=True)[0]
    assert baseline.read_text(encoding="utf-8") == before


def test_equal_holds(baseline: Path) -> None:
    assert ratchet.apply("coverage", 90.0, path=baseline) == (
        True,
        "coverage: 90.00% (baseline 90.00%)",
    )


def _summary(covered: int, total: int, br_covered: int = 0, br_total: int = 0) -> dict[str, object]:
    return {
        "summary": {
            "covered_lines": covered,
            "num_statements": total,
            "covered_branches": br_covered,
            "num_branches": br_total,
        }
    }


def _cov(path: Path, files: dict[str, dict[str, object]]) -> Path:
    path.write_text(json.dumps({"files": files}), encoding="utf-8")
    return path


def test_only_console_and_validator_code_is_ratcheted(tmp_path: Path) -> None:
    """Lines and branches combined, across the ratcheted packages only."""
    cov = _cov(
        tmp_path / "cov.json",
        {
            "src/n64mini/console/flip.py": _summary(8, 10, 1, 2),  # 9 of 12
            "src\\n64mini\\theme_schema\\validate.py": _summary(3, 4),  # 3 of 4; Windows path
            "src/n64mini/harness/render.py": _summary(0, 100),  # not ratcheted
        },
    )
    assert ratchet.coverage_percent(cov) == 75.0  # 12 of 16


def test_a_report_without_ratcheted_files_fails_loud(tmp_path: Path) -> None:
    cov = _cov(tmp_path / "cov.json", {"src/n64mini/harness/render.py": _summary(1, 1)})
    with pytest.raises(ValueError, match="no ratcheted files"):
        ratchet.coverage_percent(cov)
    empty = _cov(tmp_path / "e.json", {"src/n64mini/console/__init__.py": _summary(0, 0)})
    assert ratchet.coverage_percent(empty) == 100.0


def test_mutation_score_matches_stryker_formula(tmp_path: Path) -> None:
    # detected = killed + timeout = 7; undetected = survived + no_tests = 3
    stats = _stats(tmp_path / "s.json", killed=6, survived=2, no_tests=1, timeout=1)
    assert ratchet.mutation_percent(stats) == 70.0


def test_mutation_score_refuses_an_empty_run(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no mutants"):
        ratchet.mutation_percent(_stats(tmp_path / "s.json", killed=0, survived=0))


def test_suppressions_are_real_comments_with_reasons(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text(
        "x = 1  # noqa: E501 - long generated line\n"
        "y = 2  # noqa: S311\n"
        "z = 3  # type: ignore[assignment]\n"
        's = "a string that says # noqa: E501 is not a suppression"\n'
        "# a plain comment\n",
        encoding="utf-8",
    )
    found = ratchet.find_suppressions(tmp_path)
    assert [(p.name, line) for p, line, _ in found] == [("a.py", 1), ("a.py", 2), ("a.py", 3)]
    assert len(ratchet.unexplained(found)) == 1
    assert "a.py:2" in ratchet.unexplained(found)[0]


def test_main_exit_codes(tmp_path: Path, baseline: Path) -> None:
    cov = _cov(tmp_path / "cov.json", {"src/n64mini/console/x.py": _summary(1, 2)})
    assert ratchet.main(["coverage", str(cov), "--baseline", str(baseline)]) == 1

    stats = _stats(tmp_path / "s.json", killed=7, survived=3)
    args = ["mutation", str(stats), "--baseline", str(baseline), "--check-only"]
    assert ratchet.main(args) == 0
    assert ratchet.load_baseline(baseline)["mutation"] == 60.0

    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "t.py").write_text("x = 1  # noqa: E501\n", encoding="utf-8")
    # One suppression is within the cap of 5, but it has no reason, so it fails.
    assert ratchet.main(["suppressions", str(tmp_path), "--baseline", str(baseline)]) == 1


def test_repo_suppressions_all_have_reasons() -> None:
    root = ratchet.BASELINE_FILE.parent
    assert ratchet.unexplained(ratchet.find_suppressions(root)) == []
