"""Markdown hygiene: links must resolve on GitHub, not just on one machine."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from n64mini.paths import REFERENCES_DIR, REPO_ROOT

_SKIP_DIRS = {".git", ".venv", "mutants", "node_modules", "vendor"}
_LINK_RE = re.compile(r"\]\(([^)\s]+)\)")


def _markdown_files() -> list[Path]:
    # From git, like the other file-set gates: everything a commit could include, so ignored
    # trees (a Torch build's vendored docs, local research) are never scanned (AGENTS.md).
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "*.md"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted(
        REPO_ROOT / name
        for name in listed.split("\0")
        if name
        and not _SKIP_DIRS & set(Path(name).parts)
        and not (REPO_ROOT / name).is_relative_to(REFERENCES_DIR)
        and (REPO_ROOT / name).exists()  # deleted but not yet staged
    )


MARKDOWN = _markdown_files()


def test_markdown_files_were_found() -> None:
    assert REPO_ROOT / "README.md" in MARKDOWN
    assert REPO_ROOT / "docs" / "ports.md" in MARKDOWN
    ignored = ("test_harness", "N64", "frontend/.venv")  # git-ignored trees are never scanned
    assert not [p for p in MARKDOWN if p.relative_to(REPO_ROOT).as_posix().startswith(ignored)]


@pytest.mark.parametrize("doc", MARKDOWN, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_machine_specific_paths(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    assert "/home/" not in text
    assert "file:///" not in text


@pytest.mark.parametrize("doc", MARKDOWN, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_relative_links_resolve(doc: Path) -> None:
    broken: list[str] = []
    for target in _LINK_RE.findall(doc.read_text(encoding="utf-8")):
        if re.match(r"^[a-z]+:", target) or target.startswith("#"):
            continue  # external URL or in-page anchor
        path = (doc.parent / target.split("#", 1)[0]).resolve()
        if path.is_relative_to(REFERENCES_DIR):
            continue  # local-only research material (git-ignored)
        if not path.exists():
            broken.append(target)
    assert broken == []
