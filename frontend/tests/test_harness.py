from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from n64mini.harness import compare as compare_cli
from n64mini.harness import live
from n64mini.harness.bindings import Device, evaluate, player_count, substitute
from n64mini.harness.metrics import ZONES, compare, heatmap, max_channel
from n64mini.harness.render import (
    MOCK_GAMES,
    SCREEN_H,
    SCREEN_W,
    SELECTED_INDEX,
    ElementSpec,
    MockGame,
    ThemeParser,
    build_device,
    expand_vars,
    game_field,
    is_visible,
    mock_box_art,
    render_grid_view,
    render_view,
)
from n64mini.harness.values import RenderError, parse_hex_color, parse_pair
from n64mini.paths import GOLDEN_REFERENCE
from tests.conftest import N64_THEME, ThemeWriter, wrap

if TYPE_CHECKING:
    from pathlib import Path


def _solid(color: tuple[int, int, int]) -> Image.Image:
    return Image.new("RGB", (SCREEN_W, SCREEN_H), color)


# ---------------------------------------------------------------- parsing helpers
@pytest.mark.parametrize(
    ("value", "expected"),
    [("0.5 0.25", (0.5, 0.25)), ("0.3", (0.3, 0.3)), ("", (9.0, 9.0)), (None, (9.0, 9.0))],
)
def test_parse_pair(value: str | None, expected: tuple[float, float]) -> None:
    assert parse_pair(value, (9.0, 9.0)) == expected


@pytest.mark.parametrize(("value", "match"), [("x y", "not a number pair"), ("1 2 3", "got 3")])
def test_parse_pair_rejects_garbage(value: str, match: str) -> None:
    with pytest.raises(RenderError, match=match):
        parse_pair(value, (0.0, 0.0))


@pytest.mark.parametrize(
    ("value", "expected"),
    [("EE2A30", (238, 42, 48, 255)), ("#EE2A3080", (238, 42, 48, 128)), ("", (1, 2, 3, 4))],
)
def test_parse_hex_color(value: str, expected: tuple[int, int, int, int]) -> None:
    assert parse_hex_color(value, (1, 2, 3, 4)) == expected


@pytest.mark.parametrize("value", ["nope", "FFF", "GGGGGG", "#12345"])
def test_parse_hex_color_rejects_garbage(value: str) -> None:
    with pytest.raises(RenderError, match="not a hex colour"):
        parse_hex_color(value)


def test_expand_vars_resolves_nested_and_keeps_unknown() -> None:
    variables = {"a": "${b}", "b": "gold"}
    assert expand_vars("${a}/${missing}", variables) == "gold/${missing}"


# ---------------------------------------------------------------- metrics
def test_identical_images_score_100() -> None:
    _, m = compare(_solid((10, 20, 30)), _solid((10, 20, 30)))
    assert m["overall"] == {
        "mae": 0.0,
        "rms": 0.0,
        "similarity_pct": 100.0,
        "within_tol_pct": 100.0,
    }
    assert [name for name, _ in ZONES] == list(m["zones"])


def test_known_difference_is_scored_exactly() -> None:
    _, m = compare(_solid((0, 0, 0)), _solid((51, 0, 0)))
    assert m["overall"]["mae"] == 17.0  # 51 / 3 channels
    assert m["overall"]["similarity_pct"] == round(100 * (1 - 17 / 255), 2)
    assert m["overall"]["within_tol_pct"] == 0.0


def test_tolerance_counts_pixels_at_threshold() -> None:
    _, m = compare(_solid((0, 0, 0)), _solid((8, 8, 8)))
    assert m["overall"]["within_tol_pct"] == 100.0


def test_compare_resizes_to_screen() -> None:
    small = Image.new("RGB", (640, 360), (5, 5, 5))
    diff, _ = compare(small, _solid((5, 5, 5)))
    assert diff.size == (SCREEN_W, SCREEN_H)


