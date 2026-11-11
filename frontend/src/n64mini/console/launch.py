"""``n64mini-launch``: choose between the port and the emulator for an N64 launch (AC-T15, AC-T17).

Batocera runs ``n64mini-launch.sh`` instead of ``emulatorlauncher`` for N64
(``es_systems_n64.cfg``), with the same arguments. The script asks this module what to
run (the command comes back NUL-separated) and ``exec``s it.

On a real game launch (emulator or port), ``n64mini-launch.sh`` starts ``--animate`` in a
detached session on the console display and delays ``0.75 s`` before ``exec``ing the game so
the animation:
1. Flips the selected centre card (``CARD_BOX``) horizontally from the game's box art
   (``front``) to the N64 boot graphic (``back``),
2. Expands the flipped card to full screen (``SCREEN_BOX``) with cubic ease-out while fading
   the carousel backdrop to black, and
3. Cross-fades into the full-screen N64 boot screen (``art/launch_splash.png``) and holds
   while the emulator starts up in the background.

A game flipped to Port (``modes.json``, written by ``n64mini-flip``) runs its port, as
configured in ``ports.json``. Everything else is handed to ``emulatorlauncher``
unchanged, so normal games start exactly as before.

A game flipped to Port whose port isn't installed doesn't fall back to the emulator: the owner
chose Port, so a silent switch would hide the problem. The reason goes on screen (the engine's
notification) and the launch exits non-zero.

The Showcase/Full card (``showcase.CARD``) isn't a game: launching it switches the list (AC-T14).
"""

from __future__ import annotations

import ctypes
import json
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Final, Protocol, TypedDict, cast

from PIL import Image

from n64mini.console.engine import Engine, EngineError
from n64mini.console.flip import PORTS_FILE, FlipError, load_modes, load_ports
from n64mini.console.reset import NEXT_WRITE_SLOT
from n64mini.console.showcase import (
    CARD,
    CATALOG,
    GAME_RE,
    ShowcaseError,
    element_text,
    entry_path,
    load_catalog,
)
from n64mini.console.suspend import RESUME_FILE

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

EMULATORLAUNCHER: Final = "emulatorlauncher"
CARD_BOX: Final = (523, 170, 234, 268)
SCREEN_BOX: Final = (0, 0, 1280, 720)
DEFAULT_THEME_DIR: Final = Path("/userdata/themes/n64-classic-mini")
_SDL_INIT_VIDEO: Final = 0x00000020
_SDL_WINDOW_FLAGS: Final = 0x00001011  # FULLSCREEN_DESKTOP | BORDERLESS (emulator maps above)
_SDL_RENDERER_ACCELERATED: Final = 0x00000002
_SDL_PIXELFORMAT_ABGR8888: Final = 0x16762004  # RGBA byte order on little-endian
_SDL_TEXTUREACCESS_STATIC: Final = 0
_SDL_BLENDMODE_BLEND: Final = 1


class LaunchError(Exception):
    """The game can't be launched as chosen."""


class Decision(TypedDict):
    """What the wrapper would run, and why."""

    mode: str
    rom: str
    command: list[str]


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


class _Canvas(Protocol):
    def clear(self) -> None: ...
    def draw(self, slot: str, box: tuple[int, int, int, int], alpha: int) -> None: ...
    def present(self, delay_ms: int) -> None: ...
    def close(self) -> None: ...


class _SdlCanvas:
    """Hardware-accelerated 1280x720 SDL2 canvas backed by ``libSDL2-2.0.so.0``."""

    def __init__(self, surfaces: LaunchSurfaces, sdl: ctypes.CDLL) -> None:
        self._sdl = sdl
        self._sdl.SDL_CreateWindow.restype = ctypes.c_void_p
        self._sdl.SDL_CreateRenderer.restype = ctypes.c_void_p
        self._sdl.SDL_CreateTexture.restype = ctypes.c_void_p
        if int(self._sdl.SDL_Init(_SDL_INIT_VIDEO)) != 0:
            raise LaunchError("SDL_Init failed")
        sw, sh = SCREEN_BOX[2], SCREEN_BOX[3]
        win_ptr = self._sdl.SDL_CreateWindow(b"n64mini-launch", 0, 0, sw, sh, _SDL_WINDOW_FLAGS)
        self._win = ctypes.c_void_p(win_ptr)
        ren_ptr = self._sdl.SDL_CreateRenderer(self._win, -1, _SDL_RENDERER_ACCELERATED)
        self._ren = ctypes.c_void_p(ren_ptr)
        self._sdl.SDL_RenderSetLogicalSize(self._ren, sw, sh)
        self._textures = {
            "bg": self._upload(surfaces.bg),
            "front": self._upload(surfaces.front),
            "back": self._upload(surfaces.back),
            "splash": self._upload(surfaces.splash),
        }

    def _upload(self, image: Image.Image) -> ctypes.c_void_p:
        w, h = image.size
        raw = image.tobytes("raw", "RGBA")
        ptr = self._sdl.SDL_CreateTexture(
            self._ren, _SDL_PIXELFORMAT_ABGR8888, _SDL_TEXTUREACCESS_STATIC, w, h
        )
        tex = ctypes.c_void_p(ptr)
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
        self._sdl.SDL_DestroyRenderer(self._ren)
        self._sdl.SDL_DestroyWindow(self._win)
        self._sdl.SDL_Quit()


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


