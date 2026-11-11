#!/bin/bash
# Background daemon for the N64 Classic Mini top-bar buttons and D-Pad Up/Down carousel actions:
# - D-Pad Up on the carousel focuses the 3 top-bar buttons (Settings, Showcase/Full, BG Music),
#   drawing an LED-highlighted selection border over the recessed pocket via X11.
# - Left / Right moves across the 3 top-bar buttons; A/Enter triggers the selected button
#   in-place without restarting EmulationStation; Down / B / Esc returns to the game carousel.
# - D-Pad Down on the carousel (or Reset inside an emulator game) opens the 4-slot NUS-006
#   Suspend Point List drawer (n64mini.console.suspend).
set -euo pipefail
root="$(cd "$(dirname "$(readlink -f "$0")")/../../.." && pwd)"
export PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}"

pid_file="/var/run/n64mini-topbar.pid"
if [ -f "$pid_file" ]; then
    old_pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [ -n "$old_pid" ]; then
        kill -9 "$old_pid" 2>/dev/null || true
    fi
fi
for cmdline in /proc/[0-9]*/cmdline; do
    [ -f "$cmdline" ] || continue
    if tr '\0' ' ' <"$cmdline" 2>/dev/null | grep -q "n64mini-topbar-daemon"; then
        proc_dir="${cmdline%/cmdline}"
        kill -9 "${proc_dir#/proc/}" 2>/dev/null || true
    fi
done
rm -f /var/run/n64mini-topbar.cmd

python3 -c "from pathlib import Path; from n64mini.console.strip import sync_topbar; sync_topbar(Path('/userdata'))" || true

DISPLAY="${DISPLAY:-:0.0}" nohup python3 -c '# n64mini-topbar-daemon
import ctypes, os, re, select, subprocess, threading, time
from pathlib import Path
import evdev
from evdev import ecodes
from PIL import Image
from n64mini.console import library, showcase, strip
from n64mini.console.engine import Engine, EngineError
from n64mini.console.showcase import GAME_RE, element_text, entry_path

Path("/var/run/n64mini-topbar.pid").write_text(str(os.getpid()), encoding="utf-8")

USERDATA = Path("/userdata")
POCKET_W, POCKET_H = 341, 73
TOAST_W, TOAST_H = 336, 94
TOPBAR_DIR = Path("/userdata/system/n64mini/topbar")
THEME_ART = Path("/userdata/themes/n64-classic-mini/art")
ROMS_DIR = Path("/userdata/roms/n64")
MUSIC_FULL = Path("/userdata/themes/n64-classic-mini/music/theme.full.wav")
CMD_FIFO = Path("/var/run/n64mini-topbar.cmd")

class XImage(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_int), ("height", ctypes.c_int), ("xoffset", ctypes.c_int),
        ("format", ctypes.c_int), ("data", ctypes.c_void_p), ("byte_order", ctypes.c_int),
        ("bitmap_unit", ctypes.c_int), ("bitmap_bit_order", ctypes.c_int), ("bitmap_pad", ctypes.c_int),
        ("depth", ctypes.c_int), ("bytes_per_line", ctypes.c_int), ("bits_per_pixel", ctypes.c_int),
        ("red_mask", ctypes.c_ulong), ("green_mask", ctypes.c_ulong), ("blue_mask", ctypes.c_ulong),
    ]

class XSetWindowAttributes(ctypes.Structure):
    _fields_ = [
        ("background_pixmap", ctypes.c_ulong), ("background_pixel", ctypes.c_ulong),
        ("border_pixmap", ctypes.c_ulong), ("border_pixel", ctypes.c_ulong),
        ("bit_gravity", ctypes.c_int), ("win_gravity", ctypes.c_int),
        ("backing_store", ctypes.c_int), ("backing_planes", ctypes.c_ulong),
        ("backing_pixel", ctypes.c_ulong), ("save_under", ctypes.c_int),
        ("event_mask", ctypes.c_long), ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int), ("colormap", ctypes.c_ulong),
        ("cursor", ctypes.c_ulong),
    ]