def test_heatmap_colours_by_max_channel() -> None:
    diff = Image.new("RGB", (3, 1))
    diff.putdata([(0, 0, 0), (0, 10, 2), (200, 0, 0)])
    assert list(max_channel(diff).tobytes()) == [0, 10, 200]
    hm = heatmap(diff)
    assert hm.getpixel((0, 0)) == (12, 18, 32)
    assert hm.getpixel((2, 0)) == (255, 0, 40)


# ---------------------------------------------------------------- render + CLI
def test_render_view_draws_the_themes_game_list() -> None:
    rendered = render_view(N64_THEME)  # its defaultView: the gamecarousel
    assert rendered.clean.size == rendered.wireframe.size == (SCREEN_W, SCREEN_H)
    assert rendered.clean.tobytes() != rendered.wireframe.tobytes()
    assert rendered.clean.getpixel((0, 0)) != (18, 22, 34)  # background image was composited


def test_compare_cli_writes_report_and_gates(tmp_path: Path) -> None:
    args = ["--theme-dir", str(N64_THEME.parent), "--out-dir", str(tmp_path)]
    assert compare_cli.main(args) == 0
    for name in ("xml_render_clean.png", "xml_render_wireframe.png", "visual_diff_report.png"):
        assert (tmp_path / name).is_file()
    metrics = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert set(metrics) == {"overall", "zones"}
    assert compare_cli.main([*args, "--min-similarity", "100.01"]) == 1


def test_compare_actual_screenshot(tmp_path: Path) -> None:
    metrics = compare_cli.run(reference=GOLDEN_REFERENCE, out_dir=tmp_path, actual=GOLDEN_REFERENCE)
    assert metrics["overall"]["similarity_pct"] == 100.0
    assert not (tmp_path / "xml_render_clean.png").exists()


def test_compare_requires_exactly_one_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="one of"):
        compare_cli.run(reference=GOLDEN_REFERENCE, out_dir=tmp_path)
    with pytest.raises(ValueError, match="only one"):
        compare_cli.run(
            reference=GOLDEN_REFERENCE,
            out_dir=tmp_path,
            theme_dir=N64_THEME.parent,
            actual=GOLDEN_REFERENCE,
        )


# ---------------------------------------------------------------- live (no ES needed)
def test_parse_inputs_validates_buttons() -> None:
    assert live.parse_inputs(" A, right ,,start") == ["a", "right", "start"]
    with pytest.raises(ValueError, match="jump"):
        live.parse_inputs("a,jump")


def test_capture_without_powershell_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live, "POWERSHELL", tmp_path / "missing.exe")
    with pytest.raises(live.CaptureError, match="WSL2"):
        live.capture_window(tmp_path / "out.png", pid=1)


def test_es_request_without_powershell_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live, "POWERSHELL", tmp_path / "missing.exe")
    with pytest.raises(live.CaptureError, match="WSL2"):
        live.es_request("/input", "a")


