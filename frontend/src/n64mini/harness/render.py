"""Tier-1 headless preview of the theme's game list (``gamecarousel`` or ``grid`` view) at 1280x720.

This is an approximation for fast iteration, not an engine. It reads element
properties from the theme XML (following includes and ``${variables}``). The
cards are laid out with the engine's own maths for the view (``gamecarousel.py`` for the
horizontal carousel, ``carousel.py`` for the grid) and filled with generated placeholder box art
standing in for the user's gamelist.
Text placement is still approximate. Only ``image`` and ``text`` elements and the carousel are
drawn; other element types (the ``helpsystem`` help bar, for one) don't appear in the preview at
all. Check those, and anything else that matters, on the real engine with ``./run shot``.
"""

from __future__ import annotations

import functools
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

from lxml import etree
from PIL import Image, ImageChops, ImageDraw, ImageFont

from n64mini.console import strip
from n64mini.harness import gamecarousel
from n64mini.harness.bindings import Device, evaluate, player_count
from n64mini.harness.carousel import Layout, TileMode, draw_carousel, layout
from n64mini.harness.template import Card, TemplateChild, draw_card
from n64mini.harness.values import RGBA, RenderError, parse_hex_color, parse_pair
from n64mini.theme_assets.fonts import Face, font

SCREEN_W: Final = 1280
SCREEN_H: Final = 720

type BoxKind = Literal["image", "grid", "text"]
type Box = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class MockGame:
    """Placeholder gamelist entry used to fill the carousel."""

    slug: str  # the ROM and image file name in the engine sandbox's gamelist
    name: str
    players: str  # as a scraper writes it: "1", "1-4"
    publisher: str
    color: tuple[int, int, int]
    year: str = ""  # {game:releaseYear}
    suspend_slots: tuple[int, ...] = ()  # the save-state slots it has (<rom>.st<slot>)

    @property
    def rom(self) -> str:
        """``{game:rom}``: its ROM's file name."""
        return f"{self.slug}.z64"

    @property
    def suspend_points(self) -> bool:
        """What ``md_savestate`` / ``md_nosavestate`` show for."""
        return bool(self.suspend_slots)


# In the engine's order: sorted by name (engine fact 30).
MOCK_GAMES: Final = (
    MockGame("mariokart64", "Mario Kart 64", "1-4", "Nintendo", (196, 40, 40)),
    MockGame("starfox64", "Star Fox 64", "1-4", "Nintendo", (60, 70, 92)),
    MockGame(
        "supermario64",
        "Super Mario 64",
        "1",
        "Nintendo",
        (40, 84, 196),
        "1996",
        suspend_slots=(0, 1),
    ),
    MockGame("zelda64", "The Legend of Zelda", "1", "Nintendo", (150, 120, 40), suspend_slots=(0,)),
    MockGame("waverace64", "Wave Race 64", "1-2", "Nintendo", (24, 96, 176)),
)
SELECTED_INDEX: Final = 2
# N64 box fronts are landscape, about 1.38:1 (the PAL/NTSC-U box).
_BOX_ART_SIZE: Final = (276, 200)
_PATH_PROPS: Final = frozenset({"path", "default", "fontPath", "backgroundImage"})
_PLACEHOLDER_RE: Final = re.compile(r"\$\{([^}]+)\}")
_GAME_BINDING_RE: Final = re.compile(r"\{game:(\w+)\}")
# Fields the preview's mock games leave empty: AC-T15's flip badge is only set on ported games,
# and multidisk holds the live top-bar overlay path on the console.
_EMPTY_FOR_MOCK_GAMES: Final = frozenset({"cartridge", "multidisk"})
# A bound visible the preview can evaluate: the field isn't empty (BindingManager.cpp evaluates
# it as an expression). Anything else raises rather than guessing.
_NOT_EMPTY_RE: Final = re.compile(r'\{game:(\w+)\}\s*!=\s*""')


@dataclass(slots=True)
class ElementSpec:
    """One theme element and its resolved properties."""

    type: str
    name: str
    view: str
    props: dict[str, str] = field(default_factory=dict[str, str])