x11 = ctypes.CDLL("libX11.so.6")
x11.XOpenDisplay.restype = ctypes.c_void_p
x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
x11.XDefaultRootWindow.restype = ctypes.c_ulong
x11.XDefaultGC.argtypes = [ctypes.c_void_p, ctypes.c_int]
x11.XDefaultGC.restype = ctypes.c_void_p
x11.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
x11.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
x11.XDefaultDepth.argtypes = [ctypes.c_void_p, ctypes.c_int]
x11.XDefaultDepth.restype = ctypes.c_int
x11.XGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]
x11.XGetImage.restype = ctypes.POINTER(XImage)
x11.XDestroyImage.argtypes = [ctypes.POINTER(XImage)]
x11.XCreatePixmap.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
x11.XCreatePixmap.restype = ctypes.c_ulong
x11.XCreateSimpleWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_ulong]
x11.XCreateSimpleWindow.restype = ctypes.c_ulong
x11.XChangeWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(XSetWindowAttributes)]
x11.XSetWindowBackgroundPixmap.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
x11.XClearWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
x11.XMapRaised.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
x11.XUnmapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
x11.XPutImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.POINTER(XImage), ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
x11.XFlush.argtypes = [ctypes.c_void_p]
x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
x11.XWarpPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.c_int]
x11.XQueryPointer.argtypes = [
    ctypes.c_void_p, ctypes.c_ulong,
    ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
    ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
    ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
    ctypes.POINTER(ctypes.c_uint),
]

dpy = x11.XOpenDisplay(b":0.0")
root_win = x11.XDefaultRootWindow(dpy)
gc = x11.XDefaultGC(dpy, 0)
sw = x11.XDisplayWidth(dpy, 0) or 1280
sh = x11.XDisplayHeight(dpy, 0) or 720
x11.XWarpPointer(dpy, 0, root_win, 0, 0, 0, 0, sw - 1, sh - 1)
x11.XFlush(dpy)

def mouse_xy() -> tuple[int, int]:
    r_ret, c_ret = ctypes.c_ulong(), ctypes.c_ulong()
    rx, ry, wx_p, wy_p = ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
    mask = ctypes.c_uint()
    x11.XQueryPointer(
        dpy, root_win, ctypes.byref(r_ret), ctypes.byref(c_ret),
        ctypes.byref(rx), ctypes.byref(ry), ctypes.byref(wx_p), ctypes.byref(wy_p),
        ctypes.byref(mask),
    )
    return rx.value, ry.value

def pixel_rgb(x720: int, y720: int) -> tuple[int, int, int] | None:
    im = x11.XGetImage(dpy, root_win, round(x720 * sw / 1280), round(y720 * sh / 720), 1, 1, 0xFFFFFFFF, 2)
    if not im:
        return None
    b = ctypes.string_at(im.contents.data, 4)
    x11.XDestroyImage(im)
    return b[2], b[1], b[0]

def screen_state() -> str:
    bar = pixel_rgb(200, 40)
    if bar is None:
        return "other"
    if bar[0] >= 45 and bar[1] >= 48 and bar[2] >= 54:
        pill = pixel_rgb(300, 105)
        if pill is not None and pill[0] > 180 and pill[1] > 180 and pill[2] > 180:
            return "carousel"
    return "other"

def is_on_carousel() -> bool:
    return screen_state() == "carousel"

wx = round(470 * sw / 1280)
wy = round(10 * sh / 720)
ww = round(POCKET_W * sw / 1280)
wh = round(POCKET_H * sh / 720)
depth = x11.XDefaultDepth(dpy, 0) or 24
overlay_win = x11.XCreateSimpleWindow(dpy, root_win, wx, wy, ww, wh, 0, 0, 0)
attrs = XSetWindowAttributes(override_redirect=1)
x11.XChangeWindowAttributes(dpy, overlay_win, 1 << 9, ctypes.byref(attrs))
xim = x11.XGetImage(dpy, root_win, 0, 0, ww, wh, 0xFFFFFFFF, 2)
overlay_mapped = False
current_pocket: str | None = None
pocket_pixmaps: dict[str, tuple[int, int]] = {}
base_bgra: bytes | None = None

tx = round(472 * sw / 1280)
ty = round(83 * sh / 720)
tw = round(TOAST_W * sw / 1280)
th = round(TOAST_H * sh / 720)
toast_win = x11.XCreateSimpleWindow(dpy, root_win, tx, ty, tw, th, 0, 0, 0)
x11.XChangeWindowAttributes(dpy, toast_win, 1 << 9, ctypes.byref(attrs))
toast_xim = x11.XGetImage(dpy, root_win, 0, 0, tw, th, 0xFFFFFFFF, 2)
toast_pm = x11.XCreatePixmap(dpy, root_win, tw, th, depth)
toast_mapped = False
toast_until = 0.0

