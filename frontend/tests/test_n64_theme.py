"""Gate: the real themes/n64-classic-mini must validate with zero errors."""

from __future__ import annotations

import re
import subprocess
from typing import TYPE_CHECKING

from lxml import etree

from n64mini.console.strip import SIZE as STRIP_SIZE
from n64mini.console.strip import STRIP_DIR
from n64mini.paths import VENDOR_DIR
from n64mini.theme_assets.background import BOTTOM_Y
from n64mini.theme_assets.console_bar import CONSOLE_SIZE
from n64mini.theme_assets.frames import CORNER
from n64mini.theme_schema import Code, Issue, validate_theme
from n64mini.theme_schema.cli import main
from tests.conftest import N64_THEME, ThemeWriter, wrap

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

# Known, accepted warnings. Remove an entry when the underlying issue is fixed.
ACCEPTED_WARNINGS: set[Code] = set()


def test_n64_theme_has_no_errors() -> None:
    result = validate_theme(N64_THEME, system="n64")
    assert [i.format() for i in result.errors] == []
    assert {i.code for i in result.warnings} <= ACCEPTED_WARNINGS
    assert len(result.files) >= 4  # theme.xml + config, base, n64/custom


def test_cli_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(N64_THEME.parent)]) == 0
    assert main([str(N64_THEME.parent), "--warnings-as-errors"]) == 0
    assert main([str(N64_THEME.parent / "missing")]) == 2
    out = capsys.readouterr().out
    assert "[n64] ok:" in out