@pytest.mark.parametrize("endpoint", ["/input'; rm -rf x", "input", "/in put"])
def test_es_request_refuses_to_build_unsafe_commands(
    endpoint: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The endpoint is spliced into a PowerShell command, so only plain paths are allowed."""
    fake = tmp_path / "powershell.exe"
    fake.write_text("")
    monkeypatch.setattr(live, "POWERSHELL", fake)
    with pytest.raises(ValueError, match="refusing to build a request"):
        live.es_request(endpoint, "a")
    with pytest.raises(ValueError, match="refusing to build a request"):
        live.es_request("/input", "a", content_type="text/html")


def test_es_request_never_puts_a_body_in_the_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A body can hold quotes or be large binary; it goes in a file, never into PowerShell."""
    fake = tmp_path / "powershell.exe"
    fake.write_text("")
    monkeypatch.setattr(live, "POWERSHELL", fake)
    files: list[Path] = []

    def windows_path(path: Path) -> str:
        files.append(path)
        return "C:\\tmp\\body"

    sent: list[tuple[str, bytes]] = []

    def run(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        assert timeout > 0
        sent.append((args[-1], files[0].read_bytes()))  # read while the request is in flight
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    monkeypatch.setattr(live, "to_windows_path", windows_path)
    monkeypatch.setattr(live, "_run", run)
    body = "a'; Remove-Item C:\\ -Recurse; '" * 2000  # quotes, and far past a command line
    assert live.es_request("/input", body) == "ok"
    script, data = sent[0]
    assert "Remove-Item" not in script
    assert "-InFile 'C:\\tmp\\body'" in script
    assert data == body.encode()
    assert not files[0].exists()  # the temporary file is cleaned up


def test_live_cli_reports_an_unreachable_engine_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(live, "POWERSHELL", tmp_path / "missing.exe")
    assert live.main(["--pid", "1", "--navigate", "a", "--out", str(tmp_path / "x.png")]) == 1
    assert "error:" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("size", "pixels", "match"),
    [
        ((1800, 960), "noise", "captured 1800x960, expected 1280x720"),
        ((1280, 720), "blank", "captured a blank image"),
    ],
)
def test_capture_check_rejects_the_wrong_window_or_a_blank(
    tmp_path: Path, size: tuple[int, int], pixels: str, match: str
) -> None:
    """A capture of another window (a terminal, once) or of nothing must not pass as a shot."""
    out = tmp_path / "shot.png"
    img = Image.new("RGB", size, (0, 0, 0))
    if pixels == "noise":
        img.putpixel((5, 5), (255, 255, 255))
    img.save(out)
    with pytest.raises(live.CaptureError, match=match):
        live.check_capture(out, (1280, 720))


def test_capture_check_accepts_a_real_frame(tmp_path: Path) -> None:
    out = tmp_path / "shot.png"
    img = Image.new("RGB", (1280, 720), (10, 20, 30))
    img.putpixel((640, 360), (255, 220, 56))
    img.save(out)
    live.check_capture(out, (1280, 720))


# ---------------------------------------------------------------- metrics, in depth
@pytest.mark.parametrize(
    ("delta", "rgb"),
    [
        (4, (12, 18, 32)),  # still "exact"
        (10, (22, 88, 127)),  # cyan band: t = 0.5
        (16, (32, 158, 222)),  # band edge
        (32, (143, 194, 111)),  # yellow band: t = 0.5
        (48, (255, 230, 0)),
        (108, (255, 115, 20)),  # red band: t = 0.5
        (255, (255, 0, 40)),  # saturates
    ],
)
def test_heatmap_bands(delta: int, rgb: tuple[int, int, int]) -> None:
    diff = Image.new("RGB", (1, 1), (0, delta, 0))
    assert heatmap(diff).getpixel((0, 0)) == rgb


def test_difference_is_confined_to_its_zone() -> None:
    actual = _solid((0, 0, 0))
    actual.paste((90, 90, 90), (0, 0, SCREEN_W, 104))  # top header zone only
    _, m = compare(actual, _solid((0, 0, 0)))
    zones = m["zones"]
    assert zones["Top Header & 5-Icon Bar"]["mae"] == 90.0
    assert zones["Top Header & 5-Icon Bar"]["rms"] == 90.0
    assert zones["Top Header & 5-Icon Bar"]["box"] == (0, 0, 1280, 104)
    for name in ("Chiseled 3D Title Banner", "Center 5-Card Carousel", "Bottom Ribbon & Controls"):
        assert zones[name]["similarity_pct"] == 100.0
    assert m["overall"]["mae"] == round(90 * 104 / 720, 2)


def test_scores_are_rounded_to_two_places() -> None:
    _, m = compare(_solid((0, 0, 0)), _solid((1, 0, 0)))
    assert m["overall"] == {
        "mae": 0.33,
        "rms": 0.33,
        "similarity_pct": 99.87,
        "within_tol_pct": 100.0,
    }


# ---------------------------------------------------------------- theme parser
def test_theme_parser_collects_last_definition_and_paths(write_theme: ThemeWriter) -> None:
    theme = write_theme(
        {
            "theme.xml": wrap(
                "<variables><z>7</z></variables>"
                "<include>./inc.xml</include>"
                "<view name='system, grid'><image name='bg, bg2'><path>./a.png</path>"
                "<zIndex>${z}</zIndex></image></view>"
                "<feature supported='video'><view name='grid'>"
                "<imagegrid name='gamegrid'><autoLayout>3 1</autoLayout></imagegrid>"
                "<gridtile name='selected'><backgroundImage>./t.png</backgroundImage></gridtile>"
                "</view></feature>"
            ),
            "inc.xml": "<theme><view name='grid'><image name='bg'><size>1 1</size></image>"
            "</view></theme>",
        }
    )
    parsed = ThemeParser().load(theme)
    bg = parsed.elements[("grid", "image", "bg")]
    assert bg.props == {"size": "1 1", "path": str(theme.parent / "a.png"), "zIndex": "7"}
    assert ("system", "image", "bg2") in parsed.elements
    assert parsed.grid_props == {"autoLayout": "3 1"}
    assert parsed.gridtiles["selected"] == {"backgroundImage": str(theme.parent / "t.png")}
    assert parsed.gridtiles["default"] == {}


def test_theme_parser_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="theme XML not found"):
        ThemeParser().load(tmp_path / "theme.xml")


