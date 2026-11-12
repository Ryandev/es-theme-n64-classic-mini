"""The strip of box art under the carousel (AC-T20): the engine's list, its order, the cursor."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st
from PIL import Image

from n64mini.console import strip
from n64mini.console.strip import (
    ART_TOP,
    CURSOR,
    SIZE,
    WINDOW,
    Game,
    StripError,
    compare_key,
    draw,
    extensions,
    generate,
    list_games,
    window,
)
from n64mini.paths import REPO_ROOT

if TYPE_CHECKING:
    from pathlib import Path

EXTS = frozenset({".z64", ".n64", ".v64", ".n64mini"})


def _roms(tmp_path: Path, files: list[str], gamelist: str = "") -> Path:
    roms = tmp_path / "roms"
    roms.mkdir()
    for name in files:
        (roms / name).write_bytes(b"")
    if gamelist:
        (roms / "gamelist.xml").write_text(f"<gameList>{gamelist}</gameList>", encoding="utf-8")
    return roms


def _art(path: Path, color: tuple[int, int, int], size: tuple[int, int] = (276, 200)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


def test_the_sort_key_upper_cases_like_the_engine() -> None:
    """StringUtil::compareIgnoreCase upper-cases a-z only per character; '_' sorts after 'Z'."""
    assert compare_key("Banjo-Tooie") == "BANJO-TOOIE"
    assert compare_key("straße") == "STRAßE"  # ß upper-cases to two letters: kept as it is
    names = ["_test", "banjo", "Banjo-Tooie", "1080", "~"]
    assert sorted(names, key=compare_key) == ["1080", "banjo", "Banjo-Tooie", "_test", "~"]


def test_the_list_is_the_engines_list_in_its_order(tmp_path: Path) -> None:
    """Hidden entries out, other extensions and dot-files out, files without an entry in."""
    gamelist = (
        "<game><path>./b.z64</path><name>Zelda</name></game>"
        "<game><path>./c.n64</path><name>Mario</name><sortname>Aardvark</sortname></game>"
        "<game><path>./d.z64</path><name>Hidden</name><hidden>true</hidden></game>"
        "<game><path>./Showcase or Full.n64mini</path><name>The Showcase</name>"
        "<sortname>~</sortname></game>"
        "<game><path>./gone.z64</path><name>Not on disk</name></game>"
    )
    files = ["b.z64", "c.n64", "d.z64", "Showcase or Full.n64mini", "e.V64", "x.txt", ".f.z64"]
    games = list_games(_roms(tmp_path, files, gamelist), EXTS)
    assert [(g.rom, g.sort_name) for g in games] == [
        ("c.n64", "Aardvark"),  # sortname wins over name
        ("e.V64", "e"),  # not in the gamelist: its file name's stem; extensions in any case
        ("b.z64", "Zelda"),
        ("Showcase or Full.n64mini", "~"),  # the card sorts last
    ]


def test_box_art_is_the_thumbnail_else_the_image(tmp_path: Path) -> None:
    gamelist = (
        "<game><path>./a.z64</path><name>A</name>"
        "<thumbnail>./media/thumbnails/a.png</thumbnail></game>"
        "<game><path>./b.z64</path><name>B</name><thumbnail>/abs/b.png</thumbnail></game>"
        "<game><path>./c.z64</path><name>C &amp; D</name></game>"
        "<game><path>./d.z64</path><name>D</name><image>./media/d.png</image></game>"
    )
    roms = _roms(tmp_path, ["a.z64", "b.z64", "c.z64", "d.z64"], gamelist)
    games = {g.rom: g for g in list_games(roms, EXTS)}
    assert games["a.z64"].art == roms / "media/thumbnails/a.png"
    assert str(games["b.z64"].art) == "/abs/b.png"
    assert games["c.z64"].art is None
    assert games["d.z64"].art == roms / "media/d.png"  # no thumbnail: the image, as the cards
    assert games["c.z64"].sort_name == "C & D"  # unescaped, as the engine reads it


def test_no_gamelist_lists_every_rom_by_its_stem(tmp_path: Path) -> None:
    games = list_games(_roms(tmp_path, ["b.z64", "a.z64"]), EXTS)
    assert [g.sort_name for g in games] == ["a", "b"]


@pytest.mark.parametrize(
    ("count", "index", "expected"),
    [
        (5, 3, range(5)),  # everything fits
        (WINDOW, 0, range(WINDOW)),
        (100, 0, range(WINDOW)),  # at the start the window can't go further left
        (100, 50, range(50 - WINDOW // 2, 50 + WINDOW // 2)),
        (100, 99, range(100 - WINDOW, 100)),  # nor further right at the end
    ],
)
def test_the_window_follows_the_selection(count: int, index: int, expected: range) -> None:
    assert window(count, index) == expected


@given(count=st.integers(1, 300), data=st.data())
def test_the_window_always_holds_the_selected_game(count: int, data: st.DataObject) -> None:
    index = data.draw(st.integers(0, count - 1))
    shown = window(count, index)
    assert index in shown
    assert len(shown) == min(count, WINDOW)
    assert shown.start >= 0
    assert shown.stop <= count


def _cursor_x(image: Image.Image) -> int:
    """The cursor's tip column: its widest row is at the top."""
    row = [x for x in range(image.width) if image.getpixel((x, 1)) == CURSOR]
    assert row, "no cursor drawn"
    return (row[0] + row[-1]) // 2