def render_flip_frame(frame: FlipFrame, surfaces: LaunchSurfaces) -> Image.Image:
    """Render one keyframe of the launch animation onto a 1280x720 RGBA canvas."""
    sw, sh = SCREEN_BOX[2], SCREEN_BOX[3]
    cw = CARD_BOX[2]
    if frame["phase"] == "crossfade":
        return Image.blend(surfaces.back, surfaces.splash, frame["alpha"] / 255.0)
    canvas = Image.new("RGBA", (sw, sh), (0, 0, 0, 255))
    if frame["phase"] == "flip":
        canvas.alpha_composite(surfaces.bg)
        source = surfaces.front if frame["face"] == "front" else surfaces.back
    else:
        bg_weight = max(0.0, (sw - frame["w"]) / (sw - cw))
        if bg_weight > 0.0:
            canvas = Image.blend(canvas, surfaces.bg, bg_weight)
        source = surfaces.back
    scaled = source.resize((max(1, frame["w"]), max(1, frame["h"])), Image.Resampling.BILINEAR)
    canvas.alpha_composite(scaled, (frame["x"], frame["y"]))
    return canvas


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
    """Render the card-flip, ease-out expansion and cross-fade frames onto an SDL2 canvas."""
    try:
        canvas = canvas_factory(surfaces)
    except (LaunchError, OSError):
        return False
    sw = SCREEN_BOX[2]
    cw = CARD_BOX[2]
    for frame in frames:
        canvas.clear()
        box = (frame["x"], frame["y"], frame["w"], frame["h"])
        if frame["phase"] == "flip":
            canvas.draw("bg", SCREEN_BOX, 255)
            canvas.draw(frame["face"], box, 255)
        elif frame["phase"] == "expand":
            bg_alpha = max(0, round(255 * (sw - frame["w"]) / (sw - cw)))
            if bg_alpha > 0:
                canvas.draw("bg", SCREEN_BOX, bg_alpha)
            canvas.draw("back", box, 255)
        else:
            canvas.draw("back", SCREEN_BOX, 255)
            canvas.draw("splash", SCREEN_BOX, frame["alpha"])
        canvas.present(frame_ms)
    for _ in range(hold_steps):
        canvas.clear()
        canvas.draw("splash", SCREEN_BOX, 255)
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


def rom_argument(args: list[str]) -> str:
    """The value of ``-rom`` in Batocera's launch arguments."""
    try:
        return args[args.index("-rom") + 1]
    except (ValueError, IndexError):
        raise LaunchError("no -rom in the launch arguments") from None


def rom_file_name(rom: str) -> str:
    """The ROM's file name, from a POSIX or (in the engine harness) Windows path."""
    # The Windows engine harness passes Windows paths; the console passes POSIX ones.
    return PureWindowsPath(rom).name if "\\" in rom else Path(rom).name