@dataclass(slots=True)
class ParsedTheme:
    """Everything the renderer needs from a theme."""

    elements: dict[tuple[str, str, str], ElementSpec] = field(
        default_factory=dict[tuple[str, str, str], ElementSpec]
    )
    grid_props: dict[str, str] = field(default_factory=dict[str, str])
    gridtiles: dict[TileMode, dict[str, str]] = field(
        default_factory=lambda: {"default": {}, "selected": {}}
    )
    template: list[TemplateChild] | None = None  # the grid's itemTemplate, if it has one
    default_view: str | None = None  # the theme's defaultView (ThemeData::parseTheme)
    carousel_props: dict[str, str] = field(default_factory=dict[str, str])
    carousel_template: list[TemplateChild] | None = None  # the gamecarousel's itemTemplate
    carousel_opacity_boards: bool = False  # a card animates opacity: the engine won't fade cards


@dataclass(frozen=True, slots=True)
class WireBox:
    """A wireframe overlay rectangle."""

    kind: BoxKind
    label: str
    box: Box


@dataclass(frozen=True, slots=True)
class Render:
    """Clean render plus the same image with wireframe overlays."""

    clean: Image.Image
    wireframe: Image.Image


def expand_vars(text: str, variables: dict[str, str]) -> str:
    """Substitute ``${name}`` placeholders, allowing nested variables."""
    cur = text
    for _ in range(5):  # nested variables, bounded
        nxt = _PLACEHOLDER_RE.sub(lambda m: variables.get(m.group(1), m.group(0)), cur)
        if nxt == cur:
            break
        cur = nxt
    return cur


def _is_bound(value: str) -> bool:
    """A path the engine fills in at runtime: a variable, or a binding (any ``{``)."""
    return value.startswith("$") or "{" in value


class ThemeParser:
    """Collects element properties from a theme, last definition wins."""

    def __init__(self, system: str = "n64") -> None:
        self.variables: dict[str, str] = {
            "system.theme": system,
            "system.name": system,
            "system.fullName": "Nintendo 64",
        }
        self.theme = ParsedTheme()

    def load(self, theme_file: Path) -> ParsedTheme:
        """Parse ``theme_file`` and everything it includes."""
        if not theme_file.is_file():
            raise FileNotFoundError(f"theme XML not found: {theme_file}")
        root = etree.parse(str(theme_file), etree.XMLParser(remove_comments=True)).getroot()
        self.theme.default_view = root.get("defaultView")
        self._parse_file(theme_file)
        return self.theme

    def _text(self, node: etree._Element) -> str:
        return expand_vars((node.text or "").strip(), self.variables)

    def _parse_file(self, path: Path) -> None:
        root = etree.parse(str(path), etree.XMLParser(remove_comments=True)).getroot()
        for child in root.iterchildren("*"):
            if child.tag == "variables":
                for var in child.iterchildren("*"):
                    self.variables[var.tag] = self._text(var)
            elif child.tag == "include":
                rel = self._text(child)
                if not rel or "$" in rel:
                    continue  # runtime-only variable: resolved on the device, not here
                target = path.parent / rel
                if not target.is_file():
                    raise RenderError(f"{path.name}: included file not found: {target}")
                self._parse_file(target)
            elif child.tag == "view":
                self._parse_view(child, path.parent)
            elif child.tag == "feature":
                for view in child.iterchildren("view"):
                    self._parse_view(view, path.parent)

    def _props(self, elem: etree._Element, base: Path) -> dict[str, str]:
        props: dict[str, str] = {}
        for prop in elem.iterchildren("*"):
            value = self._text(prop)
            if prop.tag in {"storyboard", "itemTemplate"}:
                continue  # not properties: a template and its animations, read separately
            if prop.tag in _PATH_PROPS and value and not _is_bound(value):
                value = str(base / value)
            props[prop.tag] = value
        return props

    def _template(self, template: etree._Element, base: Path) -> list[TemplateChild]:
        children: list[TemplateChild] = []
        for elem in template.iterchildren("*"):
            on_select: dict[str, str] = {}
            for board in elem.iterchildren("storyboard"):
                if board.get("event") != "activate":
                    continue  # only the selected card's end state is drawn
                for anim in board.iterchildren("animation"):
                    prop = str(anim.get("property", ""))
                    if prop != "opacity":
                        raise RenderError(f"a storyboard animating {prop!r} isn't modelled")
                    on_select[prop] = str(anim.get("to", ""))
            children.append(
                TemplateChild(
                    elem.tag, str(elem.get("name", "")), self._props(elem, base), on_select
                )
            )
        return children

    def _parse_view(self, view: etree._Element, base: Path) -> None:
        views = [v.strip() for v in str(view.get("name", "")).split(",") if v.strip()]
        is_grid = "grid" in views
        for elem in view.iterchildren("*"):
            name = str(elem.get("name", ""))
            if elem.tag == "gamecarousel" and "gamecarousel" in views:
                self.theme.carousel_props.update(self._props(elem, base))
                if (template := elem.find("itemTemplate")) is not None:
                    self.theme.carousel_template = self._template(template, base)
                    self.theme.carousel_opacity_boards = any(
                        anim.get("property") == "opacity" for anim in template.iter("animation")
                    )
            elif elem.tag == "imagegrid" and is_grid:
                self.theme.grid_props.update(self._props(elem, base))
                if (template := elem.find("itemTemplate")) is not None:
                    self.theme.template = self._template(template, base)
            elif elem.tag == "gridtile" and is_grid:
                mode: TileMode = "selected" if "selected" in name else "default"
                self.theme.gridtiles[mode].update(self._props(elem, base))
            else:
                props = self._props(elem, base)
                for v in views:
                    for single in (n.strip() for n in name.split(",") if n.strip()):
                        spec = self.theme.elements.setdefault(
                            (v, elem.tag, single), ElementSpec(elem.tag, single, v)
                        )
                        spec.props.update(props)


