"""``n64mini-launch-animation``: the card flip that plays as a game launches (AC-T17).

On a real game launch (emulator or port), ``n64mini-launch.sh`` starts this in a detached
session on the console display (its errors in ``n64mini-launch-animation.log``) and waits
``0.9 s`` before ``exec``ing the game, so the animation:
1. Flips the selected centre card (``CARD_BOX``) horizontally from the game's box art
   (``front``) to the N64 boot graphic (``back``),
2. Expands the flipped card to full screen (``SCREEN_BOX``) with cubic ease-out while fading
   the carousel backdrop to black, and
3. Cross-fades into the full-screen N64 boot screen (``art/launch_splash.png``) and holds
   while the emulator starts up in the background.

Every frame is one draw list (``frame_ops``) that the console's SDL2 canvas and the tests'
Pillow canvas both execute. It's apart from ``n64mini.console.launch`` so that deciding a
launch never loads Pillow.
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, TypedDict

from PIL import Image

from n64mini.console.launch import LaunchError, rom_argument, rom_file_name, roms_dir_of
from n64mini.console.showcase import GAME_RE, element_text, entry_path

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

CARD_BOX: Final = (523, 170, 234, 268)
SCREEN_BOX: Final = (0, 0, 1280, 720)
DEFAULT_THEME_DIR: Final = Path("/userdata/themes/n64-classic-mini")
_SDL_INIT_VIDEO: Final = 0x00000020
_SDL_WINDOW_FLAGS: Final = 0x00001011  # FULLSCREEN_DESKTOP | BORDERLESS (emulator maps above)
_SDL_RENDERER_ACCELERATED: Final = 0x00000002
_SDL_PIXELFORMAT_ABGR8888: Final = 0x16762004  # RGBA byte order on little-endian
_SDL_TEXTUREACCESS_STATIC: Final = 0
_SDL_BLENDMODE_BLEND: Final = 1


class FlipFrame(TypedDict):
    """One keyframe of the centre card flipping, expanding and cross-fading (AC-T17)."""

    phase: str
    face: str
    x: int
    y: int
    w: int
    h: int
    alpha: int


@dataclass(frozen=True, slots=True)
class LaunchSurfaces:
    """RGBA surfaces for the card-flip, cubic ease-out expand and cross-fade splash."""

    bg: Image.Image
    front: Image.Image
    back: Image.Image
    splash: Image.Image


class _SdlRect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int), ("w", ctypes.c_int), ("h", ctypes.c_int)]


type Draw = tuple[str, tuple[int, int, int, int], int]  # surface, box, alpha


class _Canvas(Protocol):
    def clear(self) -> None: ...
    def draw(self, slot: str, box: tuple[int, int, int, int], alpha: int) -> None: ...
    def present(self, delay_ms: int) -> None: ...
    def close(self) -> None: ...


class _SdlCanvas:
    """Hardware-accelerated 1280x720 SDL2 canvas backed by ``libSDL2-2.0.so.0``.

    Every SDL object it creates is checked: a NULL window, renderer or texture raises
    ``LaunchError`` with ``SDL_GetError``'s reason, after freeing what was already made.
    """

    def __init__(self, surfaces: LaunchSurfaces, sdl: ctypes.CDLL) -> None:
        self._sdl = sdl
        self._sdl.SDL_CreateWindow.restype = ctypes.c_void_p
        self._sdl.SDL_CreateRenderer.restype = ctypes.c_void_p
        self._sdl.SDL_CreateTexture.restype = ctypes.c_void_p
        self._sdl.SDL_GetError.restype = ctypes.c_char_p
        self._win: ctypes.c_void_p | None = None
        self._ren: ctypes.c_void_p | None = None
        self._textures: dict[str, ctypes.c_void_p] = {}
        if int(self._sdl.SDL_Init(_SDL_INIT_VIDEO)) != 0:
            raise LaunchError(f"SDL_Init failed: {self._error()}")
        try:
            sw, sh = SCREEN_BOX[2], SCREEN_BOX[3]
            self._win = self._made(
                self._sdl.SDL_CreateWindow(b"n64mini-launch", 0, 0, sw, sh, _SDL_WINDOW_FLAGS),
                "SDL_CreateWindow",
            )
            self._ren = self._made(
                self._sdl.SDL_CreateRenderer(self._win, -1, _SDL_RENDERER_ACCELERATED),
                "SDL_CreateRenderer",
            )
            self._sdl.SDL_RenderSetLogicalSize(self._ren, sw, sh)
            for slot in ("bg", "front", "back", "splash"):
                self._textures[slot] = self._upload(getattr(surfaces, slot))
        except LaunchError:
            self.close()
            raise

    def _error(self) -> str:
        raw = self._sdl.SDL_GetError()
        return raw.decode(errors="replace") if isinstance(raw, bytes) and raw else "no reason given"

    def _made(self, ptr: int | None, what: str) -> ctypes.c_void_p:
        if not ptr:
            raise LaunchError(f"{what} failed: {self._error()}")
        return ctypes.c_void_p(ptr)

    def _upload(self, image: Image.Image) -> ctypes.c_void_p:
        w, h = image.size
        raw = image.tobytes("raw", "RGBA")
        tex = self._made(
            self._sdl.SDL_CreateTexture(
                self._ren, _SDL_PIXELFORMAT_ABGR8888, _SDL_TEXTUREACCESS_STATIC, w, h
            ),
            "SDL_CreateTexture",
        )
        self._sdl.SDL_UpdateTexture(tex, ctypes.c_void_p(0), raw, w * 4)
        self._sdl.SDL_SetTextureBlendMode(tex, _SDL_BLENDMODE_BLEND)
        return tex

    def clear(self) -> None:
        self._sdl.SDL_SetRenderDrawColor(self._ren, 0, 0, 0, 255)
        self._sdl.SDL_RenderClear(self._ren)

    def draw(self, slot: str, box: tuple[int, int, int, int], alpha: int) -> None:
        tex = self._textures[slot]
        self._sdl.SDL_SetTextureAlphaMod(tex, alpha)
        dst = _SdlRect(*box)
        self._sdl.SDL_RenderCopy(self._ren, tex, ctypes.c_void_p(0), ctypes.byref(dst))

    def present(self, delay_ms: int) -> None:
        self._sdl.SDL_RenderPresent(self._ren)
        self._sdl.SDL_Delay(delay_ms)

    def close(self) -> None:
        for tex in self._textures.values():
            self._sdl.SDL_DestroyTexture(tex)
        self._textures = {}
        if self._ren is not None:
            self._sdl.SDL_DestroyRenderer(self._ren)
        if self._win is not None:
            self._sdl.SDL_DestroyWindow(self._win)
        self._sdl.SDL_Quit()


class ImageCanvas:
    """The same canvas in Pillow: what the tests and previews see of each frame."""

    def __init__(self, surfaces: LaunchSurfaces) -> None:
        """Start with a black screen."""
        self._surfaces = surfaces
        self.image = Image.new("RGBA", SCREEN_BOX[2:], (0, 0, 0, 255))
        self.frames: list[tuple[Image.Image, int]] = []  # each presented frame and its delay

    def clear(self) -> None:
        """Black, as ``SDL_RenderClear`` with the draw colour 0 0 0 255."""
        self.image = Image.new("RGBA", SCREEN_BOX[2:], (0, 0, 0, 255))

    def draw(self, slot: str, box: tuple[int, int, int, int], alpha: int) -> None:
        """``SDL_RenderCopy`` with blending and an alpha mod: scaled, then composited."""
        x, y, w, h = box
        sprite = getattr(self._surfaces, slot).resize(
            (max(1, w), max(1, h)), Image.Resampling.BILINEAR
        )
        if alpha < 255:
            a = sprite.getchannel("A").point([v * alpha // 255 for v in range(256)])
            sprite.putalpha(a)
        self.image.alpha_composite(sprite, (x, y))

    def present(self, delay_ms: int) -> None:
        """Keep the frame, with how long it stays up."""
        self.frames.append((self.image.copy(), delay_ms))

    def close(self) -> None:
        """Nothing to free."""


def ease_out(t: float) -> float:
    """Cubic ease-out (1 - (1 - t)^3) on 0..1."""
    return 1.0 - (1.0 - t) ** 3


def flip_expand_frames(
    flip_steps: int = 16, expand_steps: int = 24, crossfade_steps: int = 20
) -> list[FlipFrame]:
    """Keyframes for flipping the centre card, expanding with ease-out, and cross-fading."""
    cx, cy, cw, ch = CARD_BOX
    sx, sy, sw, sh = SCREEN_BOX
    centre_x = cx + cw // 2
    frames: list[FlipFrame] = []
    for i in range(1, flip_steps + 1):
        t = i / flip_steps
        if t <= 0.5:
            face = "front"
            w = max(2, round(cw * abs(1.0 - 2.0 * t)))
        else:
            face = "back"
            w = max(2, round(cw * ease_out(2.0 * (t - 0.5))))
        frames.append(
            {
                "phase": "flip",
                "face": face,
                "x": centre_x - w // 2,
                "y": cy,
                "w": w,
                "h": ch,
                "alpha": round(255 * t),
            }
        )
    for i in range(1, expand_steps + 1):
        u = ease_out(i / expand_steps)
        frames.append(
            {
                "phase": "expand",
                "face": "back",
                "x": round(cx + (sx - cx) * u),
                "y": round(cy + (sy - cy) * u),
                "w": round(cw + (sw - cw) * u),
                "h": round(ch + (sh - ch) * u),
                "alpha": round(128 + 127 * u),
            }
        )
    for i in range(1, crossfade_steps + 1):
        u = ease_out(i / crossfade_steps)
        frames.append(
            {
                "phase": "crossfade",
                "face": "splash",
                "x": sx,
                "y": sy,
                "w": sw,
                "h": sh,
                "alpha": round(255 * u),
            }
        )
    return frames


def _load_rgba(
    path: Path, size: tuple[int, int], fallback: tuple[int, int, int, int]
) -> Image.Image:
    if path.is_file():
        with Image.open(path) as img:
            rgba = img.convert("RGBA")
            return rgba if rgba.size == size else rgba.resize(size, Image.Resampling.BILINEAR)
    return Image.new("RGBA", size, fallback)


def _resolve_gamelist_art(roms_dir: Path, rom_name: str, text: str) -> Path | None:
    for m in GAME_RE.finditer(text):
        block = m.group(0)
        if Path(entry_path(block)).name == rom_name:
            rel = element_text(block, "thumbnail") or element_text(block, "image")
            if rel:
                candidate = Path(rel) if rel.startswith("/") else roms_dir / rel.removeprefix("./")
                if candidate.is_file():
                    return candidate
    return None


def _game_art_path(rom_path: Path) -> Path | None:
    roms_dir = roms_dir_of(str(rom_path))
    rom_name = rom_file_name(str(rom_path))
    gamelist = roms_dir / "gamelist.xml"
    if gamelist.is_file():
        found = _resolve_gamelist_art(roms_dir, rom_name, gamelist.read_text(encoding="utf-8"))
        if found is not None:
            return found
    fallback = roms_dir / "images" / f"{Path(rom_name).stem}-thumb.png"
    return fallback if fallback.is_file() else None


def compose_launch_surfaces(rom_path: Path, theme_dir: Path = DEFAULT_THEME_DIR) -> LaunchSurfaces:
    """Build the background, card front, card back and full-screen boot graphic surfaces."""
    art_dir = theme_dir / "art"
    sw, sh = SCREEN_BOX[2], SCREEN_BOX[3]
    cw, ch = CARD_BOX[2], CARD_BOX[3]
    bg = _load_rgba(art_dir / "n64_classic_bg.png", (sw, sh), (18, 20, 26, 255))
    for path, pos, sz in (
        (art_dir / "brand.png", (40, 5), (1200, 82)),
        (art_dir / "console.png", (40, 635), (1200, 82)),
        (
            theme_dir.parent.parent
            / "system"
            / "n64mini"
            / "strip"
            / f"{rom_file_name(str(rom_path))}.png",
            (64, 462),
            (1152, 68),
        ),
    ):
        if path.is_file():
            bg.alpha_composite(_load_rgba(path, sz, (0, 0, 0, 0)), pos)
    splash = _load_rgba(art_dir / "launch_splash.png", (sw, sh), (0, 0, 0, 255))
    frame_front = _load_rgba(art_dir / "tile_selected.png", (cw, ch), (218, 174, 32, 255))
    art_path = _game_art_path(rom_path)
    box_art = _load_rgba(
        art_path if art_path is not None else art_dir / "__missing__.png",
        (201, 193),
        (40, 44, 56, 255),
    )
    front = frame_front.copy()
    front.alpha_composite(box_art, (16, 15))
    back = _load_rgba(art_dir / "tile_selected.png", (sw, sh), (218, 174, 32, 255))
    back_inner = splash.resize((sw - 176, sh - 152), Image.Resampling.BILINEAR)
    back.alpha_composite(back_inner, (88, 76))
    return LaunchSurfaces(bg=bg, front=front, back=back, splash=splash)


def frame_ops(frame: FlipFrame) -> list[Draw]:
    """What to draw for one keyframe, on a black screen; both canvases draw exactly this."""
    sw, cw = SCREEN_BOX[2], CARD_BOX[2]
    box = (frame["x"], frame["y"], frame["w"], frame["h"])
    if frame["phase"] == "flip":
        return [("bg", SCREEN_BOX, 255), (frame["face"], box, 255)]
    if frame["phase"] == "expand":
        bg_alpha = max(0, round(255 * (sw - frame["w"]) / (sw - cw)))
        return [*([("bg", SCREEN_BOX, bg_alpha)] if bg_alpha > 0 else []), ("back", box, 255)]
    if frame["phase"] == "crossfade":
        return [("back", SCREEN_BOX, 255), ("splash", SCREEN_BOX, frame["alpha"])]
    return [("splash", SCREEN_BOX, 255)]  # hold


HOLD: Final[FlipFrame] = {
    "phase": "hold",
    "face": "splash",
    "x": 0,
    "y": 0,
    "w": 1280,
    "h": 720,
    "alpha": 255,
}


def render_flip_frame(frame: FlipFrame, surfaces: LaunchSurfaces) -> Image.Image:
    """Render one keyframe onto a 1280x720 RGBA image, through the console's own draw list."""
    canvas = ImageCanvas(surfaces)
    for op in frame_ops(frame):
        canvas.draw(*op)
    return canvas.image


