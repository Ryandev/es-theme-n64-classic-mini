"""``n64mini-topbar``: the daemon behind the carousel's top bar and its D-Pad shortcuts.

On the carousel, D-Pad Up opens the top bar's three buttons (Settings, Showcase/Full, music;
AC-T16, AC-T21): Left/Right move, A or Enter presses, Down or B closes. D-Pad Down, or a Reset
inside a game (``n64mini.console.reset`` writes ``suspend`` to the command FIFO), opens the
Suspend Point List drawer (AC-T13). Every other key passes straight through to EmulationStation.

The daemon grabs the keyboards so it sees each key first, re-sends the ones it doesn't use through
a virtual keyboard, and draws the highlighted pocket and the Showcase/Full toast in two small X11
windows over the engine. It replaces any daemon already running.

Runs on the console: X11 through ``ctypes``, input through Batocera's ``evdev``, Pillow for the
pocket and toast images.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import os
import select
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING, Final, Literal, Protocol

import evdev
from evdev import ecodes
from PIL import Image

from n64mini.console import library, showcase, strip
from n64mini.console.engine import Engine, EngineError
from n64mini.console.flip import SELECTED_FILE
from n64mini.console.showcase import GAME_RE, element_text, entry_path
from n64mini.console.suspend import XImage

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

USERDATA: Final = Path("/userdata")
CMD_FIFO: Final = Path("/var/run/n64mini-topbar.cmd")
PID_FILE: Final = Path("/var/run/n64mini-topbar.pid")
THEME_ART: Final = Path("themes/n64-classic-mini/art")  # under the userdata folder
ROM_SUFFIXES: Final = frozenset({".z64", ".n64", ".v64"})
# A daemon from before this module ran as `python3 -c '# n64mini-topbar-daemon ...'`.
DAEMON_MARKERS: Final = (b"n64mini-topbar-daemon", b"-m\0n64mini.console.topbar")
UINPUT_NAME: Final = "n64mini-topbar-kb"
SKIP_DEVICES: Final = frozenset({"batocera hotkeys", UINPUT_NAME, "Power Button"})
DRAWER_START_GAME: Final = 10  # the drawer's exit code for "start the game" (suspend.main)

# Where things are on the 1280x720 design (AC-T21); scaled to the real screen.
DESIGN: Final = (1280, 720)
POCKET_BOX: Final = (470, 10, 341, 73)  # x, y, w, h: the recessed pocket and its three buttons
TOAST_BOX: Final = (472, 83, 336, 94)  # the drop-down toast under the middle button
BUTTON_EDGES: Final = (592, 688)  # x where a click moves from one button to the next
BAR_PROBE: Final = (200, 40)  # a top-bar pixel
PILL_PROBE: Final = (300, 105)  # the selected title's white pill, under the bar

Screen = Literal["carousel", "other"]
Window = Literal["pocket", "toast"]
Box = tuple[int, int, int, int]
RGB = tuple[int, int, int]

FIFO_KEYS: Final = {
    "up": ecodes.KEY_UP,
    "down": ecodes.KEY_DOWN,
    "left": ecodes.KEY_LEFT,
    "right": ecodes.KEY_RIGHT,
    "enter": ecodes.KEY_ENTER,
    "esc": ecodes.KEY_ESC,
}
# What the drawer reads on stdin (suspend.parse_command_token).
DRAWER_TOKENS: Final = frozenset(
    {"left", "right", "c_down", "save", "c_right", "copy", "c_left", "delete"}
    | {"a", "enter", "b", "esc", "start"}
)
DRAWER_KEYS: Final = {
    ecodes.KEY_LEFT: "left",
    ecodes.BTN_DPAD_LEFT: "left",
    ecodes.KEY_RIGHT: "right",
    ecodes.BTN_DPAD_RIGHT: "right",
    ecodes.KEY_C: "c_down",
    ecodes.KEY_S: "c_down",
    ecodes.KEY_F2: "c_down",
    ecodes.BTN_NORTH: "c_down",
    ecodes.BTN_C: "c_down",
    ecodes.KEY_V: "c_right",
    ecodes.KEY_F4: "c_right",
    ecodes.BTN_WEST: "c_right",
    ecodes.KEY_X: "c_left",
    ecodes.KEY_DELETE: "c_left",
    ecodes.BTN_TL2: "c_left",
    ecodes.KEY_ENTER: "a",
    ecodes.KEY_A: "a",
    ecodes.BTN_SOUTH: "a",
    ecodes.KEY_SPACE: "start",
    ecodes.KEY_P: "start",
    ecodes.BTN_START: "start",
    ecodes.KEY_ESC: "b",
    ecodes.KEY_BACKSPACE: "b",
    ecodes.KEY_B: "b",
    ecodes.BTN_EAST: "b",
}
CLOSE_KEYS: Final = frozenset({ecodes.KEY_DOWN, ecodes.KEY_ESC, ecodes.KEY_BACKSPACE, ecodes.KEY_B})
PRESS_KEYS: Final = frozenset({ecodes.KEY_ENTER, ecodes.KEY_SPACE, ecodes.KEY_A, ecodes.KEY_Z})


class TopbarError(RuntimeError):
    """The daemon can't run: no X display, or no virtual keyboard."""