def consume_resume_args(rom_name: str, resume_file: Path = RESUME_FILE) -> list[str]:
    """Consume a one-shot ``Suspend Point List`` resume request for ``rom_name`` if present.

    Batocera 43.1's ``emulatorlauncher`` parses ``-state_slot`` and ``-state_filename``:
    ``mupenGenerator.py`` passes ``--savestate <state_filename>`` to ``mupen64plus`` while
    ``mupenConfig.py`` writes ``CurrentStateSlot = <state_slot>`` into ``mupen64plus.cfg``.
    Pinning ``-state_slot 8`` (``NEXT_WRITE_SLOT``) ensures a subsequent Reset press increments
    ``8 -> 9`` rather than overwriting any saved ``.st0..3`` slot.
    """
    if not resume_file.is_file():
        return []
    try:
        raw: object = json.loads(resume_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        resume_file.unlink(missing_ok=True)
        return []
    resume_file.unlink(missing_ok=True)
    if not isinstance(raw, dict):
        return []
    entries = cast(
        "dict[str, object]", raw
    )  # typeshed json.loads returns Any -> dict[Unknown, Unknown]
    if entries.get("rom") != rom_name:
        return []
    state_file = entries.get("state_file")
    if not isinstance(state_file, str) or not Path(state_file).is_file():
        return []
    return ["-state_slot", str(NEXT_WRITE_SLOT), "-state_filename", state_file]


def decide(
    args: list[str],
    roms_dir: Path,
    ports_file: Path,
    catalog: Path = CATALOG,
    *,
    resume_file: Path = RESUME_FILE,
) -> Decision:
    """Choose between the port and the emulator for Batocera's launch ``args``."""
    rom = rom_argument(args)
    name = rom_file_name(rom)
    if name == CARD:
        # Detached, so the launch ends at once and the engine saves its gamelist; the toggle
        # waits for that save, then switches (showcase.wait_for_launch_to_end).
        toggle = ["toggle", "--after-game", "--reload", "--roms", str(roms_dir)]
        detached = ["setsid", "-f", sys.executable, "-m", "n64mini.console.library", *toggle]
        return {"mode": "library", "rom": name, "command": detached}
    if load_modes(roms_dir).get(name) != "port":
        extra = [] if "-state_filename" in args else consume_resume_args(name, resume_file)
        return {"mode": "emulator", "rom": name, "command": [EMULATORLAUNCHER, *args, *extra]}
    game = next((g for g in load_catalog(catalog) if g.rom.search(name)), None)
    if game is None:
        raise LaunchError(f"{name} is flipped to Port but isn't in the catalogue")
    try:
        command = load_ports(ports_file).get(game.title)
    except FlipError as exc:
        raise LaunchError(str(exc)) from exc
    if command is None:
        raise LaunchError(
            f"{game.title}: its PC port isn't installed. Flip the card back to N64, "
            "or update the console from the stick."
        )
    return {"mode": "port", "rom": name, "command": [a.replace("{rom}", rom) for a in command]}


def roms_dir_of(rom: str) -> Path:
    """The folder holding the ROM, as this process can open it."""
    if "\\" not in rom:
        return Path(rom).parent
    # Under WSL, a Windows path like C:\Users\x\roms\n64\game.z64 is /mnt/c/Users/x/roms/n64.
    win = PureWindowsPath(rom).parent
    drive = win.drive.rstrip(":").lower()
    return Path("/mnt", drive, *win.parts[1:])


def main(argv: list[str] | None = None) -> int:
    """Print the command to run, NUL-separated.

    ``--dry-run FILE`` records the decision instead; ``--ports FILE`` reads installed ports there;
    ``--animate ...`` plays the card-flip, cubic ease-out expand and cross-fade splash.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--animate"]:
        try:
            rom_arg = rom_argument(args[1:]) if "-rom" in args[1:] else args[1]
        except (LaunchError, IndexError):
            return 1
        return 0 if play_launch_animation(Path(rom_arg)) else 1
    record: Path | None = None
    ports = PORTS_FILE
    while args[:1] in (["--dry-run"], ["--ports"]):  # our options come before Batocera's
        if args[0] == "--dry-run":
            record = Path(args[1])
        else:
            ports = Path(args[1])
        args = args[2:]
    try:
        rom = rom_argument(args)
        decision: dict[str, object] = {**decide(args, roms_dir_of(rom), ports)}
        failed = False
    except (LaunchError, ShowcaseError, OSError, ValueError) as exc:
        print(f"n64mini-launch: {exc}", file=sys.stderr)
        failure: dict[str, object] = {"mode": "error", "error": str(exc), "args": args}
        decision, failed = failure, True
        if record is None:
            _show(str(exc))
    if record is not None:  # the engine harness: record the choice (or the failure) instead
        with record.open("a", encoding="utf-8") as out:
            out.write(json.dumps(decision) + "\n")
    if failed or record is not None:
        return 1 if failed else 0
    sys.stdout.write("\0".join(cast("list[str]", decision["command"])) + "\0")
    return 0


def _show(message: str) -> None:
    """Put a failed launch's reason on screen; stderr (the launch log) already has it.

    With no engine to answer there's no screen to show it on, so the log is the record.
    """
    try:
        Engine().notify(message)
    except (EngineError, OSError) as exc:
        print(f"n64mini-launch: couldn't show it on screen: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
