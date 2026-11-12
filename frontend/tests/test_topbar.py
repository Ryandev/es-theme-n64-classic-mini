"""The top-bar daemon (n64mini.console.topbar; AC-T16, AC-T21) against fake X11 and devices."""

from __future__ import annotations

import ctypes
import io
import os
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, override

import evdev
import pytest
from evdev import ecodes
from PIL import Image

from n64mini.console import flip, library, showcase, strip
from n64mini.console import topbar as tb
from n64mini.console.engine import EngineError
from n64mini.console.suspend import XImage

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

W, H = tb.POCKET_BOX[2:]


class _IntOut(Protocol):
    """An ``int *`` out-parameter (``ctypes.pointer(c_int())``)."""

    @property
    def contents(self) -> ctypes.c_int: ...


def _set_int(out: _IntOut, value: int) -> None:
    out.contents.value = value


CHARCOAL = (60, 64, 70)  # a top-bar gradient colour
WHITE = (240, 240, 240)  # the title pill


# ------------------------------------------------------------------ fakes


class _Fn:
    """One Xlib function: records its calls, returns `ret` (or `impl(*args)`)."""

    def __init__(self, ret: object = 0, impl: Callable[..., object] | None = None) -> None:
        self.ret, self.impl = ret, impl
        self.argtypes: object = None
        self.restype: object = None
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *args: object) -> object:
        self.calls.append(args)
        return self.impl(*args) if self.impl else self.ret


class _FakeX11(ctypes.CDLL):
    """Xlib over an in-memory BGRA screen; every call is logged in order in `log`."""

    def __init__(
        self, *, dpy: int = 1, size: tuple[int, int] = (1280, 720), overlay_images: bool = True
    ) -> None:
        # Deliberately bypass ctypes.CDLL.__init__: no shared library is opened.
        self.size = size
        self.screen = bytearray(b"\x00\x00\x00\xff" * (size[0] * size[1]))
        self.keep: list[object] = []
        self.log: list[str] = []
        self.overlay_images = overlay_images
        self.pointer_at: tuple[int, int] = (640, 360)
        self.XWarpPointer = self._logged("XWarpPointer", _Fn(7))
        for name in (
            "XDefaultRootWindow",
            "XDefaultGC",
            "XDefaultDepth",
            "XDestroyImage",
            "XCreatePixmap",
            "XChangeWindowAttributes",
            "XSetWindowBackgroundPixmap",
            "XClearWindow",
            "XMapRaised",
            "XUnmapWindow",
            "XPutImage",
            "XFlush",
        ):
            setattr(self, name, self._logged(name, _Fn(7)))
        self.XOpenDisplay = _Fn(dpy)
        self.XDisplayWidth = _Fn(size[0])
        self.XDisplayHeight = _Fn(size[1])
        self.XCreateSimpleWindow = self._logged("XCreateSimpleWindow", _Fn(impl=self._window))
        self.XGetImage = _Fn(impl=self._get_image)
        self.XQueryPointer = _Fn(impl=self._query)
        self.windows: list[tuple[object, ...]] = []

    def _logged(self, name: str, fn: _Fn) -> _Fn:
        inner = fn.impl

        def impl(*args: object) -> object:
            self.log.append(name)
            return inner(*args) if inner else fn.ret

        fn.impl = impl
        return fn

    def _window(self, *args: object) -> int:
        self.windows.append(args[2:6])
        return 100 + len(self.windows)

    def paint(self, x: int, y: int, rgb: tuple[int, int, int]) -> None:
        i = (y * self.size[0] + x) * 4
        self.screen[i : i + 4] = bytes((rgb[2], rgb[1], rgb[0], 255))

    def _get_image(self, *args: object) -> object:
        x, y, w, h = (int(str(a)) for a in args[2:6])
        if not self.overlay_images and (x, y) == (0, 0):
            return ctypes.POINTER(XImage)()
        rows = [
            bytes(
                self.screen[((y + r) * self.size[0] + x) * 4 : ((y + r) * self.size[0] + x + w) * 4]
            )
            for r in range(h)
        ]
        buf = ctypes.create_string_buffer(b"".join(rows), w * h * 4)
        image = XImage(width=w, height=h, data=ctypes.addressof(buf))
        self.keep += [buf, image]
        return ctypes.pointer(image)

    def _query(  # XQueryPointer(dpy, root, &root, &child, &root_x, &root_y, ...)
        self,
        _dpy: object,
        _root: object,
        _r: object,
        _c: object,
        rx: _IntOut,
        ry: _IntOut,
        *_: object,
    ) -> int:
        _set_int(rx, self.pointer_at[0])
        _set_int(ry, self.pointer_at[1])
        return 1


@dataclass
class _FakeDisplay:
    """A `tb.Display`: a screen of two probe colours and a record of shown windows."""

    size: tuple[int, int] = (1280, 720)
    bar: tuple[int, int, int] | None = CHARCOAL
    pill: tuple[int, int, int] | None = WHITE
    backdrop: bytes | None = None
    shown: dict[str, bytes] = field(default_factory=dict[str, bytes])
    grabs: int = 0

    def pixel(self, x: int, y: int) -> tuple[int, int, int] | None:
        return self.bar if (x, y) == tb.BAR_PROBE else self.pill

    def grab(self, box: tb.Box) -> bytes | None:
        self.grabs += 1
        _, _, w, h = box
        return None if self.backdrop is None else self.backdrop[: w * h * 4]

    def show(self, window: tb.Window, bgra: bytes) -> None:
        self.shown[window] = bgra

    def hide(self, window: tb.Window) -> None:
        self.shown.pop(window, None)

    def pointer(self) -> tuple[int, int]:
        return (0, 0)


@dataclass
class _FakeLibrary:
    """A `tb.Library` that records what was asked of it."""

    mode: str = "showcase"
    calls: list[str] = field(default_factory=list[str])

    def sync(self, *, music_only: bool = False) -> None:
        self.calls.append("sync music" if music_only else "sync")

    def toggle_showcase(self) -> str:
        self.calls.append("toggle showcase")
        return self.mode

    def toggle_music(self) -> None:
        self.calls.append("toggle music")

    def mute_engine_music(self) -> None:
        self.calls.append("mute")


@dataclass
class _FakeKeys:
    events: list[tuple[int, int, int]] = field(default_factory=list[tuple[int, int, int]])

    def emit(self, kind: int, code: int, value: int) -> None:
        self.events.append((kind, code, value))

    def tap(self, code: int) -> None:
        self.events.append((ecodes.EV_KEY, code, -1))  # -1: a whole tap


class _BrokenStdin(io.StringIO):
    @override
    def write(self, _s: str, /) -> int:
        raise BrokenPipeError("drawer gone")


