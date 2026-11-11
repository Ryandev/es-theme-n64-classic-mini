"""Reduce an EmulationStation ``es_log.txt`` to what matters for the theme.

A 25-second run writes thousands of lines, almost all of them noise from the
engine's own environment (missing built-in fonts, help icons, network). This
keeps the theme-relevant warnings and errors, maps each one to the validator
code that should have reported it, and counts the rest:

    python tools/es_log.py PATH [--json] [--fail-on-theme]

Exit 0, or 1 with ``--fail-on-theme`` when the log holds a theme finding. The
JSON form is what the engine oracle (comparing engine output with the
validator's) consumes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

_LINE_RE: Final = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\t\[\d+\]\t(\w+)\t(.*)$")

# Theme warnings from the vendored engine source, mapped to the validator code
# that covers each one (docs/requirements/validator.md).
THEME_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("E010", re.compile(r'Included file ".*" not found!')),
    ("E030", re.compile(r'Unknown element of type ".*"!')),
    ("E031", re.compile(r"Unknown property : ")),
    ("E020", re.compile(r'missing "name" attribute!')),
    ("E005", re.compile(r'Feature missing "supported" attribute!')),
    ("E032", re.compile(r"has no <animation> items|Unknown storyboard property type")),
    (
        "E042",
        re.compile(r"random is only supported|unknow random element|only supports \{random\}"),
    ),
    ("E040", re.compile(r"Invalid color")),
    ("W005", re.compile(r'if ".*" expression is invalid')),
    ("E003", re.compile(r"formatVersion|Theme uses format version")),
)

# The engine's own environment, not the theme. Counted, never shown.
NOISE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"Could not find appropriate font face"),
    re.compile(r"Resource path not found: :/"),
    re.compile(r'Help icon ".*" - corresponding image file ":/'),
    re.compile(r"HttpReq::onError"),
    # GuiSaveState builds its own internal theme with <text name="gridtile"> and
    # <text name="gridtile:selected">, then its tiles look those names up as ninepatches.
    re.compile(
        r"requested mismatched theme type for \[grid\.gridtile(:selected)?\]"
        r' - expected "ninepatch", got "text"'
    ),
)
_SHOWN_LEVELS: Final = frozenset({"WARNING", "ERROR"})


@dataclass(frozen=True, slots=True)
class Entry:
    """One log message (continuation lines folded in)."""

    level: str
    message: str


@dataclass(frozen=True, slots=True)
class Summary:
    """The reduced log."""

    theme: list[tuple[str, str, int]]  # (validator code, message, occurrences)
    unclassified: list[tuple[str, str, int]]  # (level, message, occurrences) at WARNING/ERROR
    noise: int
    levels: dict[str, int]


def parse(text: str) -> list[Entry]:
    """Split a log into entries. Lines without a header continue the previous entry."""
    entries: list[Entry] = []
    for raw in text.replace("\r", "").split("\n"):
        if m := _LINE_RE.match(raw):
            entries.append(Entry(m.group(1), m.group(2).strip()))
        elif raw.strip() and entries:
            last = entries[-1]
            entries[-1] = Entry(last.level, f"{last.message} {raw.strip()}")
    return [e for e in entries if e.message]


def classify(entry: Entry) -> str | None:
    """The validator code for a theme message, ``"noise"``, or None if unclassified."""
    for code, pattern in THEME_PATTERNS:
        if pattern.search(entry.message):
            return code
    if any(p.search(entry.message) for p in NOISE_PATTERNS):
        return "noise"
    return None


def summarise(entries: list[Entry]) -> Summary:
    """Group the entries into theme findings, unclassified problems and noise."""
    theme: Counter[tuple[str, str]] = Counter()
    unclassified: Counter[tuple[str, str]] = Counter()
    noise = 0
    for entry in entries:
        kind = classify(entry)
        if kind == "noise":
            noise += 1
        elif kind is not None:
            theme[(kind, entry.message)] += 1
        elif entry.level in _SHOWN_LEVELS:
            unclassified[(entry.level, entry.message)] += 1
    return Summary(
        theme=[(code, msg, n) for (code, msg), n in sorted(theme.items())],
        unclassified=[(lvl, msg, n) for (lvl, msg), n in sorted(unclassified.items())],
        noise=noise,
        levels=dict(sorted(Counter(e.level for e in entries).items())),
    )


def render(summary: Summary) -> str:
    """The short human-readable report."""
    lines = [f"levels: {summary.levels}  (noise suppressed: {summary.noise})"]
    lines.append(f"theme findings: {len(summary.theme)}")
    lines += [f"  {code}  x{n}  {msg}" for code, msg, n in summary.theme]
    lines.append(f"unclassified warnings/errors: {len(summary.unclassified)}")
    lines += [f"  {lvl}  x{n}  {msg}" for lvl, msg, n in summary.unclassified]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Reduce es_log.txt to its theme findings.")
    parser.add_argument("log", type=Path)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--fail-on-theme", action="store_true", help="exit 1 on a theme finding")
    args = parser.parse_args(argv)
    log: Path = args.log
    if not log.is_file():
        print(f"es_log: {log} not found", file=sys.stderr)
        return 2
    summary = summarise(parse(log.read_text(encoding="utf-8", errors="replace")))
    print(json.dumps(asdict(summary)) if args.json else render(summary))
    return 1 if args.fail_on_theme and summary.theme else 0


if __name__ == "__main__":
    raise SystemExit(main())
