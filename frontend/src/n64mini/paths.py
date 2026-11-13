"""Repository paths, found by walking up from this file to marker files.

Markers make this work from the checkout and also from relocated copies such
as mutmut's ``mutants/`` tree, which sits one level deeper than ``src/``. Set
``N64MINI_REPO_ROOT`` to override where the theme is found.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final


def _find_up(start: Path, marker: str) -> Path:
    for parent in start.parents:
        if (parent / marker).exists():
            return parent
    msg = f"no parent of {start} contains {marker!r}"
    raise FileNotFoundError(msg)


_HERE: Final = Path(__file__).resolve()

FRONTEND_ROOT: Final = _find_up(_HERE, "pyproject.toml")
REPO_ROOT: Final = Path(
    os.environ.get("N64MINI_REPO_ROOT") or _find_up(FRONTEND_ROOT, "themes/n64-classic-mini")
)

VENDOR_DIR: Final = FRONTEND_ROOT / "vendor" / "batocera-emulationstation"
# A snapshot of the theme's own render: it proves "unchanged", never "matches the design". The
# design reference is the owner's private mock-up (``./run design-check``).
GOLDEN_REFERENCE: Final = FRONTEND_ROOT / "assets" / "golden_reference.png"
SVG_ASSETS_DIR: Final = FRONTEND_ROOT / "assets" / "svg"
DEFAULT_BRAND_TEXT: Final = "CLASSIC 64"


def brand_text() -> str:
    """Brand wordmark text from ``N64MINI_BRAND_TEXT`` (default ``CLASSIC 64``)."""
    return (os.environ.get("N64MINI_BRAND_TEXT") or DEFAULT_BRAND_TEXT).strip()


def svg_asset(name: str) -> Path:
    """Resolve an SVG file from env override or ``SVG_ASSETS_DIR``."""
    stem_key = f"N64MINI_SVG_{Path(name).stem.upper()}"
    override = os.environ.get(stem_key)
    if override:
        return Path(override)
    svg_dir = os.environ.get("N64MINI_SVG_DIR")
    if svg_dir:
        candidate = Path(svg_dir) / name
        if candidate.is_file():
            return candidate
    return SVG_ASSETS_DIR / name


THEME_DIR: Final = REPO_ROOT / "themes" / "n64-classic-mini"
THEME_FILE: Final = THEME_DIR / "theme.xml"
THEME_ART_DIR: Final = THEME_DIR / "art"
THEME_FONTS_DIR: Final = THEME_DIR / "fonts"

# Local-only research material (git-ignored); code must treat it as optional.
REFERENCES_DIR: Final = REPO_ROOT / "docs" / "references"

HARNESS_DIR: Final = REPO_ROOT / "test_harness"
SCREENSHOTS_DIR: Final = HARNESS_DIR / "screenshots"
