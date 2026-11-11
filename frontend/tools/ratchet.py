"""Quality ratchet: a metric may hold or improve, never silently regress.

The same idea as the CAD project's ``.gate_baseline`` and workspace3's
``mutation-score-floor.json`` / ESLint ``maxWarnings`` caps. The last recorded
value of each metric lives in ``.quality_baseline.json``. A run that does worse
fails. A run that does better tightens the baseline, so the gain is locked in.

    python tools/ratchet.py coverage .coverage.json
    python tools/ratchet.py mutation mutants/mutmut-cicd-stats.json [--check-only]
    python tools/ratchet.py suppressions .

``coverage`` and ``mutation`` are percentages that may only rise. ``suppressions``
counts lint/type/format/secret-scan escape hatches in src/, tests/ and tools/.
It may only fall, and each ``noqa`` / ``allowlist secret`` must carry a reason
(``# noqa: CODE - why``).

Lowering a baseline on purpose (deleting dead code with high coverage, say)
means editing the JSON in the same commit and saying why in the message.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tokenize
from pathlib import Path
from typing import Final, Literal, TypedDict, cast

BASELINE_FILE: Final = Path(__file__).resolve().parents[1] / ".quality_baseline.json"

type Metric = Literal["coverage", "mutation", "suppressions"]

HIGHER_IS_BETTER: Final[dict[Metric, bool]] = {
    "coverage": True,
    "mutation": True,
    "suppressions": False,
}
# Mutation runs are not perfectly repeatable (a slow mutant can flip between
# timeout and survived), so a re-run that reproduces the floor within this
# margin holds. Coverage and counts are deterministic: no tolerance.
EPSILON: Final[dict[Metric, float]] = {"coverage": 0.0, "mutation": 0.1, "suppressions": 0.0}

SCANNED_DIRS: Final = ("src", "tests", "tools")
_SUPPRESSION_RE: Final = re.compile(
    r"#\s*(noqa\b|type:\s*ignore|pyright:\s*ignore|fmt:\s*(?:skip|off)"
    r"|pragma:\s*(?:no\s*cover|allowlist\s+secret))"
)
_REASON_RE: Final = re.compile(
    r"#\s*(?:noqa:\s*[A-Z]+[0-9]+(?:\s*,\s*[A-Z]+[0-9]+)*|pragma:\s*allowlist\s+secret)\s+-\s+\S"
)


class Baseline(TypedDict):
    """Recorded best values (percent for coverage/mutation, a count for suppressions)."""

    coverage: float
    mutation: float
    suppressions: float


class _FileSummary(TypedDict):
    covered_lines: int
    num_statements: int
    covered_branches: int
    num_branches: int


class _FileEntry(TypedDict):
    summary: _FileSummary


class _CoverageJson(TypedDict):
    files: dict[str, _FileEntry]


# Only these are ratcheted (owner, 2026-11-06): the code that runs unattended on the console, and
# the validator, where a mistake is invisible. Everything else has just the floor in pyproject.toml.
RATCHETED: Final = ("n64mini/console/", "n64mini/theme_schema/")


class _MutmutStats(TypedDict):
    killed: int
    survived: int
    no_tests: int
    timeout: int


def coverage_percent(report: Path) -> float:
    """Line+branch coverage of the RATCHETED packages, from ``coverage json`` output.

    Combined the way coverage.py combines its total: covered lines and branches over all lines
    and branches. A report with none of those files fails loudly rather than reading as 100%.
    """
    data = cast("_CoverageJson", json.loads(report.read_text(encoding="utf-8")))
    chosen = [
        entry["summary"]
        for name, entry in data["files"].items()
        if any(package in name.replace("\\", "/") for package in RATCHETED)
    ]
    if not chosen:
        raise ValueError(f"no ratcheted files ({', '.join(RATCHETED)}) in {report}")
    covered = sum(s["covered_lines"] + s["covered_branches"] for s in chosen)
    total = sum(s["num_statements"] + s["num_branches"] for s in chosen)
    return round(100 * covered / total, 2) if total else 100.0


def mutation_percent(stats: Path) -> float:
    """Mutation score, computed the way Stryker does.

    detected = killed + timeout; undetected = survived + no_tests.
    Skipped / suspicious / segfault mutants are left out of the denominator.
    """
    data = cast("_MutmutStats", json.loads(stats.read_text(encoding="utf-8")))
    detected = data["killed"] + data["timeout"]
    considered = detected + data["survived"] + data["no_tests"]
    if considered == 0:
        raise ValueError(f"{stats}: mutmut produced no mutants")
    return round(100.0 * detected / considered, 2)


def find_suppressions(root: Path) -> list[tuple[Path, int, str]]:
    """Every suppression comment under ``root``'s source dirs, as (file, line, text)."""
    found: list[tuple[Path, int, str]] = []
    for sub in SCANNED_DIRS:
        for path in sorted((root / sub).rglob("*.py")):
            with path.open("rb") as f:
                # Real COMMENT tokens only: a string quoting a suppression is not one.
                found.extend(
                    (path, tok.start[0], tok.string)
                    for tok in tokenize.tokenize(f.readline)
                    if tok.type == tokenize.COMMENT and _SUPPRESSION_RE.search(tok.string)
                )
    return found