def log(message: str) -> None:
    """One line to the daemon's log (its stderr)."""
    sys.stderr.write(f"{time.strftime('%H:%M:%S')} n64mini-topbar: {message}\n")
    sys.stderr.flush()


# ------------------------------------------------------------------ pure parts


def scale_box(box: Box, screen: tuple[int, int]) -> Box:
    """A box on the 1280x720 design, on a ``screen``-sized display."""
    x, y, w, h = box
    sx, sy = screen[0] / DESIGN[0], screen[1] / DESIGN[1]
    return round(x * sx), round(y * sy), round(w * sx), round(h * sy)


def classify(bar: RGB | None, pill: RGB | None) -> Screen:
    """The carousel shows the charcoal top bar with the white title pill under it."""
    if bar is None or pill is None:
        return "other"
    bar_lit = bar[0] >= 45 and bar[1] >= 48 and bar[2] >= 54  # the bar gradient's darkest
    pill_white = min(pill) > 180  # the pill is white; menus are dark
    return "carousel" if bar_lit and pill_white else "other"


def swap_red_blue(buf: bytes) -> bytes:
    """RGBA <-> BGRA (X11's 32-bit order)."""
    out = bytearray(buf)
    out[0::4], out[2::4] = out[2::4], out[0::4]
    return bytes(out)


def button_columns(index: int, width: int) -> tuple[int, int]:
    """The x range of button ``index``'s third of the pocket."""
    return index * width // 3, (index + 1) * width // 3


def compose_pocket(
    backdrop: bytes | None, lit: bytes, unlit: bytes | None, index: int, size: tuple[int, int]
) -> bytes:
    """The pocket over the live screen with only button ``index``'s highlight drawn on it.

    The pocket frames are drawn on the theme's brand art, which the live screen may no longer
    match (the music and library icons change), so only the pixels where the lit frame differs
    from the unlit one are copied onto what is on screen. Without a backdrop or an unlit frame,
    the lit frame is shown whole.
    """
    if backdrop is None or unlit is None:
        return lit
    width, height = size
    out = bytearray(backdrop)
    x0, x1 = button_columns(index, width)
    for y in range(height):
        for x in range(x0, x1):
            i = (y * width + x) * 4
            if lit[i : i + 3] != unlit[i : i + 3]:
                out[i : i + 4] = lit[i : i + 4]
    return bytes(out)


def button_at(x720: float, y720: float) -> int | None:
    """Which top-bar button a click at design coordinates lands on."""
    x, y, w, h = POCKET_BOX
    if not (x <= x720 <= x + w and y <= y720 <= y + h):
        return None
    return sum(x720 >= edge for edge in BUTTON_EDGES)


def metadata_order(
    roms: Iterable[str], hidden: Iterable[str], selected: str
) -> list[tuple[str, bool]]:
    """Each game with whether it's hidden; the shown ones, selected card first, lead.

    The engine redraws a card as soon as its metadata arrives, so the cards on screen are sent
    first and the icon the user just changed flips at once.
    """
    hidden_set = set(hidden)
    ordered = sorted(roms, key=lambda r: (r in hidden_set, r != selected, r))
    return [(rom, rom in hidden_set) for rom in ordered]


def tokens(command: bytes) -> list[str]:
    """The whitespace-separated words written to the command FIFO."""
    return command.decode("utf-8", errors="replace").split()


def stop_previous(proc: Path, pid_file: Path, me: int, kill: Callable[[int, int], None]) -> int:
    """Kill every other top-bar daemon (by its command line); returns how many."""
    pids: set[int] = set()
    with contextlib.suppress(OSError, ValueError):
        pids.add(int(pid_file.read_text(encoding="utf-8").strip()))
    pids.update(int(d.name) for d in proc.iterdir() if d.name.isdigit())
    killed = 0
    for pid in sorted(pids - {me}):
        try:
            cmdline = (proc / str(pid) / "cmdline").read_bytes()
        except OSError:
            continue  # gone, or never a daemon (a stale pid file)
        if any(marker in cmdline for marker in DAEMON_MARKERS):
            with contextlib.suppress(ProcessLookupError):
                kill(pid, signal.SIGKILL)
                killed += 1
    return killed


# ------------------------------------------------------------------ display


class Display(Protocol):
    """What the daemon draws with; `X11Display` on the console."""

    size: tuple[int, int]

    def pixel(self, x: int, y: int) -> RGB | None:
        """The screen's colour at a design point."""
        ...

    def grab(self, box: Box) -> bytes | None:
        """BGRA pixels of a screen-sized box, or None."""
        ...

    def show(self, window: Window, bgra: bytes) -> None:
        """Map ``window`` with ``bgra`` as its picture."""
        ...

    def hide(self, window: Window) -> None:
        """Unmap ``window``."""
        ...

    def pointer(self) -> tuple[int, int]:
        """The mouse pointer, in screen pixels."""
        ...