def placeholder_box_art(name: str, color: tuple[int, int, int]) -> Image.Image:
    """Placeholder box art: a flat panel in ``color`` with the title along the bottom.

    Stands in for the image a scraper puts in the gamelist. It's generated here, never
    shipped in the theme, and isn't derived from the golden reference or any real box art.
    """
    w, h = _BOX_ART_SIZE
    art = Image.new("RGBA", (w, h), (*color, 255))
    draw = ImageDraw.Draw(art)
    draw.rectangle((0, h - 34, w, h), fill=(16, 18, 26, 255))
    label = name.upper()
    size = 20
    title = font(Face.SANS_BOLD, size)
    while draw.textlength(label, font=title) > w - 12 and size > 9:  # 9pt: still legible
        size -= 1
        title = font(Face.SANS_BOLD, size)
    while draw.textlength(label, font=title) > w - 12:  # still too long at 9pt: shorten it
        label = label[:-2].rstrip() + "…"
    width = draw.textlength(label, font=title)
    draw.text(((w - width) // 2, h - 28), label, font=title, fill=(240, 240, 240))
    return art


def mock_box_art(game: MockGame) -> Image.Image:
    """Placeholder box art for one of the preview's mock games."""
    return placeholder_box_art(game.name, game.color)


def _load_font(path: str | None, size: int) -> ImageFont.FreeTypeFont:
    if not path:
        return font(Face.SANS_BOLD, size)  # no fontPath: the engine's default font
    if not Path(path).is_file():
        raise RenderError(f"fontPath not found: {path}")
    return ImageFont.truetype(path, max(8, size))


def game_field(game: MockGame, field: str) -> str:
    """A ``{game:<field>}`` value for a mock game; RenderError for a field not modelled."""
    values = {
        "name": game.name,
        "players": game.players,
        "playerCount": player_count(game.players),
        "publisher": game.publisher,
        "releaseYear": game.year,
        "rom": game.rom,
        "thumbnail": f"{MOCK_ROMS}/media/{game.slug}.png",
        "image": "",
    }
    if field in _EMPTY_FOR_MOCK_GAMES:
        return ""
    if field not in values:
        raise RenderError(f"binding {{game:{field}}} isn't modelled")
    return values[field]


MOCK_ROMS: Final = "/userdata/roms/n64"
MOCK_STRIPS: Final = "/userdata/system/n64mini/strip"
MOCK_SAVES: Final = "/userdata/saves/n64"


def build_device(root: Path) -> Device:
    """The mock games on a sandbox console disk: ROMs, box art, gamelist, saves and strips.

    The strips are drawn by the console's own code (``n64mini.console.strip``), from this
    gamelist, exactly as on the console.
    """
    device = Device(root)
    roms = device.path(MOCK_ROMS)
    (roms / "media").mkdir(parents=True)
    saves = device.path(MOCK_SAVES)
    saves.mkdir(parents=True)
    entries: list[str] = []
    for game in MOCK_GAMES:
        (roms / game.rom).write_bytes(b"")
        mock_box_art(game).save(roms / "media" / f"{game.slug}.png")
        entries.append(
            f"<game><path>./{game.rom}</path><name>{game.name}</name>"
            f"<thumbnail>./media/{game.slug}.png</thumbnail><players>{game.players}</players></game>"
        )
        for slot in game.suspend_slots:
            (saves / f"{game.rom}.st{slot}").write_bytes(b"")
    (roms / "gamelist.xml").write_text(f"<gameList>{''.join(entries)}</gameList>", encoding="utf-8")
    strip.generate(roms, device.path(MOCK_STRIPS), {".z64"})
    return device


def _bound_text(text: str, game: MockGame) -> str:
    return _GAME_BINDING_RE.sub(lambda m: game_field(game, m.group(1)), text)


def is_visible(spec: ElementSpec, game: MockGame) -> bool:
    """The element's ``visible``, a bound one evaluated for ``game`` (only "not empty")."""
    raw = spec.props.get("visible", "true")
    if "{" not in raw:
        return raw.lower() != "false"
    match = _NOT_EMPTY_RE.fullmatch(raw.strip())
    if match is None:
        raise RenderError(f"<{spec.type} name={spec.name!r}>: visible={raw!r} isn't modelled")
    return game_field(game, match.group(1)) != ""


def _draw_image(
    canvas: Image.Image, spec: ElementSpec, z: float, boxes: list[WireBox], device: Device
) -> None:
    props = spec.props
    src = props.get("path") or props.get("default")
    if spec.name == "md_image" or not src:
        return  # dynamic game art, or nothing to draw
    has_states = MOCK_GAMES[SELECTED_INDEX].suspend_points
    if (spec.name == "md_savestate" and not has_states) or (
        spec.name == "md_nosavestate" and has_states
    ):
        return  # the engine shows exactly one of the two (DetailedContainer.cpp)
    if _is_bound(src):
        value = evaluate(src, lambda f: game_field(MOCK_GAMES[SELECTED_INDEX], f), device)
        if isinstance(value, bool):
            raise RenderError(f"<image name={spec.name!r}>: path {src} is true/false")
        if not value:
            return  # the engine draws nothing for a game without this field
        src = str(device.path(value)) if value.startswith("/") else value
    if not Path(src).is_file():
        raise RenderError(f"<image name={spec.name!r}>: path not found: {src}")
    with Image.open(src) as f:
        sprite = f.convert("RGBA")

    px, py = parse_pair(props.get("pos"), (0.0, 0.0))
    ox, oy = parse_pair(props.get("origin"), (0.0, 0.0))
    if "size" in props:
        sw, sh = parse_pair(props["size"], (0.0, 0.0))
        tw = round(sw * SCREEN_W) if sw > 0 else sprite.width
        th = round(sh * SCREEN_H) if sh > 0 else sprite.height
        sprite = sprite.resize((max(1, tw), max(1, th)), Image.Resampling.NEAREST)
    elif "maxSize" in props:
        mw, mh = parse_pair(props["maxSize"], (1.0, 1.0))
        ratio = min(
            round(mw * SCREEN_W) / max(1, sprite.width),
            round(mh * SCREEN_H) / max(1, sprite.height),
        )
        size = (max(1, round(sprite.width * ratio)), max(1, round(sprite.height * ratio)))
        sprite = sprite.resize(size, Image.Resampling.LANCZOS)

    x = round(round(px * SCREEN_W) - ox * sprite.width)
    y = round(round(py * SCREEN_H) - oy * sprite.height)
    canvas.alpha_composite(sprite, (x, y))
    boxes.append(
        WireBox(
            "image", f"img:{spec.name} (z={int(z)})", (x, y, x + sprite.width, y + sprite.height)
        )
    )


def _draw_template_grid(canvas: Image.Image, theme: ParsedTheme, device: Device) -> Layout:
    """Each card drawn from the item template, clipped to the grid like the classic tiles."""
    if "autoLayoutSelectedZoom" in theme.grid_props:
        raise RenderError("autoLayoutSelectedZoom with an itemTemplate isn't modelled")
    template = theme.template or []
    lay = layout(theme.grid_props, canvas.size, SELECTED_INDEX)
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    for tile in lay.tiles:
        x0, y0, x1, y1 = tile.box
        game = MOCK_GAMES[tile.index]
        spec = Card((x1 - x0, y1 - y0), tile.selected, functools.partial(game_field, game))
        card = draw_card(template, spec, device, canvas.height)
        layer.alpha_composite(card, (x0, y0))
    clip = Image.new("L", canvas.size, 0)
    clip.paste(255, lay.grid_box)
    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), clip))
    canvas.alpha_composite(layer)
    return lay


