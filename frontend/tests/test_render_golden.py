"""Golden-image regression for the Tier-1 renderer and the diff report.

This proves the render is *unchanged*, not that it is *right*: ``assets/golden_reference.png`` is
a snapshot of the theme's own output (rebuilt from it in aa92d75), so comparing against it can't
catch a design mistake. How close the theme is to the owner's design is ``./run design-check``,
which scores the render against the private mock-up (D1, 2026-11-13).

Pixels are compared by SHA-256 of the raw RGB bytes, so any change to layout,
z-order, fonts, colours or wireframe labels fails. After an *intentional*
change, review the new PNGs and refresh the hashes with:

    UPDATE_GOLDEN=1 uv run pytest tests/test_render_golden.py
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from PIL import Image

from n64mini.harness.compare import run
from n64mini.harness.render import render_view
from n64mini.paths import GOLDEN_REFERENCE
from tests.conftest import N64_THEME

GOLDEN = Path(__file__).with_name("golden") / "render_hashes.json"


def _digest(image: Image.Image) -> str:
    return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()


def test_game_list_render_matches_golden(tmp_path: Path) -> None:
    rendered = render_view(N64_THEME)
    run(reference=GOLDEN_REFERENCE, out_dir=tmp_path, theme_dir=N64_THEME.parent)
    with Image.open(tmp_path / "visual_diff_report.png") as report:
        actual = {
            "game_list_clean": _digest(rendered.clean),
            "game_list_wireframe": _digest(rendered.wireframe),
            "diff_report": _digest(report),
        }
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    expected: dict[str, str] = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert actual == expected, "render changed; see module docstring to refresh"