def hide_toast() -> None:
    global toast_mapped, toast_until
    toast_until = 0.0
    if toast_mapped:
        x11.XUnmapWindow(dpy, toast_win)
        x11.XFlush(dpy)
        toast_mapped = False

def show_toast(mode: str, duration: float = 2.2) -> None:
    global toast_mapped, toast_until
    png = THEME_ART / f"toast_{mode}.png"
    if not png.is_file() or not toast_xim or not toast_pm:
        return
    if toast_mapped:
        x11.XUnmapWindow(dpy, toast_win)
        x11.XSync(dpy, 0)
        toast_mapped = False
    bg_im = x11.XGetImage(dpy, root_win, tx, ty, tw, th, 0xFFFFFFFF, 2)
    if bg_im:
        raw_bg = bytearray(ctypes.string_at(bg_im.contents.data, tw * th * 4))
        x11.XDestroyImage(bg_im)
        raw_bg[0::4], raw_bg[2::4] = raw_bg[2::4], raw_bg[0::4]
        raw_bg[3::4] = b"\xff" * (tw * th)
        frame = Image.frombytes("RGBA", (tw, th), bytes(raw_bg))
    else:
        frame = Image.new("RGBA", (tw, th), (26, 30, 38, 255))
    with Image.open(png) as f:
        fg = f.convert("RGBA")
    if (tw, th) != fg.size:
        fg = fg.resize((tw, th), Image.Resampling.LANCZOS)
    frame.alpha_composite(fg, (0, 0))
    out_bgra = bytearray(frame.tobytes())
    out_bgra[0::4], out_bgra[2::4] = out_bgra[2::4], out_bgra[0::4]
    ctypes.memmove(toast_xim.contents.data, bytes(out_bgra), tw * th * 4)
    x11.XPutImage(dpy, toast_pm, gc, toast_xim, 0, 0, 0, 0, tw, th)
    x11.XSetWindowBackgroundPixmap(dpy, toast_win, toast_pm)
    x11.XClearWindow(dpy, toast_win)
    x11.XMapRaised(dpy, toast_win)
    x11.XFlush(dpy)
    toast_mapped = True
    toast_until = time.monotonic() + duration

def _scaled_bgra(raw_file: Path) -> bytearray:
    img = Image.frombytes("RGBA", (POCKET_W, POCKET_H), raw_file.read_bytes())
    if (ww, wh) != (POCKET_W, POCKET_H):
        img = img.resize((ww, wh), Image.Resampling.BILINEAR)
    rgba = bytearray(img.tobytes())
    rgba[0::4], rgba[2::4] = rgba[2::4], rgba[0::4]
    return rgba