def unexplained(suppressions: list[tuple[Path, int, str]]) -> list[str]:
    """``noqa`` / ``allowlist secret`` comments that don't end in ``- reason``."""
    bad: list[str] = []
    for path, lineno, text in suppressions:
        needs_reason = "noqa" in text or "allowlist" in text
        if needs_reason and not _REASON_RE.search(text):
            bad.append(f"{path}:{lineno}: {text}")
    return bad


def load_baseline(path: Path = BASELINE_FILE) -> Baseline:
    """Read the baseline file."""
    return cast("Baseline", json.loads(path.read_text(encoding="utf-8")))


def _fmt(metric: Metric, value: float) -> str:
    return f"{value:.0f}" if metric == "suppressions" else f"{value:.2f}%"


def apply(
    metric: Metric, value: float, *, path: Path = BASELINE_FILE, check_only: bool = False
) -> tuple[bool, str]:
    """Compare ``value`` with the baseline; tighten it on improvement unless ``check_only``."""
    baseline = load_baseline(path)
    best = baseline[metric]
    sign = 1 if HIGHER_IS_BETTER[metric] else -1
    # > 0 means better. Rounded so 59.9 - 60.0 == -0.1 exactly, not -0.10000000000000142.
    delta = round(sign * (value - best), 6)
    if delta < -EPSILON[metric]:
        return False, f"{metric}: REGRESSION {_fmt(metric, best)} -> {_fmt(metric, value)}"
    if delta > 0 and not check_only:
        baseline[metric] = value
        path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
        return True, (
            f"{metric}: improved {_fmt(metric, best)} -> {_fmt(metric, value)}, baseline tightened"
        )
    return True, f"{metric}: {_fmt(metric, value)} (baseline {_fmt(metric, best)})"


def _measure(metric: Metric, report: Path) -> tuple[float, list[str]]:
    if metric == "coverage":
        return coverage_percent(report), []
    if metric == "mutation":
        return mutation_percent(report), []
    found = find_suppressions(report)
    return float(len(found)), unexplained(found)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns 1 on regression or an unexplained suppression."""
    parser = argparse.ArgumentParser(description="Hold quality metrics at their best.")
    parser.add_argument("metric", choices=tuple(HIGHER_IS_BETTER))
    parser.add_argument("report", type=Path, help="report file, or the root for suppressions")
    parser.add_argument("--check-only", action="store_true", help="never write the baseline")
    parser.add_argument("--baseline", type=Path, default=BASELINE_FILE)
    args = parser.parse_args(argv)
    baseline: Path = args.baseline
    metric = cast("Metric", args.metric)
    report: Path = args.report

    value, problems = _measure(metric, report)
    for problem in problems:
        print(f"suppression without a reason ('# noqa: CODE - why'): {problem}", file=sys.stderr)
    ok, message = apply(metric, value, path=baseline, check_only=bool(args.check_only))
    print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok and not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