def _draw_grid(
    canvas: Image.Image, theme: ParsedTheme, z: float, boxes: list[WireBox], device: Device
) -> None:
    if theme.template is not None:
        lay = _draw_template_grid(canvas, theme, device)
    else:
        arts = [mock_box_art(g) for g in MOCK_GAMES]
        lay = draw_carousel(canvas, theme.grid_props, theme.gridtiles, arts, SELECTED_INDEX)
    cols = len(lay.tiles)
    boxes.append(WireBox("grid", f"imagegrid:{cols}x1 (z={int(z)})", lay.grid_box))
    for tile in lay.tiles:
        mode: TileMode = "selected" if tile.selected else "default"
        boxes.append(WireBox("grid", f"tile[{tile.index}]:{mode}", tile.box))


def _draw_gamecarousel(
    canvas: Image.Image, theme: ParsedTheme, z: float, boxes: list[WireBox], device: Device
) -> None:
    """The horizontal carousel: each card drawn from the item template, clipped to the carousel."""
    if theme.carousel_template is None:
        raise RenderError("a gamecarousel without an itemTemplate isn't modelled")
    lay = gamecarousel.layout(
        theme.carousel_props,
        canvas.size,
        games=len(MOCK_GAMES),
        selected=SELECTED_INDEX,
        opacity_storyboards=theme.carousel_opacity_boards,
    )
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    for c in lay.cards:
        x0, y0, x1, y1 = c.box
        game = MOCK_GAMES[c.game]
        spec = Card((x1 - x0, y1 - y0), c.selected, functools.partial(game_field, game))
        card = draw_card(theme.carousel_template, spec, device, canvas.height)
        if c.opacity < 1.0:
            card.putalpha(card.getchannel("A").point([round(v * c.opacity) for v in range(256)]))
        _paste(layer, card, c.box)
    clip = Image.new("L", canvas.size, 0)
    clip.paste(255, lay.clip)
    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), clip))
    canvas.alpha_composite(layer)
    visible = [c for c in lay.cards if c.box[2] > lay.clip[0] and c.box[0] < lay.clip[2]]
    boxes.append(WireBox("grid", f"gamecarousel:{len(visible)} (z={int(z)})", lay.clip))
    boxes.extend(
        WireBox("grid", f"card[{c.game}]:{'selected' if c.selected else 'default'}", c.box)
        for c in visible
    )