def test_mock_box_art_is_generated_not_read_from_the_theme() -> None:
    """AC-T05: the preview's box art stands in for the gamelist; it never comes from the theme."""
    arts = [mock_box_art(g) for g in MOCK_GAMES]
    assert {a.size for a in arts} == {(276, 200)}
    assert len({a.tobytes() for a in arts}) == len(MOCK_GAMES)  # one distinct panel per game
    assert mock_box_art(MOCK_GAMES[0]).tobytes() == arts[0].tobytes()  # deterministic


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ("<include>./missing.xml</include>", "included file not found"),
        (
            "<view name='grid'><image name='bg'><path>./gone.png</path></image></view>",
            "<image name='bg'>: path not found",
        ),
        (
            "<view name='grid'><text name='md_name'><fontPath>./gone.ttf</fontPath></text></view>",
            "fontPath not found",
        ),
        (
            "<view name='grid'><text name='md_name'><color>blue-ish</color></text></view>",
            "not a hex colour",
        ),
        (
            (
                "<view name='grid'><image name='bg'><path>./a.png</path><pos>left top</pos></image>"
                "</view>"
            ),
            "not a number pair",
        ),
    ],
)
def test_render_fails_loud_instead_of_guessing(
    write_theme: ThemeWriter, body: str, match: str
) -> None:
    png = Image.new("RGBA", (2, 2))
    theme = write_theme({"theme.xml": wrap(body)})
    png.save(theme.parent / "a.png")
    with pytest.raises(RenderError, match=match):
        render_grid_view(theme)


def test_runtime_include_is_skipped_not_failed(write_theme: ThemeWriter) -> None:
    theme = write_theme({"theme.xml": wrap("<include>./${screen.ratio}/x.xml</include>")})
    assert ThemeParser().load(theme).elements == {}


def test_compare_cli_prints_zone_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--actual", str(GOLDEN_REFERENCE), "--out-dir", str(tmp_path)]
    assert compare_cli.main([*args, "--min-similarity", "100"]) == 0  # boundary passes
    out = capsys.readouterr().out
    assert "overall: 100.00% (MAE=0.00)" in out
    assert "Center 5-Card Carousel" in out
    assert "FAILED" not in out