def _bind_x11(x11: ctypes.CDLL) -> None:
    c = ctypes
    vp, ul, i, ui = c.c_void_p, c.c_ulong, c.c_int, c.c_uint
    pul, pi = c.POINTER(ul), c.POINTER(i)
    sigs: dict[str, tuple[list[type], type | None]] = {
        "XOpenDisplay": ([c.c_char_p], vp),
        "XDefaultRootWindow": ([vp], ul),
        "XDefaultGC": ([vp, i], vp),
        "XDisplayWidth": ([vp, i], i),
        "XDisplayHeight": ([vp, i], i),
        "XDefaultDepth": ([vp, i], i),
        "XGetImage": ([vp, ul, i, i, ui, ui, ul, i], c.POINTER(XImage)),
        "XDestroyImage": ([c.POINTER(XImage)], i),
        "XCreatePixmap": ([vp, ul, ui, ui, ui], ul),
        "XCreateSimpleWindow": ([vp, ul, i, i, ui, ui, ui, ul, ul], ul),
        "XChangeWindowAttributes": ([vp, ul, ul, c.POINTER(_XSetWindowAttributes)], i),
        "XSetWindowBackgroundPixmap": ([vp, ul, ul], i),
        "XClearWindow": ([vp, ul], i),
        "XMapRaised": ([vp, ul], i),
        "XUnmapWindow": ([vp, ul], i),
        "XPutImage": ([vp, ul, vp, c.POINTER(XImage), i, i, i, i, ui, ui], i),
        "XFlush": ([vp], i),
        "XWarpPointer": ([vp, ul, ul, i, i, ui, ui, i, i], i),
        "XQueryPointer": ([vp, ul, pul, pul, pi, pi, pi, pi, c.POINTER(ui)], i),
    }
    for name, (args, res) in sigs.items():
        fn = getattr(x11, name)
        fn.argtypes = args
        fn.restype = res


class _XSetWindowAttributes(ctypes.Structure):
    _fields_ = [
        ("background_pixmap", ctypes.c_ulong),
        ("background_pixel", ctypes.c_ulong),
        ("border_pixmap", ctypes.c_ulong),
        ("border_pixel", ctypes.c_ulong),
        ("bit_gravity", ctypes.c_int),
        ("win_gravity", ctypes.c_int),
        ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong),
        ("backing_pixel", ctypes.c_ulong),
        ("save_under", ctypes.c_int),
        ("event_mask", ctypes.c_long),
        ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int),
        ("colormap", ctypes.c_ulong),
        ("cursor", ctypes.c_ulong),
    ]


_CW_OVERRIDE_REDIRECT: Final = 1 << 9
_ALL_PLANES: Final = 0xFFFFFFFF
_Z_PIXMAP: Final = 2


@dataclass(slots=True)
class _XWindow:
    win: int
    pixmap: int
    image: ctypes._Pointer[XImage]
    box: Box
    mapped: bool = False


class X11Display:
    """The engine's X display, through Xlib."""

    def __init__(self, x11: ctypes.CDLL, name: bytes = b":0.0") -> None:
        """Open the display and create the pocket and toast windows (unmapped)."""
        _bind_x11(x11)
        self.x11 = x11
        self.dpy = x11.XOpenDisplay(name)
        if not self.dpy:
            raise TopbarError(f"cannot open X display {name.decode()}")
        self.root = x11.XDefaultRootWindow(self.dpy)
        self.gc = x11.XDefaultGC(self.dpy, 0)
        self.size: tuple[int, int] = (
            int(x11.XDisplayWidth(self.dpy, 0)) or DESIGN[0],
            int(x11.XDisplayHeight(self.dpy, 0)) or DESIGN[1],
        )
        self.depth = int(x11.XDefaultDepth(self.dpy, 0)) or 24
        # Park the pointer in the corner, out of sight.
        x11.XWarpPointer(self.dpy, 0, self.root, 0, 0, 0, 0, self.size[0] - 1, self.size[1] - 1)
        x11.XFlush(self.dpy)
        self.windows = {
            "pocket": self._window(scale_box(POCKET_BOX, self.size)),
            "toast": self._window(scale_box(TOAST_BOX, self.size)),
        }

    def _window(self, box: Box) -> _XWindow:
        x, y, w, h = box
        win = self.x11.XCreateSimpleWindow(self.dpy, self.root, x, y, w, h, 0, 0, 0)
        attrs = _XSetWindowAttributes(override_redirect=1)
        self.x11.XChangeWindowAttributes(self.dpy, win, _CW_OVERRIDE_REDIRECT, ctypes.byref(attrs))
        image = self.x11.XGetImage(self.dpy, self.root, 0, 0, w, h, _ALL_PLANES, _Z_PIXMAP)
        if not image:
            raise TopbarError("XGetImage failed: no image buffer for the overlay")
        pixmap = self.x11.XCreatePixmap(self.dpy, self.root, w, h, self.depth)
        return _XWindow(win, pixmap, image, box)

    def pixel(self, x: int, y: int) -> RGB | None:
        """The screen's colour at design point (x, y)."""
        sx, sy, _, _ = scale_box((x, y, 0, 0), self.size)
        raw = self.grab((sx, sy, 1, 1))
        return None if raw is None else (raw[2], raw[1], raw[0])

    def grab(self, box: Box) -> bytes | None:
        """BGRA pixels of the screen in ``box`` (screen pixels)."""
        x, y, w, h = box
        image = self.x11.XGetImage(self.dpy, self.root, x, y, w, h, _ALL_PLANES, _Z_PIXMAP)
        if not image:
            return None
        raw = ctypes.string_at(image.contents.data, w * h * 4)
        self.x11.XDestroyImage(image)
        return raw

    def show(self, window: Window, bgra: bytes) -> None:
        """Give ``window`` its picture, then map it (in that order: no black frame first)."""
        w = self.windows[window]
        _, _, width, height = w.box
        ctypes.memmove(w.image.contents.data, bgra, width * height * 4)
        self.x11.XPutImage(self.dpy, w.pixmap, self.gc, w.image, 0, 0, 0, 0, width, height)
        self.x11.XSetWindowBackgroundPixmap(self.dpy, w.win, w.pixmap)
        self.x11.XClearWindow(self.dpy, w.win)
        if not w.mapped:
            self.x11.XMapRaised(self.dpy, w.win)
            w.mapped = True
        self.x11.XFlush(self.dpy)

    def hide(self, window: Window) -> None:
        """Unmap ``window`` if it's showing."""
        w = self.windows[window]
        if w.mapped:
            self.x11.XUnmapWindow(self.dpy, w.win)
            self.x11.XFlush(self.dpy)
            w.mapped = False

    def pointer(self) -> tuple[int, int]:
        """The pointer's position on the root window."""
        root, child = ctypes.c_ulong(), ctypes.c_ulong()
        rx, ry, wx, wy = ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        mask = ctypes.c_uint()
        self.x11.XQueryPointer(
            self.dpy,
            self.root,
            ctypes.pointer(root),
            ctypes.pointer(child),
            ctypes.pointer(rx),
            ctypes.pointer(ry),
            ctypes.pointer(wx),
            ctypes.pointer(wy),
            ctypes.pointer(mask),
        )
        return rx.value, ry.value