def _default_sdl_lib() -> ctypes.CDLL:
    return ctypes.CDLL("libSDL2-2.0.so.0")


def _default_canvas_factory(surfaces: LaunchSurfaces) -> _Canvas:
    return _SdlCanvas(surfaces, _default_sdl_lib())


def play_sdl_animation(
    surfaces: LaunchSurfaces,
    frames: Sequence[FlipFrame],
    hold_steps: int = 150,
    frame_ms: int = 16,
    canvas_factory: Callable[[LaunchSurfaces], _Canvas] = _default_canvas_factory,
) -> bool:
    """Render the card-flip, ease-out expansion and cross-fade frames onto an SDL2 canvas.

    Returns False, with the reason on stderr (the animation's log), if SDL can't start.
    """
    try:
        canvas = canvas_factory(surfaces)
    except (LaunchError, OSError) as exc:
        sys.stderr.write(f"n64mini-launch: no animation: {exc}\n")
        return False
    for frame in [*frames, *[HOLD] * hold_steps]:
        canvas.clear()
        for op in frame_ops(frame):
            canvas.draw(*op)
        canvas.present(frame_ms)
    canvas.close()
    return True


def play_launch_animation(
    rom_path: Path,
    theme_dir: Path = DEFAULT_THEME_DIR,
    canvas_factory: Callable[[LaunchSurfaces], _Canvas] = _default_canvas_factory,
) -> bool:
    """Compose the game's card and play the flip, ease-out expand and cross-fade splash."""
    surfaces = compose_launch_surfaces(rom_path, theme_dir)
    return play_sdl_animation(surfaces, flip_expand_frames(), canvas_factory=canvas_factory)


def main(argv: list[str] | None = None) -> int:
    """Play the animation for the game in Batocera's launch arguments (or a bare ROM path)."""
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        rom = rom_argument(args) if "-rom" in args else args[0]
    except (LaunchError, IndexError):
        sys.stderr.write("usage: n64mini-launch-animation (-rom ROM ... | ROM)\n")
        return 2
    return 0 if play_launch_animation(Path(rom)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