# ---------------------------------------------------------------- renderer branches
def _theme_with(write_theme: ThemeWriter, body: str) -> Path:
    theme = write_theme({"theme.xml": wrap(f"<view name='grid'>{body}</view>")})
    Image.new("RGBA", (40, 20), (255, 0, 0, 255)).save(theme.parent / "a.png")
    return theme


def test_max_size_image_keeps_its_aspect_ratio(write_theme: ThemeWriter) -> None:
    body = (
        "<image name='i'><path>./a.png</path><pos>0 0</pos>"
        "<maxSize>0.1 0.5</maxSize><zIndex>50</zIndex></image>"
    )
    clean = render_grid_view(_theme_with(write_theme, body)).clean
    # 40x20 fitted into 128x360 -> 128x64, drawn at the top-left corner.
    assert clean.getpixel((127, 63)) == (255, 0, 0)
    assert clean.getpixel((128, 0)) != (255, 0, 0)
    assert clean.getpixel((0, 64)) != (255, 0, 0)


def test_invisible_and_empty_elements_draw_nothing(write_theme: ThemeWriter) -> None:
    plain = render_grid_view(_theme_with(write_theme, "")).clean
    body = (
        "<image name='i'><path>./a.png</path><size>1 1</size><visible>false</visible></image>"
        "<image name='md_image'><path>./a.png</path><size>1 1</size></image>"
        "<image name='nopath'><size>1 1</size></image>"
        "<text name='empty'><text></text></text>"
    )
    assert render_grid_view(_theme_with(write_theme, body)).clean.tobytes() == plain.tobytes()


def test_deeply_nested_variables_stop_after_five_rounds() -> None:
    chain = {f"v{i}": f"${{v{i + 1}}}" for i in range(7)}
    assert expand_vars("${v0}", chain) == "${v5}"


def test_an_unmodelled_game_binding_fails_loud(write_theme: ThemeWriter) -> None:
    """The preview knows the mock games have no cartridge; any other bound field it can't fake."""
    theme = write_theme(
        {
            "theme.xml": wrap(
                "<view name='grid'><image name='x'><path>{game:fanart}</path></image></view>"
            )
        }
    )
    with pytest.raises(RenderError, match=r"binding \{game:fanart\} isn't modelled"):
        render_grid_view(theme)
    empty = write_theme(
        {
            "theme.xml": wrap(
                "<view name='grid'><image name='y'><path>{game:cartridge}</path></image></view>"
            )
        }
    )
    render_grid_view(empty)  # the engine draws nothing for a game without a cartridge


# ---------------------------------------------------------------- AC-T08/T09: details and suspend
_SELECTED = MOCK_GAMES[SELECTED_INDEX]


def test_bound_text_shows_the_selected_games_field(write_theme: ThemeWriter) -> None:
    body = (
        "<text name='p' extra='true'><text>{game:players}</text><pos>0 0</pos>"
        "<size>0.3 0.1</size><color>FFFFFFFF</color><zIndex>50</zIndex></text>"
    )
    plain = render_grid_view(_theme_with(write_theme, "")).clean
    drawn = render_grid_view(_theme_with(write_theme, body)).clean
    assert drawn.tobytes() != plain.tobytes()
    assert drawn.crop((0, 0, 384, 72)).tobytes() != plain.crop((0, 0, 384, 72)).tobytes()
    assert (
        drawn.crop((0, 72, SCREEN_W, SCREEN_H)).tobytes()
        == plain.crop((0, 72, SCREEN_W, SCREEN_H)).tobytes()
    )  # drawn in its own box, not on the title plaque


def test_a_detail_bound_to_an_empty_field_is_hidden_with_its_label() -> None:
    blank = MockGame("x", "X", "", "Nintendo", (0, 0, 0))
    spec = ElementSpec("text", "l", "grid", {"visible": '{game:players} != ""'})
    assert not is_visible(spec, blank)
    assert is_visible(spec, _SELECTED)
    assert is_visible(ElementSpec("text", "n", "grid", {}), blank)
    assert not is_visible(ElementSpec("text", "f", "grid", {"visible": "false"}), blank)