def get_pocket_pixmap(name: str) -> int:
    raw_file = TOPBAR_DIR / f"pocket-{name}.rgba"
    if not raw_file.is_file() or not xim:
        return 0
    mtime = raw_file.stat().st_mtime_ns
    cached = pocket_pixmaps.get(name)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    sel = _scaled_bgra(raw_file)
    other_name = "1" if name == "0" else "0"
    other_file = TOPBAR_DIR / f"pocket-{other_name}.rgba"
    if base_bgra is not None and other_file.is_file():
        unsel = _scaled_bgra(other_file)
        out = bytearray(base_bgra)
        x_lo = 0 if name == "0" else (ww // 3 if name == "1" else (2 * ww) // 3)
        x_hi = ww // 3 if name == "0" else ((2 * ww) // 3 if name == "1" else ww)
        for y in range(wh):
            row = y * ww * 4
            for x in range(x_lo, x_hi):
                i = row + x * 4
                if sel[i:i + 3] != unsel[i:i + 3]:
                    out[i:i + 4] = sel[i:i + 4]
        buf = bytes(out)
    else:
        buf = bytes(sel)
    ctypes.memmove(xim.contents.data, buf, ww * wh * 4)
    pm = cached[1] if cached is not None else x11.XCreatePixmap(dpy, root_win, ww, wh, depth)
    x11.XPutImage(dpy, pm, gc, xim, 0, 0, 0, 0, ww, wh)
    pocket_pixmaps[name] = (mtime, pm)
    return pm

for _pocket_idx in ("0", "1", "2"):
    get_pocket_pixmap(_pocket_idx)

def draw_pocket(name: str, *, force: bool = False) -> None:
    global overlay_mapped, current_pocket, base_bgra
    if overlay_mapped and current_pocket == name and not force:
        return
    if not overlay_mapped:
        live = x11.XGetImage(dpy, root_win, wx, wy, ww, wh, 0xFFFFFFFF, 2)
        if live:
            snap = ctypes.string_at(live.contents.data, ww * wh * 4)
            x11.XDestroyImage(live)
            if snap != base_bgra:
                base_bgra = snap
                for _k, (_m, _pm) in list(pocket_pixmaps.items()):
                    pocket_pixmaps[_k] = (-1, _pm)
    pm = get_pocket_pixmap(name)
    if not pm:
        return
    current_pocket = name
    x11.XSetWindowBackgroundPixmap(dpy, overlay_win, pm)
    x11.XClearWindow(dpy, overlay_win)
    if not overlay_mapped:
        x11.XMapRaised(dpy, overlay_win)
        overlay_mapped = True
    x11.XFlush(dpy)

def unmap_overlay() -> None:
    global overlay_mapped, topbar_open, current_pocket
    topbar_open = False
    current_pocket = None
    if overlay_mapped:
        x11.XUnmapWindow(dpy, overlay_win)
        x11.XFlush(dpy)
        overlay_mapped = False

def sync_es_music_mute() -> None:
    muted = "1" if MUSIC_FULL.is_file() else "0"
    try:
        out = subprocess.check_output(["pactl", "list", "sink-inputs"], text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return
    for block in out.split("Sink Input #")[1:]:
        sid = block.splitlines()[0].strip()
        if "emulationstation" in block and "44100Hz" in block:
            subprocess.run(["pactl", "set-sink-input-mute", sid, muted], check=False)

lib_lock = threading.Lock()
last_sent: dict[str, tuple[str, str]] = {}

def sync_es_state_live(*, music_only: bool = False) -> None:
    with lib_lock:
        gamelist = ROMS_DIR / "gamelist.xml"
        text = gamelist.read_text(encoding="utf-8") if gamelist.is_file() else ""
        hidden_roms: set[str] = set()
        for m in GAME_RE.finditer(text):
            block = m.group(0)
            if element_text(block, "hidden") == "true":
                hidden_roms.add(Path(entry_path(block)).name)
        showcase_on = (ROMS_DIR / "gamelist.xml.showcase.json").is_file()
        topbar_png = str(strip.topbar_brand_path(USERDATA))
        selected_rom = ""
        try:
            sel_file = Path("/var/run/n64mini-selected")
            if sel_file.is_file():
                selected_rom = Path(sel_file.read_text(encoding="utf-8").strip()).name
        except OSError:
            pass
        eng = Engine()
        roms = [
            rom for rom in sorted(ROMS_DIR.iterdir())
            if rom.is_file() and not rom.name.startswith(".") and rom.suffix.lower() in {".z64", ".n64", ".v64"}
        ]
        # Update visible ROMs (starting with the active card) first so the top-bar icon flips immediately
        roms.sort(key=lambda r: (r.name in hidden_roms, r.name != selected_rom, r.name))
        for rom in roms[:3]:
            if rom.name not in hidden_roms:
                try:
                    eng.set_metadata(str(rom), {"hidden": "false", "multidisk": topbar_png})
                except (EngineError, OSError):
                    pass
        if not music_only:
            try:
                if showcase_on:
                    strip.generate(ROMS_DIR, USERDATA / strip.STRIPS, strip.extensions(USERDATA / strip.SYSTEMS_CFG))
                else:
                    strip.clear(USERDATA / strip.STRIPS)
            except (strip.StripError, OSError):
                pass
        for rom in roms:
            is_hidden = rom.name in hidden_roms
            if is_hidden and music_only:
                continue
            want_hidden = "true" if is_hidden else "false"
            want_multi = last_sent.get(rom.name, ("", ""))[1] if is_hidden else topbar_png
            state_pair = (want_hidden, want_multi)
            if last_sent.get(rom.name) == state_pair:
                continue
            fields = {"hidden": "true"} if is_hidden else {"hidden": "false", "multidisk": topbar_png}
            try:
                eng.set_metadata(str(rom), fields)
            except (EngineError, OSError):
                pass
            last_sent[rom.name] = state_pair

sync_es_music_mute()
threading.Thread(target=sync_es_state_live, daemon=True).start()

if CMD_FIFO.exists():
    CMD_FIFO.unlink()
os.mkfifo(CMD_FIFO)
fifo_fd = os.open(str(CMD_FIFO), os.O_RDWR | os.O_NONBLOCK)

keys_all = list(range(1, 249))
ui = evdev.UInput(name="n64mini-topbar-kb", events={ecodes.EV_KEY: keys_all})
time.sleep(0.5)

def tap_key(code: int) -> None:
    ui.write(ecodes.EV_KEY, code, 1)
    ui.syn()
    time.sleep(0.06)
    ui.write(ecodes.EV_KEY, code, 0)
    ui.syn()

devices: dict[int, evdev.InputDevice] = {}
mouse_fds: set[int] = set()
for path in evdev.list_devices():
    try:
        dev = evdev.InputDevice(path)
        if dev.name in ("batocera hotkeys", "n64mini-topbar-kb", "Power Button"):
            dev.close()
            continue
        caps = dev.capabilities()
        if ecodes.EV_KEY in caps and ecodes.KEY_UP in caps[ecodes.EV_KEY]:
            dev.grab()
            devices[dev.fd] = dev
        elif ecodes.EV_KEY in caps and ecodes.BTN_LEFT in caps[ecodes.EV_KEY]:
            devices[dev.fd] = dev
            mouse_fds.add(dev.fd)
        else:
            dev.close()
    except OSError:
        pass

topbar_open = False
selected = 0
suspend_proc: subprocess.Popen[str] | None = None

SUSPEND_KEY_MAP = {
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

def open_suspend() -> None:
    global suspend_proc
    if suspend_proc is not None and suspend_proc.poll() is None:
        return
    hide_toast()
    unmap_overlay()
    x11.XSync(dpy, 0)
    try:
        suspend_proc = subprocess.Popen(
            ["python3", "-m", "n64mini.console.suspend"],
            stdin=subprocess.PIPE,
            text=True,
        )
    except OSError:
        suspend_proc = None

def send_suspend(token: str) -> None:
    if suspend_proc is not None and suspend_proc.poll() is None and suspend_proc.stdin is not None:
        try:
            suspend_proc.stdin.write(token + "\n")
            suspend_proc.stdin.flush()
        except OSError:
            pass

def reap_suspend() -> None:
    global suspend_proc
    if suspend_proc is not None and suspend_proc.poll() is not None:
        rc = suspend_proc.returncode
        suspend_proc = None
        if rc == 10:
            tap_key(ecodes.KEY_ENTER)
        else:
            threading.Thread(target=lambda: sync_es_state_live(music_only=True), daemon=True).start()

def show_topbar(idx: int) -> None:
    global topbar_open, selected
    selected = idx % 3
    topbar_open = True
    draw_pocket(str(selected))

def hide_topbar() -> None:
    unmap_overlay()

def _sync_library_worker() -> None:
    global toast_until
    sync_es_state_live()
    if toast_mapped:
        toast_until = max(toast_until, time.monotonic() + 1.2)

def toggle_library_live() -> None:
    global base_bgra
    unmap_overlay()
    base_bgra = None
    try:
        showcase.switch("toggle", ROMS_DIR / "gamelist.xml", ROMS_DIR)
        strip.sync_topbar(USERDATA)
        for _p in ("0", "1", "2"):
            get_pocket_pixmap(_p)
    except (showcase.ShowcaseError, strip.StripError, OSError):
        pass
    mode = "showcase" if (ROMS_DIR / "gamelist.xml.showcase.json").is_file() else "full"
    show_toast(mode, duration=2.2)
    threading.Thread(target=_sync_library_worker, daemon=True).start()

def trigger_selected() -> None:
    idx = selected
    unmap_overlay()
    if idx == 0:
        hide_toast()
        tap_key(ecodes.KEY_SPACE)
    elif idx == 1:
        toggle_library_live()
    elif idx == 2:
        try:
            library.toggle_music(USERDATA, reload=False)
            for _p in ("0", "1", "2"):
                get_pocket_pixmap(_p)
        except OSError:
            pass
        sync_es_music_mute()
        threading.Thread(target=lambda: sync_es_state_live(music_only=True), daemon=True).start()

def handle_key_press(code: int) -> bool:
    reap_suspend()
    if suspend_proc is not None:
        mapped = SUSPEND_KEY_MAP.get(code)
        if mapped is not None:
            send_suspend(mapped)
        return True
    if topbar_open:
        if code == ecodes.KEY_LEFT:
            show_topbar(selected - 1)
        elif code == ecodes.KEY_RIGHT:
            show_topbar(selected + 1)
        elif code == ecodes.KEY_F3:
            toggle_library_live()
        elif code in (ecodes.KEY_DOWN, ecodes.KEY_ESC, ecodes.KEY_BACKSPACE, ecodes.KEY_B):
            hide_topbar()
        elif code in (ecodes.KEY_ENTER, ecodes.KEY_SPACE, ecodes.KEY_A, ecodes.KEY_Z):
            trigger_selected()
        return True
    if is_on_carousel():
        if code == ecodes.KEY_UP:
            show_topbar(selected)
            return True
        if code == ecodes.KEY_DOWN:
            open_suspend()
            return True
        if code == ecodes.KEY_F3:
            toggle_library_live()
            return True
    return False

prev_state = "other"
last_state_sync = 0.0

def sync_screen_state() -> None:
    global prev_state, last_state_sync
    reap_suspend()
    if suspend_proc is not None:
        return
    last_state_sync = time.monotonic()
    st = screen_state()
    if st == "carousel":
        if prev_state != "carousel":
            sync_es_music_mute()
        if toast_mapped and time.monotonic() >= toast_until:
            hide_toast()
        if topbar_open:
            draw_pocket(str(selected))
        else:
            unmap_overlay()
    else:
        hide_toast()
        unmap_overlay()
    prev_state = st

suppressed: set[int] = set()
poll = select.poll()
for fd in devices:
    poll.register(fd, select.POLLIN)
poll.register(fifo_fd, select.POLLIN)

while True:
    events = poll.poll(16)
    if not events or time.monotonic() - last_state_sync >= 0.016:
        sync_screen_state()
        if not events:
            continue
    for fd, _ in events:
        if fd == fifo_fd:
            try:
                cmd = os.read(fifo_fd, 256).decode("utf-8", errors="ignore").strip()
            except OSError:
                cmd = ""
            fifo_keys = {
                "up": ecodes.KEY_UP,
                "down": ecodes.KEY_DOWN,
                "left": ecodes.KEY_LEFT,
                "right": ecodes.KEY_RIGHT,
                "enter": ecodes.KEY_ENTER,
                "esc": ecodes.KEY_ESC,
            }
            suspend_tokens = {
                "left", "right", "c_down", "save", "c_right", "copy",
                "c_left", "delete", "a", "enter", "b", "esc", "start",
            }
            for token in cmd.split():
                reap_suspend()
                if token == "suspend":
                    for _ in range(30):
                        if is_on_carousel():
                            break
                        time.sleep(0.05)
                    open_suspend()
                elif token == "toggle":
                    toggle_library_live()
                elif suspend_proc is not None and token in suspend_tokens:
                    send_suspend(token)
                elif token in fifo_keys:
                    kcode = fifo_keys[token]
                    if not handle_key_press(kcode):
                        tap_key(kcode)
                elif token == "f5":
                    tap_key(ecodes.KEY_F5)
                    threading.Thread(target=sync_es_state_live, daemon=True).start()
            continue
        dev = devices.get(fd)
        if dev is None:
            continue
        try:
            for ev in dev.read():
                if fd in mouse_fds:
                    if ev.type == ecodes.EV_KEY and ev.code == ecodes.BTN_LEFT and ev.value == 1:
                        mx, my = mouse_xy()
                        x720, y720 = mx * 1280 / sw, my * 720 / sh
                        if 10 <= y720 <= 82 and 470 <= x720 <= 810 and (topbar_open or is_on_carousel()):
                            selected = 0 if x720 < 592 else (1 if x720 < 688 else 2)
                            trigger_selected()
                    continue
                if ev.type == ecodes.EV_KEY:
                    if ev.value == 1:
                        if handle_key_press(ev.code):
                            suppressed.add(ev.code)
                        else:
                            ui.write(ecodes.EV_KEY, ev.code, 1)
                            ui.syn()
                    elif ev.value == 2:
                        if ev.code not in suppressed and not topbar_open:
                            ui.write(ecodes.EV_KEY, ev.code, 2)
                            ui.syn()
                    elif ev.value == 0:
                        if ev.code in suppressed:
                            suppressed.discard(ev.code)
                        else:
                            ui.write(ecodes.EV_KEY, ev.code, 0)
                            ui.syn()
                elif ev.type != ecodes.EV_SYN:
                    ui.write(ev.type, ev.code, ev.value)
                    ui.syn()
        except OSError:
            pass
' </dev/null >/userdata/system/logs/n64mini-topbar.log 2>&1 &
