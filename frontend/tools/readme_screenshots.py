"""Regenerate the README screenshots in ``docs/screenshots/``: one command, from ``frontend/``.

    uv run python tools/readme_screenshots.py              # both pictures
    uv run python tools/readme_screenshots.py --drawer-only

- ``theme_carousel_full.png``: the real EmulationStation
  (``./run shot``), so they need the Windows engine sandbox (WSL2 on Windows; set up by
  ``.agents/skills/batocera-theme-harness/scripts/setup_live_harness.sh``). The game list is
  free N64 homebrew titles (N64brew Game Jam entries), mirrored by name only as empty files, with
  generated placeholder cards, so no commercial game or box art appears. (The Showcase list is
  the commercial catalogue, so it has no homebrew picture.)
- ``theme_suspend_drawer.png``: the Suspend Point List exists only on the console, so it is drawn
  by the console's own compositor (``n64mini.console.suspend --render-png``) from sample slots
  with plain placeholder thumbnails. It needs nothing but this repo.

Look at every picture before committing it. Exit 0 when all requested pictures were written.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from n64mini.console import suspend
from n64mini.paths import FRONTEND_ROOT, REPO_ROOT, THEME_DIR

SHOTS = REPO_ROOT / "docs" / "screenshots"
# Free homebrew games from the N64brew Game Jams (https://n64brew.dev/wiki/Homebrew_Projects).
HOMEBREW = (
    "Kumi-Daiko Beatoff 64",
    "Mission Lost Control",
    "Sblobber64",
    "Spirit Harvest",
    "Styx",
    "Super Snooper Spookers",
    "Telocation",
    "Voidblade",
    "Wizard of the Board",
)
SUSPEND_GAME = "Telocation"
DRAWER_ROM = "Telocation.z64"
# Plain colour fields with a slot number, standing in for in-game screenshots.
_SAMPLE_SLOTS = {0: (52, 120, 196), 1: (196, 92, 52), 2: (60, 150, 90)}


def _placeholder(path: Path, colour: tuple[int, int, int], label: str) -> None:
    img = Image.new("RGB", (320, 240), colour)
    ImageDraw.Draw(img).text((160, 120), label, fill=(255, 255, 255), anchor="mm")
    img.save(path)


def engine_shots(tmp: Path) -> int:
    """Take the carousel picture on the real engine with the homebrew list."""
    roms = tmp / "homebrew"
    roms.mkdir()
    for name in HOMEBREW:
        (roms / f"{name}.z64").write_bytes(b"")
    env = {**os.environ, "SANDBOX_ROMS": str(roms), "SANDBOX_SUSPEND": f"{SUSPEND_GAME}.z64"}
    out = SHOTS / "theme_carousel_full.png"
    cmd = ["bash", str(FRONTEND_ROOT / "run"), "shot", "", str(out), "full"]
    code = subprocess.run(cmd, cwd=FRONTEND_ROOT, env=env, check=False).returncode
    if code != 0:
        print(f"./run shot exited {code}", file=sys.stderr)
    return code


def drawer_shot(tmp: Path) -> int:
    """Draw the Suspend Point List with the console's compositor."""
    saves = tmp / "saves"
    roms = tmp / "roms"
    saves.mkdir()
    roms.mkdir()
    (roms / DRAWER_ROM).write_bytes(b"")
    for slot, colour in _SAMPLE_SLOTS.items():
        (saves / f"{DRAWER_ROM}.st{slot}").write_bytes(b"state")
        _placeholder(saves / f"{DRAWER_ROM}.st{slot}.png", colour, f"slot {slot + 1}")
    (saves / f"{DRAWER_ROM}.pending").write_bytes(b"state")
    _placeholder(saves / f"{DRAWER_ROM}.pending.png", (120, 120, 140), "new")
    out = SHOTS / "theme_suspend_drawer.png"
    args = ["--rom", DRAWER_ROM, "--roms", str(roms), "--saves", str(saves)]
    code = suspend.main([*args, "--theme", str(THEME_DIR), "--render-png", str(out)])
    if code != 0:
        print(f"suspend renderer exited {code}", file=sys.stderr)
    return code


def main(argv: list[str] | None = None) -> int:
    """Write the README screenshots."""
    parser = argparse.ArgumentParser(description="Regenerate the README screenshots.")
    parser.add_argument("--drawer-only", action="store_true", help="skip the engine pictures")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="n64mini-shots-") as tmp:
        if not args.drawer_only and (code := engine_shots(Path(tmp))) != 0:
            return code
        if (code := drawer_shot(Path(tmp))) != 0:
            return code
    print(f"wrote {SHOTS}; look at each picture before committing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