# ------------------------------------------------------------------ overlay


@dataclass(slots=True)
class Overlay:
    """The highlighted pocket and the Showcase/Full toast, over the engine's top bar."""

    display: Display
    userdata: Path
    clock: Callable[[], float] = time.monotonic
    pocket_shown: int | None = None
    toast_until: float | None = None
    backdrop: bytes | None = None

    def screen(self) -> Screen:
        """What the engine is showing."""
        return classify(self.display.pixel(*BAR_PROBE), self.display.pixel(*PILL_PROBE))

    def _frame(self, index: int, size: tuple[int, int]) -> bytes | None:
        raw = self.userdata / strip.TOPBAR / f"pocket-{index}.rgba"
        try:
            img = Image.frombytes("RGBA", POCKET_BOX[2:], raw.read_bytes())
        except (OSError, ValueError) as exc:
            log(f"pocket frame {raw}: {exc}")
            return None
        if size != img.size:
            img = img.resize(size, Image.Resampling.BILINEAR)
        return swap_red_blue(img.tobytes())

    def show_pocket(self, index: int) -> None:
        """Highlight button ``index`` (0 Settings, 1 Showcase/Full, 2 music)."""
        box = scale_box(POCKET_BOX, self.display.size)
        if self.pocket_shown is None:
            self.backdrop = self.display.grab(box)  # the live bar, before our window covers it
        lit = self._frame(index, box[2:])
        if lit is None:
            return
        unlit = self._frame((index + 1) % 3, box[2:])
        self.display.show("pocket", compose_pocket(self.backdrop, lit, unlit, index, box[2:]))
        self.pocket_shown = index

    def hide_pocket(self) -> None:
        """Remove the highlight."""
        self.display.hide("pocket")
        self.pocket_shown = None

    def show_toast(self, mode: str, seconds: float = 2.2) -> None:
        """Drop the "Showcase" or "Full Library" toast under the middle button for a while."""
        png = self.userdata / THEME_ART / f"toast_{mode}.png"
        box = scale_box(TOAST_BOX, self.display.size)
        self.display.hide("toast")
        backdrop = self.display.grab(box)
        size = box[2:]
        frame = (
            Image.new("RGBA", size, (26, 30, 38, 255))
            if backdrop is None
            else Image.frombytes("RGBA", size, swap_red_blue(backdrop))
        )
        frame.putalpha(255)
        try:
            with Image.open(png) as f:
                toast = f.convert("RGBA")
        except OSError as exc:
            log(f"toast {png}: {exc}")
            return
        if toast.size != size:
            toast = toast.resize(size, Image.Resampling.LANCZOS)
        frame.alpha_composite(toast)
        self.display.show("toast", swap_red_blue(frame.tobytes()))
        self.toast_until = self.clock() + seconds

    def hide_toast(self) -> None:
        """Remove the toast."""
        self.display.hide("toast")
        self.toast_until = None

    def toast_expired(self) -> bool:
        """Whether a toast is up and its time is over."""
        return self.toast_until is not None and self.clock() >= self.toast_until


# ------------------------------------------------------------------ library state


def start_thread(target: Callable[[], None]) -> None:
    """Run ``target`` in a daemon thread (the controller's default `spawn`)."""
    threading.Thread(target=target, daemon=True).start()