def test_an_unmodelled_visible_expression_fails_loud() -> None:
    spec = ElementSpec("text", "l", "grid", {"visible": "{game:players} == '4'"})
    with pytest.raises(RenderError, match=r"visible=.* isn't modelled"):
        is_visible(spec, _SELECTED)
    with pytest.raises(RenderError, match=r"binding \{game:genre\} isn't modelled"):
        game_field(_SELECTED, "genre")
    assert game_field(_SELECTED, "releaseYear") == "1996"
    assert game_field(_SELECTED, "cartridge") == ""


def test_the_suspend_pill_follows_the_selected_game(
    write_theme: ThemeWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """md_savestate shows only for a game with suspend points, md_nosavestate only without."""
    body = (
        "<image name='md_savestate'><path>./a.png</path><pos>0 0</pos><size>0.1 0.1</size>"
        "<zIndex>50</zIndex></image>"
        "<image name='md_nosavestate'><path>./a.png</path><pos>0.5 0.5</pos>"
        "<size>0.1 0.1</size><zIndex>50</zIndex></image>"
    )
    theme = _theme_with(write_theme, body)
    with_states = render_grid_view(theme).clean
    assert _SELECTED.suspend_points
    assert with_states.getpixel((5, 5)) == (255, 0, 0)
    assert with_states.getpixel((645, 365)) != (255, 0, 0)
    games = list(MOCK_GAMES)
    games[SELECTED_INDEX] = MockGame("x", "X", "1P", "N", (0, 0, 0))
    monkeypatch.setattr("n64mini.harness.render.MOCK_GAMES", tuple(games))
    without = render_grid_view(theme).clean
    assert without.getpixel((5, 5)) != (255, 0, 0)
    assert without.getpixel((645, 365)) == (255, 0, 0)


# ---------------------------------------------------------------- AC-T19/T20: templates, bindings
@pytest.mark.parametrize(
    ("players", "count"),
    [("1-4", "4"), ("2+", "2"), ("1", "1"), ("", "1"), ("12", "9"), (" 3", "3"), ("-2", "2")],
)
def test_player_count_is_the_engines(players: str, count: str) -> None:
    """FileData::getProperty("playerCount"): after the last '+' or '-', else 1..9."""
    assert player_count(players) == count


def test_a_player_count_the_engine_would_garble_fails_loud() -> None:
    with pytest.raises(RenderError, match="isn't a number the preview models"):
        player_count("two")


def _fields(name: str) -> str:
    return {"rom": "x.z64", "players": "1-4", "image": "", "thumbnail": "/t.png"}[name]


@pytest.mark.parametrize(
    ("expr", "value"),
    [
        ('"/a/" + {game:rom} + ".png"', "/a/x.z64.png"),
        ('{game:players} != ""', True),
        ('{game:players} == ""', False),
        ("empty({game:image}) ? {game:thumbnail} : {game:image}", "/t.png"),
        ("empty({game:rom}) ? {game:thumbnail} : {game:rom}", "x.z64"),
        ("{game:rom}", "x.z64"),
        ('exists("/userdata/x.z64.st0")', True),
        ('exists("/userdata/" + {game:rom} + ".st1")', False),
    ],
)
def test_bound_expressions_evaluate_like_the_engine(
    tmp_path: Path, expr: str, value: object
) -> None:
    (tmp_path / "userdata").mkdir()
    (tmp_path / "userdata" / "x.z64.st0").write_bytes(b"")
    assert evaluate(expr, _fields, Device(tmp_path)) == value


@pytest.mark.parametrize(
    ("expr", "match"),
    [
        ("{game:rom} ? 1 : 2", "isn't modelled"),  # numbers aren't modelled
        ('{game:rom} ? "a" : "b"', "a condition must be true or false"),
        ('"a" + exists("/b")', "'\\+' joins text only"),
        ('exists(empty(""))', r"exists\(\) takes text"),
        ('"a" "b"', "trailing"),
        ('upper("a")', "isn't modelled \\('upper'\\)"),
        ('empty("a"', "expected"),
        ('exists("relative")', "isn't an absolute device path"),
    ],
)
def test_an_expression_the_preview_cannot_model_fails_loud(
    tmp_path: Path, expr: str, match: str
) -> None:
    with pytest.raises(RenderError, match=match):
        evaluate(expr, _fields, Device(tmp_path))


def test_bound_text_is_substituted_not_evaluated() -> None:
    assert substitute("{game:players}P + 1", _fields) == "1-4P + 1"


def test_exists_reads_the_sandbox_console_disk(tmp_path: Path) -> None:
    """AC-T09: the mock games' save states are real files on the sandbox disk."""
    device = build_device(tmp_path)
    mario = next(g for g in MOCK_GAMES if g.slug == "supermario64")
    assert mario.suspend_slots == (0, 1)
    for slot, saved in ((0, True), (1, True), (2, False)):
        expr = f'exists("/userdata/saves/n64/" + {{game:rom}} + ".st{slot}")'
        assert evaluate(expr, lambda f: game_field(mario, f), device) is saved
    strips = sorted(p.name for p in device.path("/userdata/system/n64mini/strip").iterdir())
    assert strips == sorted(f"{g.rom}.png" for g in MOCK_GAMES)  # drawn by the console's code


def test_the_mock_games_are_in_the_engines_order() -> None:
    """Engine fact 30: the carousel is sorted by name, so the preview's games are too."""
    names = [g.name for g in MOCK_GAMES]
    assert names == sorted(names, key=str.upper)


def _template_theme(write_theme: ThemeWriter, children: str, grid: str = "") -> Path:
    body = (
        "<imagegrid name='gamegrid'><pos>0 0</pos><size>1 0.3</size>"
        f"<autoLayout>5 1</autoLayout><zIndex>50</zIndex>{grid}"
        f"<itemTemplate>{children}</itemTemplate></imagegrid>"
    )
    return _theme_with(write_theme, body)


def _tile_centre(index: int) -> tuple[int, int]:
    return (round(SCREEN_W / 5 * (index + 0.5)), round(SCREEN_H * 0.15))


def test_the_template_draws_each_cards_own_game(write_theme: ThemeWriter) -> None:
    """AC-T19: each card's bindings are its own game's, not the selected one's."""
    theme = _template_theme(
        write_theme,
        "<image name='art'><path>{game:thumbnail}</path><maxSize>1 0.5</maxSize></image>",
    )
    clean = render_grid_view(theme).clean
    for i, game in enumerate(MOCK_GAMES):
        x, _ = _tile_centre(i)
        assert clean.getpixel((x, 20)) == game.color


def test_the_selected_card_plays_its_activate_storyboard(write_theme: ThemeWriter) -> None:
    """AC-T06: a child hidden by opacity 0 shows on the selected card only."""
    child = (
        "<image name='gold'><path>./a.png</path><opacity>0</opacity>"
        "<storyboard event='activate'><animation property='opacity' to='1'/></storyboard>"
        "<storyboard event='deactivate'><animation property='opacity' to='0'/></storyboard>"
        "</image>"
    )
    clean = render_grid_view(_template_theme(write_theme, child)).clean
    for i in range(5):
        red = clean.getpixel(_tile_centre(i)) == (255, 0, 0)
        assert red is (i == SELECTED_INDEX)


def test_half_opacity_and_a_tint_blend_the_child(write_theme: ThemeWriter) -> None:
    child = (
        "<image name='g'><path>./a.png</path><opacity>0.5</opacity><color>00FF00FF</color></image>"
    )
    plain = render_grid_view(_template_theme(write_theme, "")).clean
    clean = render_grid_view(_template_theme(write_theme, child)).clean
    under = plain.getpixel(_tile_centre(0))
    got = clean.getpixel(_tile_centre(0))
    assert isinstance(under, tuple)
    # Red art tinted 00FF00 is black (every channel multiplied), then half over the background.
    assert got == (round(under[0] / 2), round(under[1] / 2), round(under[2] / 2))


def test_template_text_shows_its_bound_value(write_theme: ThemeWriter) -> None:
    child = (
        "<text name='players'><text>{game:playerCount}P</text><size>1 1</size>"
        "<fontSize>0.05</fontSize><color>FFFFFFFF</color><alignment>center</alignment></text>"
    )
    plain = render_grid_view(_template_theme(write_theme, "")).clean
    drawn = render_grid_view(_template_theme(write_theme, child)).clean
    for i in range(5):
        box = (i * 256, 0, (i + 1) * 256, 216)
        assert drawn.crop(box).tobytes() != plain.crop(box).tobytes(), f"card {i} has no text"


@pytest.mark.parametrize(
    ("children", "grid", "match"),
    [
        ("<video name='v'/>", "", "template <video name='v'> isn't modelled"),
        (
            (
                "<image name='i'><path>./a.png</path><storyboard event='activate'>"
                "<animation property='x' to='1'/></storyboard></image>"
            ),
            "",
            "a storyboard animating 'x' isn't modelled",
        ),
        (
            "<image name='i'><path>./a.png</path><visible>{game:rom}</visible></image>",
            "",
            "isn't true/false",
        ),
        (
            "<image name='i'><path>exists(\"/x\" + {game:rom})</path></image>",
            "",
            "is true/false",
        ),
        ("<image name='i'><path>\"none\" + {game:rom}</path></image>", "", "image not found"),
        ("", "<autoLayoutSelectedZoom>1.2</autoLayoutSelectedZoom>", "SelectedZoom"),
    ],
)
def test_a_template_the_preview_cannot_model_fails_loud(
    write_theme: ThemeWriter, children: str, grid: str, match: str
) -> None:
    with pytest.raises(RenderError, match=match):
        render_grid_view(_template_theme(write_theme, children, grid))


def test_a_bound_path_that_is_empty_or_not_a_file_draws_or_fails(write_theme: ThemeWriter) -> None:
    empty = "<image name='i'><path>{game:image}</path></image>"
    plain = render_grid_view(_template_theme(write_theme, "")).clean
    assert render_grid_view(_template_theme(write_theme, empty)).clean.tobytes() == plain.tobytes()
    flat = _theme_with(
        write_theme, "<image name='i'><path>exists(\"/x\" + {game:rom})</path></image>"
    )
    with pytest.raises(RenderError, match="is true/false"):
        render_grid_view(flat)


def test_a_grid_without_a_template_draws_classic_tiles(write_theme: ThemeWriter) -> None:
    """The classic gridtile path stays modelled for a theme without an itemTemplate."""
    body = (
        "<imagegrid name='gamegrid'><pos>0 0</pos><size>1 0.3</size>"
        "<autoLayout>5 1</autoLayout><zIndex>50</zIndex></imagegrid>"
    )
    clean = render_grid_view(_theme_with(write_theme, body)).clean
    x, y = _tile_centre(0)
    assert clean.getpixel((x, y - 20)) != (18, 22, 34)  # the first game's art is drawn


def test_an_image_without_a_size_is_drawn_at_its_own(write_theme: ThemeWriter) -> None:
    body = "<image name='i'><path>./a.png</path><pos>0 0</pos><zIndex>50</zIndex></image>"
    clean = render_grid_view(_theme_with(write_theme, body)).clean
    assert clean.getpixel((39, 19)) == (255, 0, 0)  # 40x20, as the file is
    assert clean.getpixel((40, 0)) != (255, 0, 0)