def test_cli_reports_each_system_and_issue(
    write_theme: ThemeWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    theme = write_theme(
        {
            "theme.xml": wrap("<include>./$system/x.xml</include>"),
            "n64/x.xml": "<theme><view name='grid'><text name='t'><color>#EE2A30</color>"
            "</text></view></theme>",
            "default/x.xml": "<theme/>",  # every other system falls back here (AC-V22)
        }
    )
    assert main([str(theme), "--system", "n64", "--system", "snes"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].endswith(
        "n64/x.xml:1: warning W001: color='#EE2A30': HtmlColor::parse leaves "
        "alpha at 0 for '#RRGGBB'; write EE2A30 or EE2A30FF instead"
    )
    assert out[1] == "[n64] ok: 2 file(s), 0 error(s), 1 warning(s)"
    assert out[2] == "[snes] ok: 2 file(s), 0 error(s), 0 warning(s)"
    assert main([str(theme), "--system", "n64", "-W"]) == 1


def test_cli_missing_theme_goes_to_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(tmp_path / "nope.xml")]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not found" in captured.err


def test_issue_format_outside_root(tmp_path: Path) -> None:
    issue = Issue(Code.BAD_VALUE, tmp_path / "a.xml", None, "msg")
    assert issue.format(tmp_path / "elsewhere") == f"{tmp_path / 'a.xml'}: error E040: msg"
    assert issue.format(tmp_path) == "a.xml: error E040: msg"


def _grid() -> etree._Element:
    root = etree.parse(str(N64_THEME.parent / "base.xml")).getroot()
    grid = root.find("view[@name='grid']")
    assert grid is not None
    return grid


def _template() -> dict[str, etree._Element]:
    template = _grid().find("imagegrid[@name='gamegrid']/itemTemplate")
    assert template is not None, "the cards have no item template (AC-T19)"
    return {str(c.get("name")): c for c in template.iterchildren("*")}


def test_card_frames_are_the_generated_nine_patches() -> None:
    """AC-T06: the XML and the frame generator agree on the frames and their corner size."""
    cards = _template()
    for name, art in (("frame", "tile_default.png"), ("selectedframe", "tile_selected.png")):
        assert cards[name].tag == "ninepatch"
        assert cards[name].findtext("path") == f"./art/{art}"
        assert cards[name].findtext("cornerSize") == f"{CORNER} {CORNER}"
    # The gold frame shows on the selected card only: hidden, faded in on activate, no pulsing.
    selected = cards["selectedframe"]
    assert selected.findtext("opacity") == "0"
    assert selected.find("animateColor") is None
    assert selected.find("animateColorTime") is None
    fade_in = selected.find("storyboard[@event='activate']/animation[@property='opacity']")
    fade_out = selected.find("storyboard[@event='deactivate']/animation[@property='opacity']")
    assert fade_in is not None
    assert fade_in.get("to") == "1"
    assert fade_out is not None
    assert fade_out.get("to") == "0"


def _saves_pattern() -> tuple[str, str, int]:
    """Where N64 save states are and how the first is named, from Batocera's own files."""
    defaults = (VENDOR_DIR.parent / "batocera-43.1" / "configgen-defaults-n64.yml").read_text()
    emulator = re.search(r"^\s+emulator:[ \t]+(\S+)", defaults, re.MULTILINE)
    assert emulator is not None
    cfg = etree.parse(str(VENDOR_DIR.parent / "batocera-43.1" / "es_savestates.cfg")).getroot()
    entry = cfg.find(f"emulator[@name='{emulator.group(1)}']")
    assert entry is not None, f"no save-state entry for {emulator.group(1)}"
    assert entry.get("nofileextension") == "false"  # so {{romfilename}} is the full file name
    return entry.get("directory", ""), entry.get("file", ""), int(entry.get("firstslot", "-1"))


def test_each_card_shows_four_suspend_points_where_batocera_saves_them() -> None:
    """AC-T09: circle n is filled while the default N64 emulator's save state n exists."""
    directory, pattern, first = _saves_pattern()
    assert pattern == "{{romfilename}}.st{{slot0}}"
    cards = _template()
    for n in range(4):
        assert cards[f"slot{n}"].findtext("path") == "./art/save-empty.png"
        assert cards[f"slot{n}"].findtext("visible") == '{game:rom} != "Showcase or Full.n64mini"'
        saved = cards[f"slot{n}saved"]
        assert saved.findtext("path") == "./art/save-full.png"
        file = pattern.replace("{{romfilename}}", '" + {game:rom} + "').replace(
            "{{slot0}}", str(first + n)
        )
        assert saved.findtext("visible") == f'exists("/userdata/saves/{directory}/{file}")'
        assert cards[f"slot{n}"].findtext("pos") == saved.findtext("pos")


def test_each_card_shows_its_player_count_or_none() -> None:
    """AC-T19: {game:playerCount} is the highest number in "1-4" (FileData::getProperty)."""
    cards = _template()
    players = cards["players"]
    assert players.findtext("text") == "{game:playerCount}P"
    assert players.findtext("visible") == '{game:players} != ""'
    art = cards["art"]
    # The thumbnail, else the image, as FileData::getThumbnailPath falls back; fixed size.
    assert art.findtext("path") == "empty({game:thumbnail}) ? {game:image} : {game:thumbnail}"
    assert art.find("size") is not None
    assert art.find("maxSize") is None
    badge = cards["flipbadge"]
    bx, by = (float(v) for v in (badge.findtext("pos") or "").split())
    px, py = (float(v) for v in (players.findtext("pos") or "").split())
    s3x, s3y = (float(v) for v in (cards["slot3"].findtext("pos") or "").split())
    assert s3x < bx < px  # seated between the save circles and 1P
    assert by == py == s3y


def test_the_details_and_the_pill_are_gone() -> None:
    """AC-T08 withdrawn (owner, 2026-11-06): no year, publisher or details plate; the circles
    on each card replace the suspend-point pill."""
    names = {el.get("name") for el in _grid()}
    assert not {n for n in names if n and n.startswith("detail_")}
    assert "md_savestate" not in names
    assert _grid().find("gridtile") is None  # the template draws the cards


def test_the_strip_is_the_selected_games_image_from_the_console() -> None:
    """AC-T20: the console draws one strip per game; the theme shows the selected game's, at
    the size the console draws it, with subset layers spinning the cursor."""
    strip_el = _grid().find("image[@name='strip']")
    spin_el = _grid().find("image[@name='stripspin']")
    base_el = _grid().find("image[@name='stripbase']")
    assert strip_el is not None
    assert spin_el is not None
    assert base_el is not None
    assert strip_el.findtext("path") == f'"{STRIP_DIR}/" + {{game:rom}} + ".png"'
    assert spin_el.findtext("path") == f'"{STRIP_DIR}-mid/" + {{game:rom}} + ".png"'
    assert base_el.findtext("path") == f'"{STRIP_DIR}-base/" + {{game:rom}} + ".png"'
    width, height = (float(v) for v in (strip_el.findtext("size") or "").split())
    assert (round(width * 1280), round(height * 720)) == STRIP_SIZE
    for el in (strip_el, spin_el):
        anim = el.find("storyboard/animation[@property='opacity']")
        assert anim is not None
        assert anim.get("autoreverse") == "true"
        assert anim.get("repeat") == "forever"


def test_the_highlight_walks_across_the_cards() -> None:
    """AC-T04: centred selection with continuous carousel loop and animated camera slide."""
    grid = _grid().find("imagegrid[@name='gamegrid']")
    assert grid is not None
    assert grid.findtext("centerSelection") == "true"
    assert grid.findtext("scrollLoop") == "true"
    assert grid.findtext("animateSelection") == "true"
    assert grid.find("autoLayoutSelectedZoom") is None


def test_theme_opens_game_lists_in_the_carousel() -> None:
    """AC-T04: with default settings the engine uses the theme's defaultView (ThemeData.cpp).

    Without it, a console set to "automatic" picks some other view and the carousel never shows.
    """
    root = etree.parse(str(N64_THEME)).getroot()
    assert root.get("defaultView") == "grid"
    base = etree.parse(str(N64_THEME.parent / "base.xml")).getroot()
    assert base.find("view[@name='grid']/imagegrid[@name='gamegrid']") is not None


def test_help_prompts_are_styled_and_overlays_hidden() -> None:
    """AC-T16: the engine's 7-button helpsystem is hidden so the 3 Classic Mini prompts show."""
    base = etree.parse(str(N64_THEME.parent / "base.xml")).getroot()
    views = [v for v in base.iterchildren("view") if v.find("helpsystem[@name='help']") is not None]
    assert len(views) == 1
    named = {n.strip() for n in views[0].get("name", "").split(",")}
    assert named == {"system", "basic", "detailed", "grid"}
    help_el = views[0].find("helpsystem[@name='help']")
    assert help_el is not None
    assert help_el.findtext("textColor") == "00000000"
    assert help_el.findtext("iconColor") == "00000000"
    for tag in (
        "iconA",
        "iconB",
        "iconX",
        "iconY",
        "iconL",
        "iconR",
        "iconStart",
        "iconSelect",
        "iconUpDownLeftRight",
        "iconUpDown",
        "iconLeftRight",
    ):
        icon_path = help_el.findtext(tag)
        assert icon_path is not None, f"helpsystem missing {tag}"
        assert (N64_THEME.parent / icon_path).is_file()
    screen = base.find("view[@name='screen']")
    assert screen is not None
    for tag, name in (("text", "clock"), ("batteryIndicator", "batteryIndicator")):
        node = screen.find(f"{tag}[@name='{name}']/visible")
        assert node is not None, f"screen view doesn't hide {name}"
        assert node.text == "false"


def _engine_menu_elements() -> set[tuple[str, str]]:
    """The (type, name) pairs ThemeData::ThemeMenu looks up in the menu view."""
    cpp = (VENDOR_DIR / "ThemeData.cpp").read_text(encoding="utf-8")
    found = re.findall(r'getElement\("menu", "(\w+)", "(\w+)"\)', cpp)
    return {(etype, name) for name, etype in found}


def test_menu_elements_use_names_the_engine_reads() -> None:
    """AC-T17: the engine ignores any other name silently, so a typo would never show."""
    known = _engine_menu_elements()
    assert ("menuBackground", "menubg") in known  # the extraction itself works
    base = etree.parse(str(N64_THEME.parent / "base.xml")).getroot()
    menu = base.find("view[@name='menu']")
    assert menu is not None
    used = {(str(e.tag), e.get("name", "")) for e in menu.iterchildren("*")}
    assert used, "the theme styles no menu element"
    assert used <= known, f"names the engine never reads: {sorted(used - known)}"


def test_banner_title_scrolls_instead_of_being_cut() -> None:
    """AC-T07: sans-serif banner title (matching Fontworks Rodin Pro DB) with autoScroll."""
    base = etree.parse(str(N64_THEME.parent / "base.xml")).getroot()
    md_name = base.find("view[@name='grid']/text[@name='md_name']")
    assert md_name is not None
    assert md_name.findtext("fontPath") == "./fonts/DejaVuSans-Bold.ttf"
    assert md_name.findtext("autoScroll") == "horizontal"


def test_gamesplash_shows_the_boot_frame_on_black_without_game_screenshot() -> None:
    """Splash.cpp reads <theme>/gamesplash.xml on launch; it has no update(int deltaTime), so
    gamesplash.xml holds the carousel backdrop and selected centre card during the ~100 ms hand-off
    while n64mini.console.launch plays the card-flip, cubic ease-out expansion and cross-fade into
    launch_splash.png."""
    splash_cpp = (VENDOR_DIR / "Splash.cpp").read_text(encoding="utf-8")
    assert "update(int deltaTime)" not in splash_cpp
    assert "mBackground.update(" not in splash_cpp
    splash = etree.parse(str(N64_THEME.parent / "gamesplash.xml")).getroot()
    view = splash.find("view[@name='splash']")
    assert view is not None
    assert view.findtext("splash[@name='splash']/backgroundColor") == "000000FF"
    bg = view.find("image[@name='background']")
    assert bg is not None
    assert bg.findtext("path") == "./art/launch_splash.png"
    assert bg.findtext("pos") == "0.5 0.5"
    assert bg.findtext("maxSize") == "1 1"
    assert bg.find("storyboard") is None  # Splash.cpp never calls update(deltaTime)
    card_frame = view.find("image[@name='cardframe']")
    assert card_frame is not None
    assert card_frame.findtext("path") == "./art/tile_selected.png"
    card_art = view.find("image[@name='cardart']")
    assert card_art is not None
    assert "{game:thumbnail}" in (card_art.findtext("path") or "")
    label = view.find("text[@name='label']")
    assert label is not None
    assert label.findtext("visible") == "false"
    run = (N64_THEME.parents[2] / "frontend" / "run").read_text()
    assert 'cp "$REPO_ROOT/local/launch_splash.png" "$out/theme/art/launch_splash.png"' in run

    boot_splash_path = N64_THEME.parent / "splash.xml"
    boot_res = validate_theme(boot_splash_path, system="n64")
    assert boot_res.ok(warnings_as_errors=True), [i.format() for i in boot_res.issues]
    boot_view = etree.parse(str(boot_splash_path)).getroot().find("view[@name='splash']")
    assert boot_view is not None
    assert boot_view.findtext("splash[@name='splash']/backgroundColor") == "000000FF"
    assert boot_view.findtext("image[@name='background']/path") == "./art/launch_splash.png"
    assert boot_view.findtext("image[@name='progressbar']/visible") == "false"
    assert boot_view.findtext("image[@name='progressbar:active']/visible") == "false"
    assert boot_view.findtext("text[@name='label']/visible") == "false"


def test_the_system_view_shows_n64s_plate_over_the_background() -> None:
    """AC-T10: no pale default band, N64's own logo (else the engine prints "n64"), and the
    game count in the title banner."""
    base = etree.parse(str(N64_THEME.parent / "base.xml")).getroot()
    carousel = base.find("view[@name='system']/carousel[@name='systemcarousel']")
    assert carousel is not None
    assert carousel.findtext("color") == "00000000"
    info = base.find("view[@name='system']/text[@name='systemInfo']")
    assert info is not None
    assert info.findtext("backgroundColor") == "00000000"
    n64 = etree.parse(str(N64_THEME.parent / "n64" / "custom.xml")).getroot()
    logo = n64.findtext("view[@name='system']/image[@name='logo']/path")
    assert logo is not None
    assert (N64_THEME.parent / "n64" / logo).resolve().is_file()


def test_the_theme_plays_its_own_generated_music() -> None:
    """AC-T12: bgsound in the system view (AudioManager.cpp), pointing at the generated loop."""
    base = etree.parse(str(N64_THEME.parent / "base.xml")).getroot()
    path = base.findtext("view[@name='system']/sound[@name='bgsound']/path")
    assert path == "./music/theme.wav"
    assert (N64_THEME.parent / path).is_file()


def test_the_brand_mark_is_its_own_layer_the_bundle_can_swap() -> None:
    """DEC-5: the repo's own mark is art/brand.png, drawn over the background; the bundle
    replaces that one file with local/brand.png on the owner's machine."""
    base = etree.parse(str(N64_THEME.parent / "base.xml")).getroot()
    path = base.findtext("view/image[@name='brand']/path")
    assert path == "./art/brand.png"
    run = (N64_THEME.parents[2] / "frontend" / "run").read_text()
    assert 'cp "$REPO_ROOT/local/brand.png" "$out/theme/art/brand.png"' in run
    # Ignored whether local is a folder, the private workspace's symlink to one, or absent.
    check = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", "local"],
        cwd=N64_THEME.parents[2],
        check=False,
    )
    assert check.returncode == 0, "local is not git-ignored"