class MetadataSink(Protocol):
    """Where card metadata goes: the running engine (`Engine`)."""

    def set_metadata(self, path: str, fields: Mapping[str, str]) -> None:
        """Set a game's text fields."""
        ...


@dataclass(slots=True)
class LibrarySync:
    """Keeps the engine's cards and music in step with the library and music toggles."""

    userdata: Path
    engine: MetadataSink = field(default_factory=Engine)
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
    selected_file: Path = SELECTED_FILE
    lock: threading.Lock = field(default_factory=threading.Lock)
    sent: dict[str, tuple[str, str]] = field(default_factory=dict[str, tuple[str, str]])

    @property
    def roms_dir(self) -> Path:
        """The N64 games folder."""
        return self.userdata / strip.ROMS

    def showcase_on(self) -> bool:
        """Whether the Showcase lineup is the current list (AC-T14)."""
        return showcase.current_mode(self.roms_dir / "gamelist.xml") == "showcase"

    def mute_engine_music(self) -> None:
        """Mute the engine's own music stream while the theme's music is switched off.

        The engine keeps playing its copy of the old track until it reloads, so the music button
        mutes that stream in PulseAudio instead of restarting the engine.
        """
        muted = "1" if (self.userdata / strip.MUSIC_FULL).is_file() else "0"
        try:
            out = self.run(
                ["pactl", "list", "sink-inputs"],
                capture_output=True,
                text=True,
                timeout=3,
                check=True,
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            log(f"pactl list: {exc}")
            return
        for block in out.split("Sink Input #")[1:]:
            if "emulationstation" in block and "44100Hz" in block:
                sink = block.splitlines()[0].strip()
                self.run(["pactl", "set-sink-input-mute", sink, muted], check=False)

    def _selected(self) -> str:
        try:
            return Path(self.selected_file.read_text(encoding="utf-8").strip()).name
        except OSError:
            return ""  # nothing selected yet this boot

    def _set(self, rom: Path, fields: Mapping[str, str]) -> None:
        try:
            self.engine.set_metadata(str(rom), fields)
        except EngineError:
            pass  # the engine answers 404 when nothing changed (Engine.set_metadata)
        except OSError as exc:
            log(f"engine metadata for {rom.name}: {exc}")

    def sync(self, *, music_only: bool = False) -> None:
        """Push each card's hidden flag and top-bar icon to the engine; redraw the strips."""
        with self.lock:
            gamelist = self.roms_dir / "gamelist.xml"
            text = gamelist.read_text(encoding="utf-8") if gamelist.is_file() else ""
            hidden = {
                Path(entry_path(m.group(0))).name
                for m in GAME_RE.finditer(text)
                if element_text(m.group(0), "hidden") == "true"
            }
            topbar_png = str(strip.topbar_brand_path(self.userdata))
            roms = [
                p.name
                for p in self.roms_dir.iterdir()
                if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in ROM_SUFFIXES
            ]
            order = metadata_order(roms, hidden, self._selected())
            if not music_only:
                self._redraw_strips()
            for name, is_hidden in order:
                if is_hidden and music_only:
                    continue
                want = (
                    ("true", self.sent.get(name, ("", ""))[1])
                    if is_hidden
                    else ("false", topbar_png)
                )
                if self.sent.get(name) == want:
                    continue
                fields = (
                    {"hidden": "true"}
                    if is_hidden
                    else {"hidden": "false", "multidisk": topbar_png}
                )
                self._set(self.roms_dir / name, fields)
                self.sent[name] = want

    def _redraw_strips(self) -> None:
        try:
            if self.showcase_on():
                strip.generate(
                    self.roms_dir,
                    self.userdata / strip.STRIPS,
                    strip.extensions(self.userdata / strip.SYSTEMS_CFG),
                )
            else:
                strip.clear(self.userdata / strip.STRIPS)
        except (strip.StripError, OSError) as exc:
            log(f"strips: {exc}")

    def toggle_showcase(self) -> str:
        """Switch Showcase <-> Full and redraw the top bar; returns the new mode."""
        try:
            showcase.switch("toggle", self.roms_dir / "gamelist.xml", self.roms_dir)
            strip.sync_topbar(self.userdata)
        except (showcase.ShowcaseError, strip.StripError, OSError) as exc:
            log(f"library toggle: {exc}")
        return "showcase" if self.showcase_on() else "full"

    def toggle_music(self) -> None:
        """Switch the theme music on or off."""
        try:
            library.toggle_music(self.userdata, reload=False)
        except OSError as exc:
            log(f"music toggle: {exc}")
        self.mute_engine_music()


# ------------------------------------------------------------------ the drawer


class Library(Protocol):
    """What the controller asks of the library; `LibrarySync` on the console."""

    def sync(self, *, music_only: bool = False) -> None:
        """Bring the engine's cards up to date."""
        ...

    def toggle_showcase(self) -> str:
        """Showcase <-> Full; the new mode."""
        ...

    def toggle_music(self) -> None:
        """Music on <-> off."""
        ...

    def mute_engine_music(self) -> None:
        """Match the engine's music stream to the music setting."""
        ...


class KeyOut(Protocol):
    """The virtual keyboard keys go out through."""

    def emit(self, kind: int, code: int, value: int) -> None:
        """Send one event (with its SYN)."""
        ...

    def tap(self, code: int) -> None:
        """Press and release ``code``."""


class Process(Protocol):
    """The part of ``subprocess.Popen`` the drawer uses."""

    @property
    def stdin(self) -> IO[str] | None:
        """The pipe to the process."""
        ...

    def poll(self) -> int | None:
        """Its exit code once it has ended."""
        ...


@dataclass(slots=True)
class Drawer:
    """The Suspend Point List drawer process (``n64mini.console.suspend``), fed on stdin."""

    popen: Callable[..., Process] = subprocess.Popen
    proc: Process | None = None

    @property
    def open(self) -> bool:
        """Whether the drawer is up."""
        return self.proc is not None

    def start(self) -> None:
        """Open the drawer, unless it's already open."""
        if self.proc is not None:
            return
        try:
            self.proc = self.popen(
                [sys.executable, "-m", "n64mini.console.suspend"], stdin=subprocess.PIPE, text=True
            )
        except OSError as exc:
            log(f"drawer: {exc}")

    def send(self, token: str) -> None:
        """Pass a key to the drawer."""
        if self.proc is None or self.proc.stdin is None:
            return
        try:
            self.proc.stdin.write(token + "\n")
            self.proc.stdin.flush()
        except OSError as exc:
            log(f"drawer input: {exc}")

    def reap(self) -> int | None:
        """The drawer's exit code once it has closed (then it's forgotten), else None."""
        if self.proc is None:
            return None
        rc = self.proc.poll()
        if rc is not None:
            self.proc = None
        return rc


# ------------------------------------------------------------------ the controller


@dataclass(slots=True)
class Topbar:
    """What each key, click and FIFO command does."""

    overlay: Overlay
    library: Library
    keys: KeyOut
    drawer: Drawer
    spawn: Callable[[Callable[[], None]], None] = start_thread
    sleep: Callable[[float], None] = time.sleep
    open: bool = False
    selected: int = 0
    last_screen: Screen = "other"

    def _reap(self) -> None:
        rc = self.drawer.reap()
        if rc == DRAWER_START_GAME:
            self.keys.tap(ecodes.KEY_ENTER)  # the drawer chose "start": launch the selected card
        elif rc is not None:
            self.spawn(lambda: self.library.sync(music_only=True))

    def show(self, index: int) -> None:
        """Open the top bar with button ``index`` highlighted."""
        self.selected = index % 3
        self.open = True
        self.overlay.show_pocket(self.selected)

    def close(self) -> None:
        """Close the top bar."""
        self.open = False
        self.overlay.hide_pocket()

    def open_drawer(self) -> None:
        """Clear our windows off the screen and open the drawer."""
        if self.drawer.open:
            return
        self.overlay.hide_toast()
        self.close()
        self.drawer.start()

    def toggle_library(self) -> None:
        """Showcase <-> Full, with the toast, then bring the cards up to date."""
        self.close()
        mode = self.library.toggle_showcase()
        self.overlay.show_toast(mode)
        self.spawn(self.library.sync)

    def press(self) -> None:
        """Press the highlighted button."""
        index = self.selected
        self.close()
        if index == 0:
            self.overlay.hide_toast()
            self.keys.tap(ecodes.KEY_SPACE)  # the engine's own menu
        elif index == 1:
            self.toggle_library()
        else:
            self.library.toggle_music()
            self.spawn(lambda: self.library.sync(music_only=True))

    def _key_in_bar(self, code: int) -> None:
        if code == ecodes.KEY_LEFT:
            self.show(self.selected - 1)
        elif code == ecodes.KEY_RIGHT:
            self.show(self.selected + 1)
        elif code == ecodes.KEY_F3:
            self.toggle_library()
        elif code in CLOSE_KEYS:
            self.close()
        elif code in PRESS_KEYS:
            self.press()

    def key(self, code: int) -> bool:
        """Handle a key press; True if the daemon used it (so the engine mustn't see it)."""
        self._reap()
        if self.drawer.open:
            token = DRAWER_KEYS.get(code)
            if token is not None:
                self.drawer.send(token)
            return True
        if self.open:
            self._key_in_bar(code)
            return True
        if self.overlay.screen() != "carousel":
            return False
        if code == ecodes.KEY_UP:
            self.show(self.selected)
        elif code == ecodes.KEY_DOWN:
            self.open_drawer()
        elif code == ecodes.KEY_F3:
            self.toggle_library()
        else:
            return False
        return True

    def click(self, x720: float, y720: float) -> None:
        """A left click: press the top-bar button under it."""
        index = button_at(x720, y720)
        if index is not None and (self.open or self.overlay.screen() == "carousel"):
            self.selected = index
            self.press()

    def command(self, words: Sequence[str]) -> None:
        """Words from the command FIFO (``n64mini.console.reset`` and test tooling)."""
        for word in words:
            self._reap()
            if word == "suspend":
                # After a Reset the engine needs a moment to come back from the game.
                for _ in range(30):
                    if self.overlay.screen() == "carousel":
                        break
                    self.sleep(0.05)
                self.open_drawer()
            elif word == "toggle":
                self.toggle_library()
            elif self.drawer.open and word in DRAWER_TOKENS:
                self.drawer.send(word)
            elif word in FIFO_KEYS:
                if not self.key(FIFO_KEYS[word]):
                    self.keys.tap(FIFO_KEYS[word])
            elif word == "f5":
                self.keys.tap(ecodes.KEY_F5)
                self.spawn(self.library.sync)
            else:
                log(f"unknown command {word!r}")

    def tick(self) -> None:
        """Keep our windows in step with the screen (called ~60 times a second)."""
        self._reap()
        if self.drawer.open:
            return
        now = self.overlay.screen()
        if now == "carousel":
            if self.last_screen != "carousel":
                self.library.mute_engine_music()
            if self.overlay.toast_expired():
                self.overlay.hide_toast()
            if not self.open:
                self.overlay.hide_pocket()
        else:
            self.overlay.hide_toast()
            self.close()
        self.last_screen = now


# ------------------------------------------------------------------ input


class InputDevice(Protocol):
    """The part of ``evdev.InputDevice`` the daemon uses."""

    @property
    def name(self) -> str:
        """The kernel's device name."""
        ...

    @property
    def fd(self) -> int:
        """Its file descriptor."""
        ...

    def capabilities(self) -> Mapping[int, Sequence[int]]:
        """Event codes by type (no absinfo)."""
        ...

    def grab(self) -> None:
        """Take the device exclusively."""
        ...

    def close(self) -> None:
        """Release it."""
        ...

    def read(self) -> Iterable[evdev.InputEvent]:
        """Pending events."""
        ...


def classify_device(dev: InputDevice) -> Literal["keys", "mouse"] | None:
    """Keyboards (anything with arrow keys) are grabbed; mice are only watched for clicks."""
    if dev.name in SKIP_DEVICES:
        return None
    keys = dev.capabilities().get(ecodes.EV_KEY, ())
    if ecodes.KEY_UP in keys:
        return "keys"
    if ecodes.BTN_LEFT in keys:
        return "mouse"
    return None


@dataclass(slots=True)
class Router:
    """Routes one device event: to the controller first, else on through the virtual keyboard."""

    topbar: Topbar
    keys: KeyOut
    to_design: Callable[[], tuple[float, float]]
    held: set[int] = field(default_factory=set[int])  # pressed keys the daemon kept from the engine

    def mouse(self, ev: evdev.InputEvent) -> None:
        """Clicks press top-bar buttons; everything else from a mouse is ignored."""
        if ev.type == ecodes.EV_KEY and ev.code == ecodes.BTN_LEFT and ev.value == 1:
            self.topbar.click(*self.to_design())

    def keyboard(self, ev: evdev.InputEvent) -> None:
        """A key the daemon uses never reaches the engine: not its press, repeats or release."""
        if ev.type == ecodes.EV_SYN:
            return
        if ev.type != ecodes.EV_KEY:
            self.keys.emit(ev.type, ev.code, ev.value)
        elif ev.value == 1:
            if self.topbar.key(ev.code):
                self.held.add(ev.code)
            else:
                self.keys.emit(ev.type, ev.code, 1)
        elif ev.value == REPEAT:
            if ev.code not in self.held and not self.topbar.open:
                self.keys.emit(ev.type, ev.code, 2)
        elif ev.code in self.held:
            self.held.discard(ev.code)
        else:
            self.keys.emit(ev.type, ev.code, 0)


class VirtualKeyboard(Protocol):
    """The part of ``evdev.UInput`` the daemon uses."""

    def write(self, etype: int, code: int, value: int) -> None:
        """Queue one event."""
        ...

    def syn(self) -> None:
        """Send the queued events."""
        ...


@dataclass(slots=True)
class UInputKeys:
    """`KeyOut` through an ``evdev.UInput`` device."""

    ui: VirtualKeyboard
    sleep: Callable[[float], None] = time.sleep

    def emit(self, kind: int, code: int, value: int) -> None:
        """Send one event and a SYN."""
        self.ui.write(kind, code, value)
        self.ui.syn()

    def tap(self, code: int) -> None:
        """Press, hold for a frame or so, release."""
        self.emit(ecodes.EV_KEY, code, 1)
        self.sleep(0.06)
        self.emit(ecodes.EV_KEY, code, 0)


@dataclass(slots=True)
class Inputs:
    """The devices the daemon reads."""

    keyboards: dict[int, InputDevice] = field(default_factory=dict[int, InputDevice])
    mice: dict[int, InputDevice] = field(default_factory=dict[int, InputDevice])


def open_inputs(paths: Iterable[str], opener: Callable[[str], InputDevice]) -> Inputs:
    """Open every input device: grab the keyboards, watch the mice, close the rest."""
    found = Inputs()
    for path in paths:
        try:
            dev = opener(path)
        except OSError as exc:
            log(f"input {path}: {exc}")
            continue
        kind = classify_device(dev)
        try:
            if kind == "keys":
                dev.grab()
                found.keyboards[dev.fd] = dev
            elif kind == "mouse":
                found.mice[dev.fd] = dev
            else:
                dev.close()
        except OSError as exc:
            log(f"input {path} ({dev.name}): {exc}")
    return found


# ------------------------------------------------------------------ the loop


FRAME_S: Final = 0.016
REPEAT: Final = 2  # an evdev key event's value while auto-repeating


@dataclass(frozen=True, slots=True)
class Loop:
    """The loop's command FIFO, and (for tests) its clock and how many rounds it runs."""

    fifo_fd: int
    clock: Callable[[], float] = time.monotonic
    rounds: int | None = None  # None: forever


def serve(topbar: Topbar, router: Router, inputs: Inputs, loop: Loop) -> None:
    """The daemon's loop: read input and commands, and keep the windows in step every frame."""
    poll = select.poll()
    for fd in (*inputs.keyboards, *inputs.mice, loop.fifo_fd):
        poll.register(fd, select.POLLIN)
    last_tick = float("-inf")
    done = 0
    while loop.rounds is None or done < loop.rounds:
        done += 1
        events = poll.poll(int(FRAME_S * 1000))
        if not events or loop.clock() - last_tick >= FRAME_S:
            topbar.tick()
            last_tick = loop.clock()
        for fd, _ in events:
            if fd == loop.fifo_fd:
                try:
                    topbar.command(tokens(os.read(fd, 256)))
                except OSError as exc:
                    log(f"command fifo: {exc}")
                continue
            dev = inputs.keyboards.get(fd) or inputs.mice[fd]
            handle = router.mouse if fd in inputs.mice else router.keyboard
            try:
                for ev in dev.read():
                    handle(ev)
            except OSError as exc:
                log(f"input {dev.name}: {exc}")


def open_fifo(path: Path) -> int:
    """A fresh command FIFO, opened read-write so it never sees end-of-file."""
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    os.mkfifo(path)
    return os.open(path, os.O_RDWR | os.O_NONBLOCK)


def list_inputs(input_dir: Path = Path("/dev/input")) -> list[str]:
    """The event devices (what ``evdev.list_devices`` lists, minus its untyped signature)."""
    return [str(p) for p in sorted(input_dir.glob("event*"))]


def _uinput() -> VirtualKeyboard:
    return evdev.UInput({ecodes.EV_KEY: list(range(1, 249))}, name=UINPUT_NAME)


def _xlib() -> ctypes.CDLL:
    return ctypes.CDLL("libX11.so.6")


@dataclass(frozen=True, slots=True)
class Console:
    """The console's devices and paths; tests substitute them."""

    x11: Callable[[], ctypes.CDLL] = _xlib
    uinput: Callable[[], VirtualKeyboard] = _uinput
    list_inputs: Callable[[], Iterable[str]] = list_inputs
    open_input: Callable[[str], InputDevice] = evdev.InputDevice
    proc: Path = Path("/proc")
    userdata: Path = USERDATA
    fifo: Path = CMD_FIFO
    pid_file: Path = PID_FILE
    settle_s: float = 0.5  # lets the engine's SDL see the new keyboard before keys arrive on it
    make_library: Callable[[Path], LibrarySync] = LibrarySync
    spawn: Callable[[Callable[[], None]], None] = start_thread


def build(console: Console) -> tuple[Topbar, Router, Inputs, int]:
    """Wire the daemon to the display, the keyboards and the engine."""
    try:
        display = X11Display(console.x11())
    except OSError as exc:
        raise TopbarError(f"cannot load Xlib: {exc}") from exc
    try:
        keys = UInputKeys(console.uinput())
    except (OSError, evdev.UInputError) as exc:
        raise TopbarError(f"cannot create the virtual keyboard: {exc}") from exc
    time.sleep(console.settle_s)
    overlay = Overlay(display, console.userdata)
    sync = console.make_library(console.userdata)
    topbar = Topbar(overlay, sync, keys, Drawer(), spawn=console.spawn)

    def to_design() -> tuple[float, float]:
        x, y = display.pointer()
        return x * DESIGN[0] / display.size[0], y * DESIGN[1] / display.size[1]

    router = Router(topbar, keys, to_design)
    inputs = open_inputs(console.list_inputs(), console.open_input)
    fifo_fd = open_fifo(console.fifo)
    sync.mute_engine_music()
    topbar.spawn(sync.sync)
    return topbar, router, inputs, fifo_fd


def main(
    argv: list[str] | None = None, *, console: Console | None = None, rounds: int | None = None
) -> int:
    """Run the daemon (until killed). Exit 2 if it can't reach the display or keyboards."""
    argparse.ArgumentParser(
        description="The top-bar daemon (started by n64mini-topbar.sh)."
    ).parse_args(argv)
    console = console or Console()
    stopped = stop_previous(console.proc, console.pid_file, os.getpid(), os.kill)
    if stopped:
        log(f"replaced {stopped} running daemon(s)")
    console.pid_file.write_text(str(os.getpid()), encoding="utf-8")
    try:
        strip.sync_topbar(console.userdata)
    except (strip.StripError, OSError) as exc:
        log(f"top-bar art: {exc}")
    try:
        topbar, router, inputs, fifo_fd = build(console)
    except TopbarError as exc:
        log(str(exc))
        return 2
    serve(topbar, router, inputs, Loop(fifo_fd, rounds=rounds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