def _paste(layer: Image.Image, card: Image.Image, box: Box) -> None:
    """Composite the part of a card that's on the canvas (the buffer cards lie off it)."""
    x0, y0 = box[0], box[1]
    left, top = max(0, x0), max(0, y0)
    right, bottom = min(layer.width, x0 + card.width), min(layer.height, y0 + card.height)
    if right <= left or bottom <= top:
        return
    layer.alpha_composite(card.crop((left - x0, top - y0, right - x0, bottom - y0)), (left, top))


def _draw_text(canvas: Image.Image, spec: ElementSpec, boxes: list[WireBox]) -> None:
    props = spec.props
    game = MOCK_GAMES[SELECTED_INDEX]
    text = {
        "md_name": game.name,
        "md_players": game.players,
        "md_publisher": f"1996 {game.publisher.upper()}",
    }.get(spec.name, _bound_text(props.get("text", ""), game))
    if not text:
        return
    if props.get("forceUppercase", "0") in {"1", "true"}:
        text = text.upper()

    px, py = parse_pair(props.get("pos"), (0.5, 0.1944))
    sw, sh = parse_pair(props.get("size"), (0.36, 0.055))
    ox, oy = parse_pair(props.get("origin"), (0.0, 0.0))  # the engine's default (GuiComponent)
    w, h = round(sw * SCREEN_W), round(sh * SCREEN_H)
    bx, by = round(round(px * SCREEN_W) - ox * w), round(round(py * SCREEN_H) - oy * h)

    size_norm = float(props.get("fontSize", "0.03472"))
    size_px = round(size_norm * SCREEN_H) if size_norm < 1.0 else int(size_norm)
    f = _load_font(props.get("fontPath"), size_px)
    color = parse_hex_color(props.get("color", "121620"))

    draw = ImageDraw.Draw(canvas)
    bbox = draw.textbbox((0, 0), text, font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    align = props.get("alignment", "left")
    tx = {"center": bx + (w - tw) // 2, "right": bx + w - tw}.get(align, bx)
    ty = by + (h - th) // 2 - bbox[1]  # vertically centred in its box, as the engine does
    shadow: RGBA = (140, 148, 165, 255) if sum(color[:3]) < 200 else (10, 12, 18, 220)
    draw.text((tx + 2, ty + 2), text, font=f, fill=shadow)
    draw.text((tx, ty), text, font=f, fill=color)
    boxes.append(WireBox("text", f"txt:{spec.name}", (bx, by, bx + w, by + h)))


_WIRE_COLORS: Final[dict[BoxKind, tuple[int, int, int]]] = {
    "image": (0, 230, 255),
    "grid": (255, 70, 220),
    "text": (255, 230, 40),
}


def _wireframe(clean: Image.Image, boxes: list[WireBox]) -> Image.Image:
    wire = clean.copy()
    draw = ImageDraw.Draw(wire)
    label_font = font(Face.SANS_BOLD, 11)
    for wb in boxes:
        x0, y0, x1, y1 = wb.box
        c = _WIRE_COLORS[wb.kind]
        draw.rectangle((x0, y0, x1, y1), outline=c, width=2)
        draw.rectangle(
            (x0, y0, min(x1, x0 + len(wb.label) * 7 + 6), min(SCREEN_H, y0 + 14)), fill=(10, 14, 22)
        )
        draw.text((x0 + 3, y0 + 1), wb.label, font=label_font, fill=c)
    return wire


_GAME_LISTS: Final = frozenset({"grid", "gamecarousel"})


def render_view(theme_file: Path, view: str | None = None, *, system: str = "n64") -> Render:
    """Render a game-list view of ``theme_file`` as clean and wireframe images.

    The view defaults to the theme's ``defaultView``, the one a console set to "automatic" opens.
    """
    theme = ThemeParser(system).load(theme_file)
    view = view or theme.default_view
    if view not in _GAME_LISTS:
        raise RenderError(f"the preview draws a grid or gamecarousel game list, not {view!r}")
    canvas = Image.new("RGBA", (SCREEN_W, SCREEN_H), (18, 22, 34, 255))
    boxes: list[WireBox] = []

    layers: list[tuple[float, ElementSpec | None]] = [
        (float(spec.props.get("zIndex", "10")), spec)
        for (v, _, _), spec in theme.elements.items()
        if v == view
    ]
    games = theme.grid_props if view == "grid" else theme.carousel_props
    if games:
        layers.append((float(games.get("zIndex", "25")), None))  # None = the game list
    layers.sort(key=lambda layer: layer[0])

    with tempfile.TemporaryDirectory(prefix="n64mini-device-") as tmp:
        device = build_device(Path(tmp))
        for z, spec in layers:
            if spec is None and view == "grid":
                _draw_grid(canvas, theme, z, boxes, device)
            elif spec is None:
                _draw_gamecarousel(canvas, theme, z, boxes, device)
            elif not is_visible(spec, MOCK_GAMES[SELECTED_INDEX]):
                continue
            elif spec.type == "image":
                _draw_image(canvas, spec, z, boxes, device)
            elif spec.type == "text":
                _draw_text(canvas, spec, boxes)

    clean = canvas.convert("RGB")
    return Render(clean, _wireframe(clean, boxes))


def render_grid_view(theme_file: Path, *, system: str = "n64") -> Render:
    """Render the ``grid`` view of ``theme_file``."""
    return render_view(theme_file, "grid", system=system)