class _FakeProc:
    def __init__(self, stdin: io.StringIO | None = None) -> None:
        self.stdin: io.StringIO | None = stdin if stdin is not None else io.StringIO()
        self.rc: int | None = None

    def poll(self) -> int | None:
        return self.rc

    def sent(self) -> str:
        assert self.stdin is not None
        return self.stdin.getvalue()


def _frames(userdata: Path, *, lit_pixel: int = 5) -> None:
    """Pocket frames 0-2: grey, each with its own button's pixel `lit_pixel` (in its third) red."""
    d = userdata / strip.TOPBAR
    d.mkdir(parents=True, exist_ok=True)
    for idx in range(3):
        img = Image.new("RGBA", (W, H), (36, 39, 44, 255))
        x0, _ = tb.button_columns(idx, W)
        img.putpixel((x0 + lit_pixel, 0), (255, 0, 0, 255))
        (d / f"pocket-{idx}.rgba").write_bytes(img.tobytes())


def _toast(userdata: Path, mode: str, size: tuple[int, int]) -> None:
    art = userdata / tb.THEME_ART
    art.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (255, 255, 0, 128)).save(art / f"toast_{mode}.png")


@dataclass
class _Rig:
    display: _FakeDisplay
    library: _FakeLibrary
    keys: _FakeKeys
    drawer: tb.Drawer
    topbar: tb.Topbar
    spawned: list[Callable[[], None]]
    slept: list[float]
    procs: list[_FakeProc]


def _rig(tmp_path: Path, *, now: list[float] | None = None) -> _Rig:
    clock = now if now is not None else [0.0]
    display = _FakeDisplay(backdrop=b"\x10\x20\x30\xff" * (1280 * 720))  # a whole screen
    _frames(tmp_path)
    _toast(tmp_path, "showcase", tb.TOAST_BOX[2:])
    _toast(tmp_path, "full", tb.TOAST_BOX[2:])
    overlay = tb.Overlay(display, tmp_path, clock=lambda: clock[0])
    library = _FakeLibrary()
    keys = _FakeKeys()
    procs: list[_FakeProc] = []

    def popen(*_a: object, **_k: object) -> tb.Process:
        procs.append(_FakeProc())
        return procs[-1]

    drawer = tb.Drawer(popen=popen)
    spawned: list[Callable[[], None]] = []
    slept: list[float] = []
    topbar = tb.Topbar(overlay, library, keys, drawer, spawn=spawned.append, sleep=slept.append)
    return _Rig(display, library, keys, drawer, topbar, spawned, slept, procs)


def _no_pactl(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, "", "")


def _ev(kind: int, code: int, value: int) -> evdev.InputEvent:
    return evdev.InputEvent(0, 0, kind, code, value)


# ------------------------------------------------------------------ pure parts


def test_boxes_scale_to_the_screen_and_clicks_find_their_button() -> None:
    assert tb.scale_box(tb.POCKET_BOX, (1280, 720)) == tb.POCKET_BOX
    assert tb.scale_box(tb.POCKET_BOX, (1920, 1080)) == (705, 15, 512, 110)
    assert [tb.button_at(x, 40) for x in (470, 591, 592, 687, 688, 811)] == [0, 0, 1, 1, 2, 2]
    assert tb.button_at(469, 40) is None
    assert tb.button_at(812, 40) is None
    assert tb.button_at(500, 9) is None
    assert tb.button_at(500, 84) is None
    assert [tb.button_columns(i, 341) for i in range(3)] == [(0, 113), (113, 227), (227, 341)]


@pytest.mark.parametrize(
    ("bar", "pill", "screen"),
    [
        (CHARCOAL, WHITE, "carousel"),
        ((45, 48, 54), (181, 181, 181), "carousel"),  # both at their thresholds
        ((44, 48, 54), WHITE, "other"),
        ((45, 47, 54), WHITE, "other"),
        ((45, 48, 53), WHITE, "other"),
        (CHARCOAL, (180, 240, 240), "other"),  # a dark menu where the pill would be
        (None, WHITE, "other"),
        (CHARCOAL, None, "other"),
    ],
)
def test_the_carousel_is_recognised_by_its_bar_and_title_pill(
    bar: tuple[int, int, int] | None, pill: tuple[int, int, int] | None, screen: str
) -> None:
    assert tb.classify(bar, pill) == screen


def test_only_the_lit_buttons_own_highlight_is_drawn_over_the_live_bar() -> None:
    size = (6, 1)
    backdrop = bytes(range(24))
    unlit = b"\x00" * 24
    lit = bytearray(unlit)
    lit[4:8] = b"\x01\x02\x03\x04"  # pixel 1: in button 0's third (pixels 0-1)
    lit[16:20] = b"\x09\x09\x09\x09"  # pixel 4: button 2's third, not 0's
    lit[0:4] = b"\x00\x00\x00\x77"  # pixel 0: alpha alone differs: not a highlight
    out = tb.compose_pocket(backdrop, bytes(lit), unlit, 0, size)
    assert out == backdrop[:4] + b"\x01\x02\x03\x04" + backdrop[8:]
    assert tb.compose_pocket(None, bytes(lit), unlit, 0, size) == bytes(lit)
    assert tb.compose_pocket(backdrop, bytes(lit), None, 0, size) == bytes(lit)
    assert (
        tb.swap_red_blue(b"\x01\x02\x03\x04\x05\x06\x07\x08") == b"\x03\x02\x01\x04\x07\x06\x05\x08"
    )


def test_cards_on_screen_get_their_metadata_first_selected_first() -> None:
    order = tb.metadata_order(["c.z64", "a.z64", "h.z64", "b.z64"], ["h.z64"], "b.z64")
    assert order == [("b.z64", False), ("a.z64", False), ("c.z64", False), ("h.z64", True)]
    assert tb.tokens(b" suspend\nup  \xff") == ["suspend", "up", "�"]


def test_starting_replaces_only_other_top_bar_daemons(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    for pid, cmd in {
        11: b"python3\0-c\0# n64mini-topbar-daemon\nimport x",  # the pre-module daemon
        12: b"python3\0-m\0n64mini.console.topbar\0",
        13: b"python3\0-m\0n64mini.console.suspend\0",  # the drawer: not a daemon
        14: b"python3\0-m\0n64mini.console.topbar\0",  # this process
        15: b"python3\0-m\0n64mini.console.topbar\0",  # exits before it's killed
    }.items():
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / "cmdline").write_bytes(cmd)
    (proc / "self").mkdir()
    pid_file = tmp_path / "topbar.pid"
    pid_file.write_text("99\n")  # stale: no such process
    killed: list[tuple[int, int]] = []

    def kill(pid: int, sig: int) -> None:
        if pid == 15:  # the pid that exits first
            raise ProcessLookupError
        killed.append((pid, sig))

    assert tb.stop_previous(proc, pid_file, 14, kill) == 2  # pids 11 and 12
    assert killed == [(11, signal.SIGKILL), (12, signal.SIGKILL)]
    pid_file.write_text("not a pid")
    assert tb.stop_previous(proc, pid_file, 14, kill) == 2  # still 11 and 12


