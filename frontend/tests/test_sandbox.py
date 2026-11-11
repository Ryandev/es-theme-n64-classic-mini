from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from PIL import Image

from n64mini.harness import sandbox
from n64mini.harness.render import MOCK_GAMES, mock_box_art, placeholder_box_art

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("rom", "name"),
    [
        ("Mario Kart 64 (U) [!].z64", "Mario Kart 64"),
        (
            "Legend of Zelda, The - Ocarina of Time (U) (V1.2) [!].v64",
            "The Legend of Zelda - Ocarina of Time",
        ),
        ("Diddy Kong Racing (U) (M2) (V1.1) [!].z64", "Diddy Kong Racing"),
        ("PilotWings_64.v64", "PilotWings 64"),
        ("Command & Conquer (U) [!].z64", "Command & Conquer"),
    ],
)
def test_display_name_reads_like_a_title(rom: str, name: str) -> None:
    assert sandbox.display_name(rom) == name


def test_populate_mirrors_names_without_copying_data(tmp_path: Path) -> None:
    dest = tmp_path / "n64"
    games = [("A & B (U) [!].z64", "A & B", (1, 2, 3)), ("C (E).v64", "C", (4, 5, 6))]
    gamelist = sandbox.populate(dest, games).read_text()
    assert (dest / "A & B (U) [!].z64").read_bytes() == b""  # placeholder, never game data
    assert "<path>./A &amp; B (U) [!].z64</path>" in gamelist
    assert "<image>./images/C (E).png</image>" in gamelist
    assert gamelist.count("<game>") == 2
    with Image.open(dest / "images" / "C (E).png") as art:
        assert art.size == (276, 200)


def test_the_mock_sandbox_uses_the_previews_own_art(tmp_path: Path) -> None:
    """Without the owner's ROMs, the engine shows exactly the images the preview draws."""
    assert sandbox.main([str(tmp_path / "n64")]) == 0
    for game in MOCK_GAMES:
        with Image.open(tmp_path / "n64" / "images" / f"{game.slug}.png") as f:
            assert f.convert("RGBA").tobytes() == mock_box_art(game).tobytes(), game.name


def test_the_owners_library_is_mirrored_by_name(tmp_path: Path) -> None:
    src = tmp_path / "roms"
    src.mkdir()
    (src / "Mario Kart 64 (U) [!].z64").write_bytes(b"not really a rom")
    (src / "readme.txt").write_text("")
    assert sandbox.main([str(tmp_path / "n64"), "--from", str(src)]) == 0
    assert (tmp_path / "n64" / "Mario Kart 64 (U) [!].z64").read_bytes() == b""
    assert "<name>Mario Kart 64</name>" in (tmp_path / "n64" / "gamelist.xml").read_text()


def test_populate_refuses_to_merge_or_take_non_roms(tmp_path: Path) -> None:
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "x").write_text("")
    with pytest.raises(FileExistsError):
        sandbox.populate(tmp_path / "full", [("a.z64", "a", (0, 0, 0))])
    with pytest.raises(ValueError, match="not a ROM"):
        sandbox.populate(tmp_path / "new", [("notes.txt", "x", (0, 0, 0))])


def test_colours_are_stable_and_vary() -> None:
    assert sandbox.colour_for("Mario Kart 64") == sandbox.colour_for("Mario Kart 64")
    assert len({sandbox.colour_for(n) for n in ("a", "b", "c", "d")}) > 1


@pytest.mark.parametrize(
    "title", ["Aidyn Chronicles - The First Mage", "Star Wars Shadows of the Empire " * 3]
)
def test_a_long_title_stays_inside_the_art(title: str) -> None:
    strip = placeholder_box_art(title, (0, 0, 0)).convert("RGB").crop((0, 166, 276, 200))
    lit = [x for x in range(276) for y in range(34) if strip.getpixel((x, y)) == (240, 240, 240)]
    assert lit, "no title drawn"
    assert min(lit) >= 1
    assert max(lit) <= 274


def test_suspend_points_are_named_as_batocera_expects(tmp_path: Path) -> None:
    """es_savestates.cfg (libretro): {{romfilename}}.state{{slot}} and .png, extension dropped."""
    written = sandbox.write_suspend_points(tmp_path, "Banjo-Kazooie (U) (!).v64", count=2)
    assert [p.name for p in written] == [
        "Banjo-Kazooie (U) (!).state1",
        "Banjo-Kazooie (U) (!).state1.png",
        "Banjo-Kazooie (U) (!).state2",
        "Banjo-Kazooie (U) (!).state2.png",
    ]
    assert written[2].stat().st_mtime > written[0].stat().st_mtime  # newer slot, newer state


def test_suspend_points_need_a_saves_folder(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        sandbox.main([str(tmp_path / "n64"), "--suspend-points", "x.z64"])
    assert (
        sandbox.main(
            [
                str(tmp_path / "n64b"),
                "--saves",
                str(tmp_path / "saves"),
                "--suspend-points",
                "x.z64",
            ]
        )
        == 0
    )
    assert (tmp_path / "saves" / "x.state3.png").is_file()
