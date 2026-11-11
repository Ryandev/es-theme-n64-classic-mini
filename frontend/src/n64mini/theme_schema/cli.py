"""``n64mini-validate-theme``: validate a Batocera theme from the command line."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from n64mini.theme_schema.validate import validate_theme


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns a process exit code."""
    parser = argparse.ArgumentParser(
        description="Validate Batocera EmulationStation theme XML against the engine grammar."
    )
    parser.add_argument("theme", type=Path, help="theme directory or its theme.xml")
    parser.add_argument(
        "--system",
        action="append",
        default=None,
        help="system to resolve ${system.theme} for (repeatable; default: n64)",
    )
    parser.add_argument(
        "--warnings-as-errors", "-W", action="store_true", help="fail on warnings too"
    )
    args = parser.parse_args(argv)

    theme: Path = args.theme
    theme_file = (theme / "theme.xml" if theme.is_dir() else theme).resolve()
    if not theme_file.is_file():
        print(f"error: {theme_file} not found", file=sys.stderr)
        return 2
    systems: list[str] = args.system or ["n64"]
    strict: bool = args.warnings_as_errors

    failed = False
    for system in systems:
        result = validate_theme(theme_file, system=system)
        for issue in result.issues:
            print(issue.format(root=theme_file.parent.parent))
        status = "ok" if result.ok(warnings_as_errors=strict) else "FAILED"
        print(
            f"[{system}] {status}: {len(result.files)} file(s), "
            f"{len(result.errors)} error(s), {len(result.warnings)} warning(s)"
        )
        failed |= not result.ok(warnings_as_errors=strict)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
