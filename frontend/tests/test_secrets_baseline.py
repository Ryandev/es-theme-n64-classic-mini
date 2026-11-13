"""Every finding the secrets baseline clears must be a checksum or a commit pin (D3, 2026-11-13).

The baseline once had every finding marked ``is_secret: false`` in one go, unreviewed. Reviewed
one by one, all 17 were SHA-256 checksums and git commit pins. This keeps it that way: clearing
anything else (a token, a password) fails here and needs a person to look at it.
"""

from __future__ import annotations

import json
import re

from n64mini.paths import REPO_ROOT

BASELINE = REPO_ROOT / ".secrets.baseline"
# A JSON line holding one 40-hex git commit or 64-hex SHA-256, optionally as a keyed value.
PIN = re.compile(r'^\s*(?:"[a-z_0-9]+":\s*)?"(?:[0-9a-f]{40}|[0-9a-f]{64})",?\s*$')


def _cleared() -> list[tuple[str, int]]:
    results = json.loads(BASELINE.read_text(encoding="utf-8"))["results"]
    return [
        (path, item["line_number"])
        for path, items in results.items()
        for item in items
        if item.get("is_secret") is False
    ]


def test_every_cleared_finding_is_a_checksum_or_commit_pin() -> None:
    cleared = _cleared()
    assert cleared, "the baseline clears nothing: this test would check nothing"
    not_pins = [
        f"{path}:{line}"
        for path, line in cleared
        if not PIN.match((REPO_ROOT / path).read_text(encoding="utf-8").splitlines()[line - 1])
    ]
    assert not_pins == []


def test_the_pattern_rejects_a_token() -> None:
    """Red-confirm: a credential-looking value on a cleared line would not pass."""
    assert PIN.match('    "sha256": "' + "a" * 64 + '",')
    # Built from parts so the secrets scan doesn't flag this test itself.
    assert not PIN.match('    "api' + '_key": "ghp_' + "A1b2" * 9 + '",')
    assert not PIN.match('    "pass' + 'word": "' + "hunter2" * 6 + '",')
