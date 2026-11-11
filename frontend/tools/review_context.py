"""Build the context directory for a ``/review`` run (adapted from workspace3's prepare.mjs).

Everything deterministic about a review lives here, so the agents only judge:

    python tools/review_context.py [--base REF]

writes ``<repo>/.review/<head>/`` with ``diff.patch``, ``files.txt``,
``commits.txt``, ``rules.txt`` and ``meta.json``, then prints the meta as
JSON. Exit 2 means there is nothing to review (an empty diff), which is normal
and not a failure.

The base defaults to the merge-base with ``main`` on a branch, or to
``HEAD~1`` on ``main`` itself (reviewing the last commit).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Final, TypedDict

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
TRUNK: Final = "main"

# Changes under these paths alter what the validator reports, so the blind pass runs.
_VALIDATOR_PATHS: Final = (
    "frontend/src/n64mini/theme_schema/",
    "frontend/vendor/",
    "docs/requirements/",
)
_ALWAYS_RULES: Final = ("AGENTS.md",)


class ReviewMeta(TypedDict):
    """What ``meta.json`` holds, and what the command reads."""

    ctx: str
    base: str
    head: str
    files: list[str]
    needs_blind_pass: bool
    rules: list[str]


class GitError(RuntimeError):
    """A git command failed; the message carries git's own stderr."""


def _git(*args: str, cwd: Path = REPO_ROOT) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def needs_blind_pass(files: list[str]) -> bool:
    """True if any changed file can alter what the validator reports."""
    return any(f.startswith(_VALIDATOR_PATHS) for f in files)


def rules_for(files: list[str]) -> list[str]:
    """The rule and spec files a reviewer must read for this change, repo-relative."""
    rules: list[str] = list(_ALWAYS_RULES)
    if any(f.startswith("frontend/") for f in files):
        rules.append("frontend/AGENTS.md")
    if needs_blind_pass(files):
        rules += ["docs/requirements/validator.md", "docs/MFIC.md"]
    if any(f.startswith(("themes/", ".agents/")) for f in files):
        rules.append(".agents/skills/batocera-theme-harness/SKILL.md")
    return rules


def default_base(cwd: Path = REPO_ROOT) -> str:
    """The merge-base with the trunk on a branch, else the previous commit."""
    branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd).strip()
    if branch != TRUNK:
        return _git("merge-base", TRUNK, "HEAD", cwd=cwd).strip()
    return _git("rev-parse", "HEAD~1", cwd=cwd).strip()


def build(base: str, cwd: Path = REPO_ROOT) -> ReviewMeta | None:
    """Write the context directory; None if the diff is empty."""
    head = _git("rev-parse", "--short", "HEAD", cwd=cwd).strip()
    files = [f for f in _git("diff", "--name-only", f"{base}...HEAD", cwd=cwd).splitlines() if f]
    if not files:
        return None
    ctx = cwd / ".review" / head
    ctx.mkdir(parents=True, exist_ok=True)
    (ctx / "diff.patch").write_text(_git("diff", f"{base}...HEAD", cwd=cwd), encoding="utf-8")
    (ctx / "files.txt").write_text("\n".join(files) + "\n", encoding="utf-8")
    commits = _git("log", "--reverse", "--format=%h %s%n%b", f"{base}..HEAD", cwd=cwd)
    (ctx / "commits.txt").write_text(commits, encoding="utf-8")
    rules = rules_for(files)
    (ctx / "rules.txt").write_text("\n".join(rules) + "\n", encoding="utf-8")
    meta: ReviewMeta = {
        "ctx": str(ctx),
        "base": base,
        "head": head,
        "files": files,
        "needs_blind_pass": needs_blind_pass(files),
        "rules": rules,
    }
    (ctx / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: 0 with meta JSON on stdout, 2 if nothing to review, 1 on git error."""
    parser = argparse.ArgumentParser(description="Prepare the context for a /review run.")
    parser.add_argument("--base", help="git ref to diff against (default: see module doc)")
    args = parser.parse_args(argv)
    try:
        base: str = args.base or default_base()
        meta = build(base)
    except GitError as exc:
        print(f"review_context: {exc}", file=sys.stderr)
        return 1
    if meta is None:
        print(f"review_context: nothing to review ({base}...HEAD is empty)", file=sys.stderr)
        return 2
    print(json.dumps(meta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