def test_help_prompts_sit_above_the_bottom_bar() -> None:
    """AC-T16: the prompts sit over the background just above the console's bar (AC-T21)."""
    base = etree.parse(str(N64_THEME.parent / "base.xml")).getroot()
    pos = base.findtext("view/helpsystem[@name='help']/pos")
    assert pos is not None
    y = float(pos.split()[1]) * 720
    strip_el = _grid().find("image[@name='strip']")
    assert strip_el is not None
    strip_y = float((strip_el.findtext("pos") or "").split()[1]) * 720
    strip_bottom = strip_y + STRIP_SIZE[1] / 2  # origin 0.5 0.5
    assert strip_el.findtext("origin") == "0.5 0.5"
    assert strip_bottom < y < BOTTOM_Y


def test_the_console_is_its_own_layer_the_bundle_can_swap() -> None:
    """AC-T21: art/console.png over the bar; the bundle replaces it with local/console.png."""
    base = etree.parse(str(N64_THEME.parent / "base.xml")).getroot()
    console = base.find("view/image[@name='console']")
    assert console is not None
    assert console.findtext("path") == "./art/console.png"
    x, y = (float(v) for v in (console.findtext("pos") or "").split())
    w, h = (float(v) for v in (console.findtext("size") or "").split())
    assert (round(w * 1280), round(h * 720)) == CONSOLE_SIZE  # drawn 1:1 at 720p
    assert x == 0.5
    assert (y - h / 2) * 720 > BOTTOM_Y  # inside the bar
    assert (y + h / 2) * 720 <= 720
    run = (N64_THEME.parents[2] / "frontend" / "run").read_text()
    assert 'cp "$REPO_ROOT/local/console.png" "$out/theme/art/console.png"' in run
