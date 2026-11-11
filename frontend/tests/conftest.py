from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from n64mini.paths import THEME_FILE

N64_THEME = THEME_FILE

type ThemeWriter = Callable[[Mapping[str, str | bytes]], Path]


def wrap(body: str, *, version: str = "7") -> str:
    """Add the ``<theme>`` / ``<formatVersion>`` boilerplate around ``body``."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<theme>\n  <formatVersion>{version}</formatVersion>\n{body}\n</theme>\n"
    )


@pytest.fixture
def write_theme(tmp_path: Path) -> ThemeWriter:
    """Write ``files`` (relative path -> text or bytes) and return theme.xml's path."""

    def _write(files: Mapping[str, str | bytes]) -> Path:
        for name, content in files.items():
            target = tmp_path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding="utf-8")
        return tmp_path / "theme.xml"

    return _write