def test_the_cursor_sits_over_the_selected_games_art() -> None:
    games = [Game(f"{i}.z64", str(i), None) for i in range(3)]
    thumbs = [Image.new("RGBA", (46, 46), (10 * i, 0, 0, 255)) for i in range(3)]
    cell, left = 48, (SIZE[0] - 3 * 48) // 2  # a short list: 48 px each, centred
    for index in range(3):
        image = draw(games, index, thumbs)
        assert image.size == SIZE
        assert abs(_cursor_x(image) - (left + index * cell + cell // 2)) <= 1
        art_x = left + index * cell + 1
        assert image.getpixel((art_x + 5, ART_TOP + 10)) == (10 * index, 0, 0, 255)


def test_generate_writes_each_games_strip_and_removes_stale_ones(tmp_path: Path) -> None:
    gamelist = (
        "<game><path>./a.z64</path><name>A</name><thumbnail>./m/a.png</thumbnail></game>"
        "<game><path>./b.z64</path><name>B</name></game>"
    )
    roms = _roms(tmp_path, ["a.z64", "b.z64"], gamelist)
    _art(roms / "m/a.png", (200, 30, 30))
    out = tmp_path / "strip"
    out.mkdir()
    (tmp_path / "strip-base").mkdir()
    (tmp_path / "strip-mid").mkdir()
    (out / "old.z64.png").write_bytes(b"stale")
    (tmp_path / "strip-base/old.z64.png").write_bytes(b"stale")
    (tmp_path / "strip-mid/old.z64.png").write_bytes(b"stale")
    (out / "notes.txt").write_text("kept")
    assert generate(roms, out, EXTS) == 2
    assert sorted(p.name for p in out.iterdir()) == ["a.z64.png", "b.z64.png", "notes.txt"]
    assert sorted(p.name for p in (tmp_path / "strip-base").iterdir()) == ["a.z64.png", "b.z64.png"]
    assert sorted(p.name for p in (tmp_path / "strip-mid").iterdir()) == ["a.z64.png", "b.z64.png"]
    with Image.open(out / "b.z64.png") as b:
        image = b.convert("RGBA")
    cell, left = 48, (SIZE[0] - 2 * 48) // 2
    assert abs(_cursor_x(image) - (left + cell + cell // 2)) <= 1
    a_middle = (left + cell // 2, ART_TOP + (SIZE[1] - ART_TOP) // 2)
    assert image.getpixel(a_middle) == (200, 30, 30, 255)  # a's box art
    b_middle = (left + cell + cell // 2, a_middle[1])
    assert image.getpixel(b_middle) == (40, 44, 56, 255)  # b has none: a blank slot


def test_portrait_and_landscape_box_art_get_the_same_size_in_the_strip(tmp_path: Path) -> None:
    gamelist = (
        "<game><path>./jp.z64</path><name>A</name><thumbnail>./m/jp.png</thumbnail></game>"
        "<game><path>./us.z64</path><name>B</name><thumbnail>./m/us.png</thumbnail></game>"
    )
    roms = _roms(tmp_path, ["jp.z64", "us.z64"], gamelist)
    _art(roms / "m/jp.png", (200, 30, 30), size=(453, 640))  # Japanese vertical box
    _art(roms / "m/us.png", (30, 30, 200), size=(640, 469))  # US horizontal box
    out = tmp_path / "strip"
    generate(roms, out, EXTS)
    with Image.open(out / "jp.z64.png") as f:
        image = f.convert("RGBA")
    cell, left = 48, (SIZE[0] - 2 * 48) // 2
    jp_ys = [
        y for y in range(ART_TOP, SIZE[1]) if image.getpixel((left + cell // 2, y)) != (0, 0, 0, 0)
    ]
    us_ys = [
        y
        for y in range(ART_TOP, SIZE[1])
        if image.getpixel((left + cell + cell // 2, y)) != (0, 0, 0, 0)
    ]
    assert jp_ys == us_ys


def test_unreadable_box_art_fails_loud(tmp_path: Path) -> None:
    gamelist = "<game><path>./a.z64</path><name>A</name><thumbnail>./a.png</thumbnail></game>"
    roms = _roms(tmp_path, ["a.z64"], gamelist)
    (roms / "a.png").write_bytes(b"not an image")
    with pytest.raises(StripError, match=r"a\.z64: can't read its box art"):
        generate(roms, tmp_path / "out", EXTS)


def test_extensions_come_from_the_installed_system_config(tmp_path: Path) -> None:
    cfg = tmp_path / "es_systems_n64.cfg"
    cfg.write_text("<systemList><system><extension>.Z64 .n64mini</extension></system></systemList>")
    assert extensions(cfg) == {".z64", ".n64mini"}
    cfg.write_text("<!-- the <extension> tag --><extension>.v64</extension>")
    assert extensions(cfg) == {".v64"}  # the shipped config's comment names the tag too
    cfg.write_text("<systemList><system><extension> </extension></system></systemList>")
    with pytest.raises(StripError, match="lists no extensions"):
        extensions(cfg)
    with pytest.raises(StripError, match="can't read the extensions"):
        extensions(tmp_path / "missing.cfg")


def test_the_shipped_system_config_is_what_the_strip_reads() -> None:
    shipped = extensions(REPO_ROOT / "bundle/console/es_systems_n64.cfg")
    assert {".z64", ".n64", ".v64", ".n64mini"} <= shipped


def test_cli_draws_the_consoles_strips_and_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    userdata = tmp_path / "userdata"
    (userdata / "roms/n64").mkdir(parents=True)
    (userdata / "roms/n64/a.z64").write_bytes(b"")
    assert strip.main(["--userdata", str(userdata)]) == 1  # no system config yet
    assert "can't read the extensions" in capsys.readouterr().err
    cfg = userdata / "system/configs/emulationstation/es_systems_n64.cfg"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("<extension>.z64</extension>")
    assert strip.main(["--userdata", str(userdata)]) == 0
    assert capsys.readouterr().out == "strip: 1 games\n"
    assert (userdata / "system/n64mini/strip/a.z64.png").is_file()


def test_sync_topbar_updates_brand_and_pocket_frames(tmp_path: Path) -> None:
    userdata = tmp_path / "userdata"
    assert not strip.sync_topbar(userdata)
    brand_path = userdata / strip.THEME_BRAND
    brand_path.parent.mkdir(parents=True)
    brand = Image.new("RGBA", (1280, 90), (0, 0, 0, 0))
    brand.putpixel((580, 20), (200, 150, 20, 255))  # gold pixel in library icon region
    brand.putpixel((581, 20), (40, 40, 40, 255))  # non-gold rim pixel in library icon region
    brand.putpixel((680, 20), (20, 200, 80, 255))  # green pixel in music icon region
    brand.putpixel((681, 20), (40, 40, 40, 255))  # non-green rim pixel in music icon region
    brand.save(brand_path)
    # Full library mode (no showcase.json) and music OFF (theme.full.wav exists)
    (userdata / strip.MUSIC_FULL).parent.mkdir(parents=True)
    (userdata / strip.MUSIC_FULL).write_bytes(b"wav")
    assert strip.sync_topbar(userdata)
    assert (brand_path.parent / "brand.orig.png").is_file()
    for name in ("0", "1", "2"):
        raw = userdata / strip.TOPBAR / f"pocket-{name}.rgba"
        assert raw.is_file()
        assert len(raw.read_bytes()) == strip.POCKET_SIZE[0] * strip.POCKET_SIZE[1] * 4
    topbar_png = strip.topbar_brand_path(userdata)
    assert topbar_png.name == "brand-0-0.png"
    assert topbar_png.is_file()
    with Image.open(brand_path) as updated:
        rgba = updated.convert("RGBA")
        # Gold pixel swapped R/B to blue; green pixel muted to grey
        px_lib = rgba.getpixel((580, 20))
        assert isinstance(px_lib, tuple)
        assert len(px_lib) == 4
        assert px_lib[2] > px_lib[0]
        px_mus = rgba.getpixel((680, 20))
        assert isinstance(px_mus, tuple)
        assert len(px_mus) == 4
        assert abs(px_mus[0] - px_mus[1]) <= 10
    # Showcase ON and music ON restores original colors from brand.orig.png
    (userdata / strip.ROMS).mkdir(parents=True)
    (userdata / strip.ROMS / "gamelist.xml.showcase.json").write_text("{}")
    (userdata / strip.MUSIC_FULL).unlink()
    assert strip.sync_topbar(userdata)
    assert strip.topbar_brand_path(userdata).name == "brand-1-1.png"
    assert strip.topbar_brand_path(userdata).is_file()
    with Image.open(brand_path) as restored:
        rgba2 = restored.convert("RGBA")
        assert rgba2.getpixel((580, 20)) == (200, 150, 20, 255)
        assert rgba2.getpixel((680, 20)) == (20, 200, 80, 255)


def test_sync_topbar_swaps_generated_showcase_and_full_icons(tmp_path: Path) -> None:
    userdata = tmp_path / "userdata"
    brand_path = userdata / strip.THEME_BRAND
    brand_path.parent.mkdir(parents=True)
    Image.new("RGBA", (1200, 82), (0, 0, 0, 0)).save(brand_path)
    Image.new("RGBA", (64, 64), (245, 190, 36, 255)).save(brand_path.with_name("icon_showcase.png"))
    Image.new("RGBA", (64, 64), (62, 158, 248, 255)).save(brand_path.with_name("icon_full.png"))

    assert strip.sync_topbar(userdata)
    with Image.open(brand_path) as full_img:
        assert full_img.convert("RGBA").getpixel((600, 41)) == (62, 158, 248, 255)

    (userdata / strip.ROMS).mkdir(parents=True)
    (userdata / strip.ROMS / "gamelist.xml.showcase.json").write_text("{}")
    assert strip.sync_topbar(userdata)
    with Image.open(brand_path) as show_img:
        assert show_img.convert("RGBA").getpixel((600, 41)) == (245, 190, 36, 255)


def test_clear_removes_strip_pngs_across_all_three_layers(tmp_path: Path) -> None:
    out = tmp_path / "strip"
    strip.clear(out)  # missing folders are a no-op
    for d in (out, tmp_path / "strip-base", tmp_path / "strip-mid"):
        d.mkdir()
        (d / "a.z64.png").write_bytes(b"png")
    (out / "keep.txt").write_text("ok")
    strip.clear(out)
    assert sorted(p.name for p in out.iterdir()) == ["keep.txt"]
    assert list((tmp_path / "strip-base").iterdir()) == []
    assert list((tmp_path / "strip-mid").iterdir()) == []


def test_the_topbar_launcher_only_starts_the_gated_module() -> None:
    # The daemon used to be 650 lines of Python inside this script, outside every gate (its call
    # signatures were checked here by parsing it). It is now n64mini.console.topbar, type-checked
    # and tested in test_topbar.py, and the script must not grow code of its own again.
    sh_text = (REPO_ROOT / "frontend/src/n64mini/console/data/n64mini-topbar.sh").read_text(
        encoding="utf-8"
    )
    assert "python3 -m n64mini.console.topbar" in sh_text
    assert "python3 -c" not in sh_text
    assert len(sh_text.splitlines()) < 15  # a launcher, not a program