# ------------------------------------------------------------------ X11


def test_the_x11_display_opens_parks_the_pointer_and_makes_two_hidden_windows() -> None:
    x11 = _FakeX11(size=(1920, 1080))
    d = tb.X11Display(x11)
    assert d.size == (1920, 1080)
    assert x11.windows == [(705, 15, 512, 110), (708, 124, 504, 141)]
    warp = x11.XWarpPointer.calls[0]
    assert warp[-2:] == (1919, 1079)
    assert "XMapRaised" not in x11.log
    assert x11.XGetImage.restype is not None  # signatures bound before the first call
    with pytest.raises(tb.TopbarError, match=r"cannot open X display :0\.0"):
        tb.X11Display(_FakeX11(dpy=0))
    with pytest.raises(tb.TopbarError, match="no image buffer"):
        tb.X11Display(_FakeX11(overlay_images=False))
    unsized = _FakeX11()
    unsized.XDisplayWidth.ret = 0
    unsized.XDisplayHeight.ret = 0
    assert tb.X11Display(unsized).size == tb.DESIGN


def test_a_window_gets_its_picture_before_it_is_mapped_and_maps_once() -> None:
    x11 = _FakeX11()
    d = tb.X11Display(x11)
    x11.log.clear()
    d.show("pocket", b"\x01\x02\x03\x04" * (W * H))
    assert x11.log == [
        "XPutImage",
        "XSetWindowBackgroundPixmap",
        "XClearWindow",
        "XMapRaised",
        "XFlush",
    ]
    x11.log.clear()
    d.show("pocket", b"\x05" * (W * H * 4))
    assert "XMapRaised" not in x11.log
    x11.log.clear()
    d.hide("pocket")
    d.hide("pocket")
    d.hide("toast")
    assert x11.log == ["XUnmapWindow", "XFlush"]


def test_the_x11_display_reads_pixels_and_the_pointer() -> None:
    x11 = _FakeX11(size=(640, 360))
    d = tb.X11Display(x11)
    x11.paint(100, 20, (10, 20, 30))  # design (200, 40) on a half-size screen
    assert d.pixel(*tb.BAR_PROBE) == (10, 20, 30)
    assert d.grab((100, 20, 1, 1)) == bytes((30, 20, 10, 255))
    x11.pointer_at = (321, 123)
    assert d.pointer() == (321, 123)

    def no_image(*_a: object) -> object:
        return ctypes.POINTER(XImage)()

    x11.XGetImage.impl = no_image
    assert d.pixel(*tb.BAR_PROBE) is None


# ------------------------------------------------------------------ overlay


def test_the_pocket_highlights_one_button_over_a_backdrop_taken_once(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    overlay = rig.topbar.overlay
    assert overlay.screen() == "carousel"
    overlay.show_pocket(1)
    shown = rig.display.shown["pocket"]
    x0, _ = tb.button_columns(1, W)
    assert shown[(x0 + 5) * 4 : (x0 + 5) * 4 + 4] == b"\x00\x00\xff\xff"  # red, in BGRA
    assert shown[:4] == b"\x10\x20\x30\xff"  # the backdrop elsewhere
    other = tb.button_columns(0, W)[0] + 5
    assert shown[other * 4 : other * 4 + 4] == b"\x10\x20\x30\xff"  # button 0 isn't lit
    overlay.show_pocket(2)
    assert rig.display.grabs == 1  # the backdrop isn't re-taken while the pocket is up
    overlay.hide_pocket()
    assert "pocket" not in rig.display.shown
    overlay.show_pocket(0)
    assert rig.display.grabs == 2  # re-taken after the pocket was hidden


def test_a_missing_pocket_frame_is_logged_and_nothing_is_drawn(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rig = _rig(tmp_path)
    (tmp_path / strip.TOPBAR / "pocket-1.rgba").unlink()
    rig.topbar.overlay.show_pocket(1)
    assert "pocket" not in rig.display.shown
    assert "pocket-1.rgba" in capsys.readouterr().err
    rig.topbar.overlay.show_pocket(0)  # its unlit partner (frame 1) is missing: shown whole
    assert rig.display.shown["pocket"][:4] == b"\x2c\x27\x24\xff"


def test_the_pocket_is_scaled_to_a_bigger_screen(tmp_path: Path) -> None:
    display = _FakeDisplay(size=(1920, 1080))
    _frames(tmp_path)
    overlay = tb.Overlay(display, tmp_path)
    overlay.show_pocket(0)
    assert len(display.shown["pocket"]) == 512 * 110 * 4


def test_the_toast_drops_over_the_screen_and_expires(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    now = [100.0]
    display = _FakeDisplay(backdrop=b"\x00\x00\xff\xff" * (336 * 94))  # red screen, BGRA
    overlay = tb.Overlay(display, tmp_path, clock=lambda: now[0])
    _toast(tmp_path, "full", (168, 47))  # half size: scaled up
    overlay.show_toast("full", seconds=2.0)
    px = display.shown["toast"][:4]
    assert px == bytes((0, 128, 255, 255))  # 50 % yellow over red, BGRA
    assert not overlay.toast_expired()
    now[0] = 102.0
    assert overlay.toast_expired()
    overlay.hide_toast()
    assert "toast" not in display.shown
    assert not overlay.toast_expired()
    display.backdrop = None
    overlay.show_toast("full")
    # Over the dark fill (26, 30, 38): 255*a + c*(1-a) with a = 128/255, per channel, in BGRA.
    assert display.shown["toast"][:4] == bytes((19, 143, 141, 255))
    overlay.show_toast("showcase")  # no such art
    assert "toast" not in display.shown
    assert "toast_showcase.png" in capsys.readouterr().err


# ------------------------------------------------------------------ library sync


@dataclass
class _FakeEngine:
    sent: list[tuple[str, dict[str, str]]] = field(default_factory=list[tuple[str, dict[str, str]]])
    fail: type[Exception] | None = None

    def set_metadata(self, path: str, fields: Mapping[str, str]) -> None:
        if self.fail is not None:
            raise self.fail("engine says no")
        self.sent.append((Path(path).name, dict(fields)))


def _library(tmp_path: Path, engine: _FakeEngine) -> tb.LibrarySync:
    roms = tmp_path / strip.ROMS
    roms.mkdir(parents=True)
    for name in ("b.z64", "a.Z64", "h.n64", ".hidden.z64", "notes.txt"):
        (roms / name).write_bytes(b"")
    (roms / "dir.z64").mkdir()
    (roms / "gamelist.xml").write_text(
        "<gameList>\n"
        "<game><path>./h.n64</path><hidden>true</hidden></game>\n"
        "<game><path>./a.Z64</path><hidden>false</hidden></game>\n"
        "</gameList>\n",
        encoding="utf-8",
    )
    selected = tmp_path / "selected"
    selected.write_text("/userdata/roms/n64/b.z64\n", encoding="utf-8")
    return tb.LibrarySync(tmp_path, engine=engine, selected_file=selected)


def test_sync_sends_each_card_its_hidden_flag_and_icon_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _FakeEngine()
    lib = _library(tmp_path, engine)
    strips: list[str] = []

    def clear(_out: Path) -> None:
        strips.append("clear")

    def generate(*_a: object) -> int:
        strips.append("generate")
        return 0

    def extensions(_cfg: Path) -> frozenset[str]:
        return frozenset({".z64"})

    monkeypatch.setattr(strip, "clear", clear)
    monkeypatch.setattr(strip, "generate", generate)
    monkeypatch.setattr(strip, "extensions", extensions)
    icon = str(strip.topbar_brand_path(tmp_path))
    lib.sync()
    assert engine.sent == [
        ("b.z64", {"hidden": "false", "multidisk": icon}),
        ("a.Z64", {"hidden": "false", "multidisk": icon}),
        ("h.n64", {"hidden": "true"}),
    ]
    assert strips == ["clear"]  # Full: no strips
    engine.sent.clear()
    lib.sync()
    assert engine.sent == []  # nothing changed
    (tmp_path / strip.ROMS / "gamelist.xml.showcase.json").write_text("{}")
    lib.sent.clear()
    lib.sync(music_only=True)
    assert [name for name, _ in engine.sent] == ["b.z64", "a.Z64"]  # hidden cards are skipped
    assert strips == ["clear", "clear"]  # and a music-only sync doesn't redraw the strips
    lib.sync()
    assert strips == ["clear", "clear", "generate"]  # Showcase: strips drawn


def test_sync_survives_a_missing_list_selection_and_engine_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = _FakeEngine(fail=EngineError)
    lib = _library(tmp_path, engine)
    (tmp_path / strip.ROMS / "gamelist.xml").unlink()
    lib.selected_file.unlink()

    def broken(*_a: object) -> None:
        raise strip.StripError("bad art")

    monkeypatch.setattr(strip, "clear", broken)
    lib.sync()  # 404s (nothing changed) are expected and quiet
    err = capsys.readouterr().err
    assert "strips: bad art" in err
    assert "engine" not in err
    assert set(lib.sent) == {"a.Z64", "b.z64", "h.n64"}  # with no list, nothing is hidden
    engine.fail = OSError
    lib.sent.clear()
    lib.sync(music_only=True)
    assert "engine metadata for b.z64: engine says no" in capsys.readouterr().err


def test_the_engines_music_stream_is_muted_while_the_music_is_off(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    listing = "".join(
        f'Sink Input #{n}\n\tapplication.name = "{app}"\n\tSample Specification: s16le 2ch {hz}\n'
        for n, app, hz in [
            (12, "emulationstation", "44100Hz"),
            (13, "emulationstation", "48000Hz"),
            (14, "mupen64plus", "44100Hz"),
        ]
    )

    def run(args: list[str], **_k: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, listing, "")

    lib = tb.LibrarySync(tmp_path, run=run)
    lib.mute_engine_music()
    assert calls[1:] == [["pactl", "set-sink-input-mute", "12", "0"]]
    (tmp_path / strip.MUSIC_FULL).parent.mkdir(parents=True)
    (tmp_path / strip.MUSIC_FULL).write_bytes(b"")
    calls.clear()
    lib.mute_engine_music()
    assert calls[1:] == [["pactl", "set-sink-input-mute", "12", "1"]]


def test_no_pulseaudio_is_logged(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def run(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("pactl", 3)

    tb.LibrarySync(tmp_path, run=run).mute_engine_music()
    assert "pactl list" in capsys.readouterr().err


def test_the_toggles_switch_the_library_and_music_and_log_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    roms = tmp_path / strip.ROMS
    roms.mkdir(parents=True)
    state = roms / "gamelist.xml.showcase.json"
    switched: list[str] = []

    def switch(mode: str, gamelist: Path, roms_dir: Path) -> str:
        switched.append(mode)
        state.write_text("{}")
        assert (gamelist, roms_dir) == (roms / "gamelist.xml", roms)
        return "showcase"

    monkeypatch.setattr(showcase, "switch", switch)

    def sync_topbar(_userdata: Path) -> bool:
        return True

    monkeypatch.setattr(strip, "sync_topbar", sync_topbar)
    lib = tb.LibrarySync(tmp_path, run=_no_pactl)
    assert lib.toggle_showcase() == "showcase"
    assert switched == ["toggle"]

    def fail(*_a: object) -> str:
        raise showcase.ShowcaseError("no lineup")

    monkeypatch.setattr(showcase, "switch", fail)
    state.unlink()
    assert lib.toggle_showcase() == "full"
    assert "library toggle: no lineup" in capsys.readouterr().err

    toggled: list[tuple[Path, bool]] = []

    def toggle_music(userdata: Path, *, reload: bool) -> bool:
        toggled.append((userdata, reload))
        return True

    monkeypatch.setattr(library, "toggle_music", toggle_music)
    lib.toggle_music()
    assert toggled == [(tmp_path, False)]  # no engine reload: the stream is muted instead

    def no_music(*_a: object, **_k: object) -> bool:
        raise OSError("read-only")

    monkeypatch.setattr(library, "toggle_music", no_music)
    lib.toggle_music()
    assert "music toggle: read-only" in capsys.readouterr().err


# ------------------------------------------------------------------ drawer


def test_the_drawer_opens_once_takes_keys_and_reports_how_it_closed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    procs: list[_FakeProc] = []
    args: list[object] = []

    def popen(cmd: object, **_k: object) -> tb.Process:
        args.append(cmd)
        procs.append(_FakeProc())
        return procs[-1]

    drawer = tb.Drawer(popen=popen)
    drawer.send("left")  # not open: dropped
    assert drawer.reap() is None
    drawer.start()
    drawer.start()
    assert len(procs) == 1
    assert args == [[sys.executable, "-m", "n64mini.console.suspend"]]
    drawer.send("c_down")
    assert procs[0].sent() == "c_down\n"
    assert drawer.reap() is None  # still open
    procs[0].rc = 10
    assert drawer.reap() == 10  # the drawer's exit code
    assert not drawer.open

    procs[0].stdin = _BrokenStdin()  # a drawer that died: its stdin pipe is broken
    drawer.proc = procs[0]
    drawer.send("a")
    assert "drawer input" in capsys.readouterr().err
    procs[0].stdin = None
    drawer.send("a")

    def broken(*_a: object, **_k: object) -> tb.Process:
        raise OSError("no python")

    lost = tb.Drawer(popen=broken)
    lost.start()
    assert not lost.open
    assert "drawer: no python" in capsys.readouterr().err


# ------------------------------------------------------------------ controller


def test_up_opens_the_bar_left_right_move_and_down_closes(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    t = rig.topbar
    assert t.key(ecodes.KEY_UP)
    assert (t.open, t.selected, rig.topbar.overlay.pocket_shown) == (True, 0, 0)
    assert t.key(ecodes.KEY_LEFT)
    assert t.selected == 2  # wraps round
    assert t.key(ecodes.KEY_RIGHT)
    assert t.selected == 0
    assert t.key(ecodes.KEY_Q)  # any other key is swallowed while the bar is open
    for close in (ecodes.KEY_DOWN, ecodes.KEY_ESC, ecodes.KEY_BACKSPACE, ecodes.KEY_B):
        t.show(1)
        assert t.key(close)
        assert not t.open
        assert "pocket" not in rig.display.shown
    assert rig.keys.events == []


def test_pressing_each_button(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    t = rig.topbar
    t.show(0)
    assert t.key(ecodes.KEY_ENTER)
    assert rig.keys.events == [(ecodes.EV_KEY, ecodes.KEY_SPACE, -1)]  # the engine's menu
    t.show(1)
    assert t.key(ecodes.KEY_A)
    assert rig.library.calls == ["toggle showcase"]
    assert rig.topbar.overlay.toast_until is not None
    t.show(2)
    assert t.key(ecodes.KEY_Z)
    assert rig.library.calls == ["toggle showcase", "toggle music"]
    for job in rig.spawned:
        job()
    assert rig.library.calls[2:] == ["sync", "sync music"]
    assert not t.open
    t.show(1)
    assert t.key(ecodes.KEY_F3)
    assert rig.library.calls[-1] == "toggle showcase"


def test_on_the_carousel_down_opens_the_drawer_and_f3_toggles(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    t = rig.topbar
    t.overlay.show_toast("full")
    assert t.key(ecodes.KEY_DOWN)
    assert len(rig.procs) == 1
    assert rig.display.shown == {}  # our windows are off the screen first
    t.open_drawer()
    assert len(rig.procs) == 1
    rig.procs[0].rc = 0
    assert t.key(ecodes.KEY_F3)  # the drawer closed: F3 toggles the library again
    assert rig.library.calls == ["toggle showcase"]
    assert not t.key(ecodes.KEY_ENTER)  # the engine's
    rig.display.pill = (20, 20, 20)
    assert not t.key(ecodes.KEY_UP)  # not on the carousel: the engine's


def test_keys_go_to_the_open_drawer_and_its_exit_decides_what_follows(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    t = rig.topbar
    t.open_drawer()
    assert t.key(ecodes.BTN_SOUTH)
    assert t.key(ecodes.KEY_Q)  # unmapped: swallowed, not sent
    assert rig.procs[0].sent() == "a\n"
    rig.procs[0].rc = tb.DRAWER_START_GAME
    t.tick()
    assert rig.keys.events == [(ecodes.EV_KEY, ecodes.KEY_ENTER, -1)]  # start the game
    t.open_drawer()
    rig.procs[1].rc = 1
    t.tick()
    assert rig.spawned
    rig.spawned[-1]()
    assert rig.library.calls[-1] == "sync music"


def test_a_click_presses_the_button_under_it(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    t = rig.topbar
    t.click(700, 40)
    assert rig.library.calls == ["toggle music"]
    t.click(700, 200)  # under the bar
    rig.display.pill = None
    t.click(600, 40)  # not on the carousel, bar closed
    assert rig.library.calls == ["toggle music"]
    t.show(0)
    t.click(600, 40)  # the bar is open
    assert rig.library.calls[-1] == "toggle showcase"


def test_fifo_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rig = _rig(tmp_path)
    t = rig.topbar
    rig.display.pill = None
    t.command(["up"])  # not on the carousel: up goes to the engine
    assert rig.keys.events == [(ecodes.EV_KEY, ecodes.KEY_UP, -1)]
    t.command(["suspend"])  # never reaches the carousel: gives up after 1.5 s
    assert rig.slept == [0.05] * 30
    assert len(rig.procs) == 1
    t.command(["c_down", "left"])
    assert rig.procs[0].sent() == "c_down\nleft\n"
    rig.procs[0].rc = 0
    rig.display.pill = WHITE
    rig.slept.clear()
    t.command(["suspend"])
    assert rig.slept == []
    assert len(rig.procs) == 2  # a second drawer
    rig.procs[1].rc = 0
    rig.keys.events.clear()
    t.command(["up", "right", "toggle", "f5", "bogus"])
    assert t.selected == 1
    assert rig.keys.events == [(ecodes.EV_KEY, ecodes.KEY_F5, -1)]
    assert "toggle showcase" in rig.library.calls
    assert "unknown command 'bogus'" in capsys.readouterr().err


def test_each_frame_keeps_the_windows_in_step_with_the_screen(tmp_path: Path) -> None:
    now = [0.0]
    rig = _rig(tmp_path, now=now)
    t = rig.topbar
    t.tick()
    assert rig.library.calls == ["mute"]  # arriving on the carousel
    t.tick()
    assert rig.library.calls == ["mute"]
    t.overlay.show_toast("full", seconds=1.0)
    t.show(1)
    now[0] = 0.5
    t.tick()
    assert set(rig.display.shown) == {"pocket", "toast"}
    now[0] = 1.0
    t.tick()
    assert set(rig.display.shown) == {"pocket"}  # the toast's time is up
    t.overlay.display.show("pocket", b"stray")
    t.open = False
    t.tick()
    assert rig.display.shown == {}  # a pocket with the bar closed is removed
    t.show(1)
    t.overlay.show_toast("full")
    rig.display.bar = None  # a game or a menu
    t.tick()
    assert rig.display.shown == {}
    assert not t.open
    rig.display.bar = CHARCOAL
    t.open_drawer()
    t.tick()  # the drawer owns the screen
    assert rig.library.calls == ["mute"]


# ------------------------------------------------------------------ input


@dataclass
class _FakeDevice:
    name: str
    fd: int
    caps: dict[int, list[int]] = field(default_factory=dict[int, list[int]])
    events: list[evdev.InputEvent] = field(default_factory=list[evdev.InputEvent])
    grabbed: bool = False
    closed: bool = False
    fail_grab: bool = False
    fail_read: bool = False
    fail_close: bool = False
    drain: int | None = None  # a pipe to empty on read, so poll() settles

    def capabilities(self, *, absinfo: bool = True) -> Mapping[int, Sequence[int]]:
        assert not absinfo  # with absinfo, EV_ABS lists (code, AbsInfo) pairs
        return self.caps

    def grab(self) -> None:
        if self.fail_grab:
            raise OSError("busy")
        self.grabbed = True

    def ungrab(self) -> None:
        if self.fail_grab:
            raise OSError("busy")
        self.grabbed = False

    def close(self) -> None:
        if self.fail_close:
            raise OSError("already gone")
        self.closed = True

    def read(self) -> Iterable[evdev.InputEvent]:
        if self.drain is not None:
            os.read(self.drain, 64)
        if self.fail_read:
            raise OSError("unplugged")
        out, self.events = self.events, []
        return out


def test_keyboards_are_grabbed_mice_and_pads_watched_and_the_rest_closed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    kb = _FakeDevice("AT keyboard", 3, {ecodes.EV_KEY: [ecodes.KEY_UP, ecodes.KEY_A]})
    mouse = _FakeDevice("mouse", 4, {ecodes.EV_KEY: [ecodes.BTN_LEFT]})
    stick = _FakeDevice("joystick", 5, {ecodes.EV_ABS: [ecodes.ABS_X]})  # no D-Pad, no buttons
    ours = _FakeDevice(tb.UINPUT_NAME, 6, {ecodes.EV_KEY: [ecodes.KEY_UP]})
    stuck = _FakeDevice("stuck", 7, {ecodes.EV_KEY: [ecodes.KEY_UP]}, fail_grab=True)
    xbox = _FakeDevice("X-Box pad", 8, {ecodes.EV_ABS: [ecodes.ABS_X, ecodes.ABS_HAT0Y]})
    buttons = _FakeDevice("pad", 9, {ecodes.EV_KEY: [ecodes.BTN_SOUTH]})
    dpad = _FakeDevice("dpad", 10, {ecodes.EV_KEY: [ecodes.BTN_DPAD_UP]})
    devs = {d.name: d for d in (kb, mouse, stick, ours, stuck, xbox, buttons, dpad)}
    opened: list[str] = []

    def opener(path: str) -> tb.InputDevice:
        opened.append(path)
        if path == "gone":
            raise OSError("no such device")
        return devs[path]

    found = tb.open_inputs([*devs, "gone"], opener)
    assert list(found.keyboards) == [3]
    assert kb.grabbed
    assert list(found.mice) == [4]
    assert list(found.pads) == [8, 9, 10]
    assert not mouse.grabbed
    assert not xbox.grabbed  # pads are only held while the bar is open
    assert stick.closed
    assert ours.closed
    err = capsys.readouterr().err
    assert "input gone: no such device" in err
    assert "input stuck (stuck): busy" in err
    kinds = [tb.classify_device(d) for d in (kb, mouse, stick, ours, xbox, buttons, dpad)]
    assert kinds == ["keys", "mouse", None, None, "pad", "pad", "pad"]
    # A later scan opens only what's new.
    opened.clear()
    devs["new pad"] = _FakeDevice("new pad", 11, {ecodes.EV_ABS: [ecodes.ABS_HAT0Y]})
    assert found.scan([*devs, "gone"], opener) == [11]
    assert opened == ["new pad"]
    assert found.device(11) is devs["new pad"]
    assert found.device(3) is kb
    assert found.device(4) is mouse


def test_an_unplugged_device_is_forgotten_so_a_new_one_at_its_path_is_opened() -> None:
    pad = _FakeDevice("pad", 8, {ecodes.EV_ABS: [ecodes.ABS_HAT0Y]}, fail_close=True)
    found = tb.open_inputs(["event8"], lambda _p: pad)
    found.drop(8)  # its close fails: it's already gone
    assert (found.pads, found.seen) == ({}, {})
    again = _FakeDevice("pad", 9, {ecodes.EV_ABS: [ecodes.ABS_HAT0Y]})
    assert found.scan(["event8"], lambda _p: again) == [9]
    kb = _FakeDevice("kb", 3, {ecodes.EV_KEY: [ecodes.KEY_UP]})
    found.scan(["event3"], lambda _p: kb)
    found.drop(3)
    assert kb.closed
    assert found.keyboards == {}


def test_a_pad_is_held_only_while_the_daemon_owns_the_screen(
    capsys: pytest.CaptureFixture[str],
) -> None:
    dev = _FakeDevice("pad", 8)
    pad = tb.Pad(dev)
    pad.hold(want=True)
    assert (pad.held, dev.grabbed) == (True, True)
    pad.hold(want=True)  # no change
    pad.hold(want=False)
    assert (pad.held, dev.grabbed) == (False, False)
    dev.fail_grab = True
    pad.hold(want=True)
    assert not pad.held  # still the engine's
    assert "pad pad: busy" in capsys.readouterr().err


def _grabbed(dev: _FakeDevice) -> bool:
    return dev.grabbed  # read through a call: mypy would keep an assert's narrowing


def test_a_pad_changes_hands_only_with_nothing_pressed() -> None:
    """Grabbing between a press and its release left the engine holding Up for good (43.1 VM)."""
    dev = _FakeDevice("pad", 8)
    pad = tb.Pad(dev)
    abs_, key = ecodes.EV_ABS, ecodes.EV_KEY
    pad.track(_ev(abs_, ecodes.ABS_HAT0Y, -1))  # Up is down
    pad.track(_ev(abs_, ecodes.ABS_X, 30000))  # the stick isn't tracked
    pad.hold(want=True)
    assert not _grabbed(dev)  # the engine still has to see Up let go
    pad.track(_ev(key, ecodes.BTN_SOUTH, 1))
    pad.track(_ev(abs_, ecodes.ABS_HAT0Y, 0))
    pad.hold(want=True)
    assert not _grabbed(dev)  # A is still down
    pad.track(_ev(key, ecodes.BTN_SOUTH, 2))  # a repeat: still down
    pad.hold(want=True)
    assert not _grabbed(dev)
    pad.track(_ev(key, ecodes.BTN_SOUTH, 0))
    pad.hold(want=True)
    assert _grabbed(dev)
    pad.track(_ev(key, ecodes.BTN_EAST, 1))  # B closes the bar while held
    pad.hold(want=False)
    assert _grabbed(dev)  # released only after B is let go: the engine never sees B
    pad.track(_ev(key, ecodes.BTN_EAST, 0))
    pad.hold(want=False)
    assert not _grabbed(dev)


def _is_open(topbar: tb.Topbar) -> bool:
    return topbar.open  # read through a call: mypy would keep an assert's narrowing


def test_a_pads_d_pad_opens_the_bar_and_its_buttons_press_and_close(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    router = tb.Router(rig.topbar, rig.keys, lambda: (0.0, 0.0))
    abs_, key = ecodes.EV_ABS, ecodes.EV_KEY
    router.pad(_ev(abs_, ecodes.ABS_HAT0Y, -1))  # D-Pad Up on the carousel
    assert _is_open(rig.topbar)
    router.pad(_ev(abs_, ecodes.ABS_HAT0Y, 0))  # its release: nothing
    router.pad(_ev(abs_, ecodes.ABS_X, -32000))  # the stick: nothing
    router.pad(_ev(abs_, ecodes.ABS_HAT0X, 1))  # Right
    assert rig.topbar.selected == 1
    router.pad(_ev(key, ecodes.BTN_DPAD_LEFT, 1))  # Left, on a pad whose D-Pad is buttons
    assert rig.topbar.selected == 0
    router.pad(_ev(key, ecodes.BTN_DPAD_LEFT, 0))
    router.pad(_ev(key, ecodes.BTN_DPAD_LEFT, 2))  # releases and repeats: nothing
    assert rig.topbar.selected == 0
    router.pad(_ev(key, ecodes.BTN_EAST, 1))  # B closes
    assert not _is_open(rig.topbar)
    router.pad(_ev(abs_, ecodes.ABS_HAT0Y, -1))
    router.pad(_ev(abs_, ecodes.ABS_HAT0X, 1))
    router.pad(_ev(key, ecodes.BTN_SOUTH, 1))  # A presses Showcase/Full
    assert rig.library.calls == ["toggle showcase"]
    router.pad(_ev(abs_, ecodes.ABS_HAT0Y, 1))  # D-Pad Down on the carousel: the drawer
    assert rig.drawer.open
    router.pad(_ev(key, ecodes.BTN_SOUTH, 1))
    assert rig.procs[0].sent() == "a\n"
    assert rig.keys.events == []  # the engine reads the pad itself: nothing is re-sent


def test_keys_the_daemon_uses_never_reach_the_engine_others_pass_through(
    tmp_path: Path,
) -> None:
    rig = _rig(tmp_path)
    out = _FakeKeys()
    router = tb.Router(rig.topbar, out, lambda: (700.0, 40.0))
    key = ecodes.EV_KEY
    for ev in [
        _ev(key, ecodes.KEY_UP, 1),  # opens the bar: kept
        _ev(key, ecodes.KEY_UP, 2),
        _ev(key, ecodes.KEY_UP, 0),
        _ev(ecodes.EV_SYN, 0, 0),
        _ev(ecodes.EV_MSC, ecodes.MSC_SCAN, 7),
        _ev(key, ecodes.KEY_A, 2),  # a repeat while the bar is open: dropped
    ]:
        router.keyboard(ev)
    assert out.events == [(ecodes.EV_MSC, ecodes.MSC_SCAN, 7)]
    rig.topbar.close()
    # F3 toggles the library and leaves the bar closed: its repeats and release stay ours too.
    for ev in [_ev(key, ecodes.KEY_F3, 1), _ev(key, ecodes.KEY_F3, 2), _ev(key, ecodes.KEY_F3, 0)]:
        router.keyboard(ev)
    assert out.events == [(ecodes.EV_MSC, ecodes.MSC_SCAN, 7)]
    assert rig.library.calls == ["toggle showcase"]
    rig.library.calls.clear()
    out.events.clear()
    for ev in [_ev(key, ecodes.KEY_A, 1), _ev(key, ecodes.KEY_A, 2), _ev(key, ecodes.KEY_A, 0)]:
        router.keyboard(ev)
    assert out.events == [(key, ecodes.KEY_A, 1), (key, ecodes.KEY_A, 2), (key, ecodes.KEY_A, 0)]
    router.mouse(_ev(key, ecodes.BTN_LEFT, 0))
    router.mouse(_ev(key, ecodes.BTN_RIGHT, 1))
    router.mouse(_ev(ecodes.EV_REL, ecodes.REL_X, 1))
    assert rig.library.calls == []
    router.mouse(_ev(key, ecodes.BTN_LEFT, 1))
    assert rig.library.calls == ["toggle music"]


def test_the_virtual_keyboard_sends_events_and_taps() -> None:
    ui = _UI()
    slept: list[float] = []
    keys = tb.UInputKeys(ui, sleep=slept.append)
    keys.tap(ecodes.KEY_F5)
    assert ui.got == [
        (ecodes.EV_KEY, ecodes.KEY_F5, 1),
        "syn",
        (ecodes.EV_KEY, ecodes.KEY_F5, 0),
        "syn",
    ]
    assert slept == [0.06]


# ------------------------------------------------------------------ loop and start-up


def test_the_loop_reads_commands_and_devices_and_ticks_each_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rig = _rig(tmp_path)
    ticks: list[int] = []

    def tick(_self: tb.Topbar) -> None:
        ticks.append(1)

    monkeypatch.setattr(tb.Topbar, "tick", tick)
    fifo = tmp_path / "cmd"
    fifo.write_text("stale")  # replaced by a fresh FIFO
    fd = tb.open_fifo(fifo)
    kb_r, kb_w = os.pipe()
    ms_r, ms_w = os.pipe()
    try:
        kb = _FakeDevice("kb", kb_r, drain=kb_r, events=[_ev(ecodes.EV_KEY, ecodes.KEY_UP, 1)])
        mouse = _FakeDevice(
            "mouse", ms_r, drain=ms_r, events=[_ev(ecodes.EV_KEY, ecodes.BTN_LEFT, 1)]
        )
        inputs = tb.Inputs({kb_r: kb}, {ms_r: mouse})
        router = tb.Router(rig.topbar, rig.keys, lambda: (700.0, 40.0))
        os.write(fd, b"toggle\n")
        os.write(kb_w, b"x")
        os.write(ms_w, b"x")
        times = iter([0.0, 0.0, 0.0])
        tb.serve(rig.topbar, router, inputs, tb.Loop(fd, clock=lambda: next(times), rounds=1))
        assert ticks == [1]
        # In registration order: the keyboard's Up opened the bar, the click pressed the music
        # button (closing the bar), then the FIFO's toggle.
        assert rig.library.calls == ["toggle music", "toggle showcase"]
        assert not rig.topbar.open
        # Events arriving within a frame of the last tick don't tick again.
        os.write(ms_w, b"x")
        mouse.drain = None  # stays readable: events in both rounds
        times = iter([0.0, 0.010, 0.020, 0.030])
        tb.serve(rig.topbar, router, inputs, tb.Loop(fd, clock=lambda: next(times), rounds=2))
        assert ticks == [1, 1]
        os.read(ms_r, 64)
        # A quiet frame ticks without asking the clock.
        tb.serve(rig.topbar, router, inputs, tb.Loop(fd, clock=lambda: 1.0, rounds=1))
        assert ticks == [1, 1, 1]
        # A device that fails to read (unplugged) is dropped from the loop.
        os.write(kb_w, b"x")
        kb.drain = None
        kb.fail_read = True
        tb.serve(rig.topbar, router, inputs, tb.Loop(fd, clock=lambda: 1.0, rounds=2))
        assert capsys.readouterr().err.count("input kb: unplugged") == 1
        assert kb_r not in inputs.keyboards
        assert kb.closed

        def broken_read(_fd: int, _n: int) -> bytes:
            raise OSError("fifo gone")

        os.write(fd, b"up\n")
        monkeypatch.setattr(os, "read", broken_read)
        tb.serve(rig.topbar, router, tb.Inputs(), tb.Loop(fd, rounds=1))
        monkeypatch.undo()
        assert "command fifo: fifo gone" in capsys.readouterr().err
    finally:
        for f in (fd, kb_r, kb_w, ms_r, ms_w):
            os.close(f)


def test_the_loop_holds_pads_while_the_bar_is_open_and_finds_new_ones(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    fd = tb.open_fifo(tmp_path / "cmd")
    pad_r, pad_w = os.pipe()
    try:
        pad = _FakeDevice(
            "pad",
            pad_r,
            {ecodes.EV_ABS: [ecodes.ABS_HAT0Y]},
            drain=pad_r,
            events=[
                _ev(ecodes.EV_ABS, ecodes.ABS_HAT0Y, -1),
                _ev(ecodes.EV_ABS, ecodes.ABS_HAT0Y, 0),
            ],
        )
        router = tb.Router(rig.topbar, rig.keys, lambda: (0.0, 0.0))
        inputs = tb.Inputs()
        clock = iter([0.0, 2.0, 2.0, 2.0, 2.0])
        loop = tb.Loop(
            fd,
            clock=lambda: next(clock),
            rounds=1,
            list_inputs=lambda: ["event7"],
            open_input=lambda _p: pad,
        )
        os.write(pad_w, b"x")  # the pad's Up and its release arrive in the round that finds it
        tb.serve(rig.topbar, router, inputs, loop)
        assert list(inputs.pads) == [pad_r]
        assert rig.topbar.open
        assert pad.grabbed  # the engine mustn't see Left/Right/A under the open bar
        rig.topbar.close()
        tb.serve(rig.topbar, router, inputs, tb.Loop(fd, clock=lambda: 3.0, rounds=1))
        assert not pad.grabbed
    finally:
        for f in (fd, pad_r, pad_w):
            os.close(f)


def test_input_devices_are_the_event_nodes(tmp_path: Path) -> None:
    for name in ("event10", "event2", "mouse0", "js0"):
        (tmp_path / name).write_bytes(b"")
    assert tb.list_inputs(tmp_path) == [str(tmp_path / "event10"), str(tmp_path / "event2")]


@dataclass
class _UI:
    """A `tb.VirtualKeyboard` that records what it was sent."""

    got: list[tuple[int, int, int] | str] = field(default_factory=list[tuple[int, int, int] | str])

    def write(self, etype: int, code: int, value: int) -> None:
        self.got.append((etype, code, value))

    def syn(self) -> None:
        self.got.append("syn")


def _no_device(path: str) -> tb.InputDevice:
    raise AssertionError(f"no devices are listed, yet {path} was opened")


def _console(
    tmp_path: Path,
    *,
    x11: Callable[[], ctypes.CDLL] = _FakeX11,
    uinput: Callable[[], tb.VirtualKeyboard] = _UI,
    spawn: Callable[[Callable[[], None]], None] = lambda job: job(),
) -> tb.Console:
    engine = _FakeEngine()
    roms = tmp_path / "userdata" / strip.ROMS
    roms.mkdir(parents=True, exist_ok=True)
    (tmp_path / "proc").mkdir(exist_ok=True)
    return tb.Console(
        x11=x11,
        uinput=uinput,
        list_inputs=list[str],
        open_input=_no_device,
        proc=tmp_path / "proc",
        userdata=tmp_path / "userdata",
        fifo=tmp_path / "cmd",
        pid_file=tmp_path / "topbar.pid",
        settle_s=0.0,
        make_library=lambda u: tb.LibrarySync(
            u,
            engine=engine,
            run=_no_pactl,
            selected_file=tmp_path / "selected",
        ),
        spawn=spawn,
    )


def test_main_replaces_the_old_daemon_and_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        console = _console(tmp_path)
        (console.proc / str(old.pid)).mkdir()
        (console.proc / str(old.pid) / "cmdline").write_bytes(
            b"python3\0-m\0n64mini.console.topbar"
        )
        assert tb.main([], console=console, rounds=1) == 0
        assert old.wait(timeout=5) == -signal.SIGKILL
    finally:
        old.kill()
    assert console.pid_file.read_text() == str(os.getpid())
    assert console.fifo.is_fifo()
    assert "replaced 1 running daemon(s)" in capsys.readouterr().err


def test_main_fails_without_a_display_or_keyboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_lib() -> ctypes.CDLL:
        raise OSError("libX11.so.6: cannot open")

    def no_uinput() -> tb.VirtualKeyboard:
        raise evdev.UInputError("/dev/uinput: permission denied")

    for console, message in [
        (_console(tmp_path, x11=no_lib), "cannot load Xlib: libX11.so.6"),
        (_console(tmp_path, x11=lambda: _FakeX11(dpy=0)), "cannot open X display"),
        (_console(tmp_path, uinput=no_uinput), "cannot create the virtual keyboard"),
    ]:
        assert tb.main([], console=console, rounds=1) == 2
        assert message in capsys.readouterr().err

    def bad_art(_u: Path) -> bool:
        raise strip.StripError("brand.png unreadable")

    monkeypatch.setattr(strip, "sync_topbar", bad_art)
    assert tb.main([], console=_console(tmp_path), rounds=1) == 0
    assert "top-bar art: brand.png unreadable" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        tb.main(["--bogus"], console=_console(tmp_path))


def test_the_real_device_factories(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    made: list[tuple[object, object]] = []

    def cdll(name: str) -> ctypes.CDLL:
        opened.append(name)
        return _FakeX11()

    ui = _UI()

    def uinput(events: object, name: str) -> _UI:
        made.append((events, name))
        return ui

    monkeypatch.setattr(ctypes, "CDLL", cdll)
    monkeypatch.setattr(evdev, "UInput", uinput)
    console = tb.Console()
    console.x11()
    assert opened == ["libX11.so.6"]
    assert console.uinput() is ui
    assert made == [({ecodes.EV_KEY: list(range(1, 249))}, tb.UINPUT_NAME)]
    assert console.open_input is evdev.InputDevice


def test_the_daemon_reads_the_selection_where_the_launcher_writes_it() -> None:
    # It read /var/run/n64mini-selected while flip writes the temp-dir file, so its
    # "update the selected card first" ordering never applied (RELEASE_TODO B6).
    assert tb.LibrarySync(Path("/userdata")).selected_file == flip.SELECTED_FILE


def test_build_scales_clicks_to_the_design_and_jobs_run_in_threads(tmp_path: Path) -> None:
    x11 = _FakeX11(size=(1920, 1080))
    jobs: list[Callable[[], None]] = []
    topbar, router, inputs, fifo_fd = tb.build(
        _console(tmp_path, x11=lambda: x11, spawn=jobs.append)
    )
    try:
        x11.pointer_at = (960, 108)
        assert router.to_design() == (640.0, 72.0)
        assert len(jobs) == 1  # the first library sync, on the console's spawn
        assert topbar.spawn == jobs.append
        assert inputs.keyboards == {}
    finally:
        os.close(fifo_fd)
    done = threading.Event()
    tb.start_thread(done.set)
    assert done.wait(5)
