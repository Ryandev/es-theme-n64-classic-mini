"""``n64mini-capture-live``: drive a live EmulationStation over HTTP and screenshot it.

Tier 2 of the harness. It talks to ES's built-in HTTP server
(``127.0.0.1:1234``) and captures the window's client area through PowerShell
on the Windows host (WSL2 only).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Final, cast

from PIL import Image

from n64mini.console.engine import SYSTEM
from n64mini.console.flip import BADGES
from n64mini.paths import SCREENSHOTS_DIR, THEME_ART_DIR

ES_HTTP: Final = "http://127.0.0.1:1234"
POWERSHELL: Final = Path("/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe")
_CONTENT_TYPES: Final = frozenset({"text/plain", "application/json", "image/png"})
ES_INPUTS: Final = frozenset({"a", "b", "x", "y", "up", "down", "left", "right", "start", "select"})

# {out} is replaced with the Windows path of the output PNG.
# {out} is the Windows path of the output PNG; {pid} the engine's process id. PrintWindow asks
# Windows to render that window itself, so another window lying on top of it (a terminal, say)
# can't end up in the picture, as it could with a copy of the screen.
_CAPTURE_PS: Final = r"""
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32Cap {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
    [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
"@
# Without this, a scaled display hands PowerShell logical coordinates: wrong size.
[Win32Cap]::SetProcessDPIAware() | Out-Null
$proc = Get-Process -Id {pid} -ErrorAction SilentlyContinue
if (-not $proc -or $proc.MainWindowHandle -eq 0) { Write-Error "no window for pid {pid}"; exit 1 }
if (-not $proc.Responding) { Write-Error "EmulationStation is not responding yet"; exit 1 }
$hwnd = $proc.MainWindowHandle
$rc = New-Object Win32Cap+RECT
[Win32Cap]::GetClientRect($hwnd, [ref]$rc) | Out-Null
$w = $rc.Right - $rc.Left; $h = $rc.Bottom - $rc.Top
if ($w -le 0 -or $h -le 0) { Write-Error "window client area is empty"; exit 1 }
$bmp = New-Object System.Drawing.Bitmap($w, $h)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$dc = $g.GetHdc()
# 3 = PW_CLIENTONLY | PW_RENDERFULLCONTENT (the latter is needed for GPU-drawn windows)
$ok = [Win32Cap]::PrintWindow($hwnd, $dc, 3)
$g.ReleaseHdc($dc); $g.Dispose()
if (-not $ok) { Write-Error "PrintWindow failed"; exit 1 }
$bmp.Save("{out}", [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
Write-Output "CAPTURED:$w`x$h"
"""


class CaptureError(RuntimeError):
    """The ES window could not be captured."""


def es_request(
    endpoint: str,
    body: str | bytes | None = None,
    timeout: float = 3.0,
    content_type: str = "text/plain",
) -> str:
    """Send a GET (or a POST when ``body`` is given) to the ES HTTP server.

    The request goes through PowerShell on the Windows side: in WSL2's default (NAT) networking,
    127.0.0.1 inside WSL is not the Windows host, so a direct request is refused. A body goes in
    a temporary file passed with ``-InFile``: it never appears in the command, so nothing in it
    can be read as PowerShell, and its size isn't limited by the command line.
    """
    if not POWERSHELL.exists():
        raise CaptureError(f"{POWERSHELL} not found; the live harness needs WSL2 on Windows")
    if not re.fullmatch(r"/[A-Za-z0-9/_-]*", endpoint) or content_type not in _CONTENT_TYPES:
        raise ValueError(f"refusing to build a request for {endpoint!r} ({content_type})")
    with tempfile.TemporaryDirectory() as tmp:
        payload = ""
        if body is not None:
            body_file = Path(tmp) / "body"
            body_file.write_bytes(body.encode("utf-8") if isinstance(body, str) else body)
            payload = f" -InFile '{to_windows_path(body_file)}' -ContentType '{content_type}'"
        method = "POST" if body is not None else "GET"
        script = (
            "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
            f"(Invoke-WebRequest -UseBasicParsing -TimeoutSec {int(timeout)} -Method {method} "
            f"-Uri '{ES_HTTP}{endpoint}'{payload}).Content"
        )
        res = _run(
            [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", script], timeout + 15
        )
    if res.returncode != 0:
        raise CaptureError(f"{method} {endpoint} failed: {res.stderr.strip()}")
    return res.stdout


def flip_to_port(rom: str) -> str:
    """Give ``rom``'s card the Port badge in the running engine, as n64mini-flip does.

    The game is found in the engine's own game list, so the id is the engine's. The badge is
    uploaded as the game's ``cartridge`` image. Returns the id.
    """
    games: object = json.loads(es_request(f"/systems/{SYSTEM}/games"))
    entries = cast("list[dict[str, object]]", games) if isinstance(games, list) else []
    for entry in entries:
        if re.split(r"[\\/]", str(entry.get("path", "")))[-1] == rom:
            ident = str(entry["id"])
            png = (THEME_ART_DIR / BADGES["port"]).read_bytes()
            es_request(
                f"/systems/{SYSTEM}/games/{ident}/media/cartridge", png, content_type="image/png"
            )
            return ident
    raise CaptureError(f"{rom!r} isn't in the engine's {SYSTEM} game list")


def parse_inputs(spec: str) -> list[str]:
    """Split a comma-separated input list and reject unknown buttons."""
    buttons = [b.strip().lower() for b in spec.split(",") if b.strip()]
    unknown = [b for b in buttons if b not in ES_INPUTS]
    if unknown:
        raise ValueError(f"unknown ES input(s): {', '.join(unknown)}")
    return buttons


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed executables, no shell
        args,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def to_windows_path(path: Path) -> str:
    """Convert a WSL path to its Windows form via ``wslpath -w``."""
    res = _run(["wslpath", "-w", str(path.resolve())], timeout=5)
    if res.returncode != 0:
        raise CaptureError(f"wslpath failed: {res.stderr.strip()}")
    return res.stdout.strip()


def check_capture(out: Path, expected: tuple[int, int]) -> None:
    """Fail unless ``out`` is a non-blank image of the expected size.

    A capture of the wrong window, or a GPU window that rendered nothing, still produces a PNG,
    and a run that checks nothing would read as a pass.
    """
    with Image.open(out) as img:
        size = img.size
        lo, hi = zip(*img.convert("RGB").getextrema(), strict=True)
    if size != expected:
        raise CaptureError(f"captured {size[0]}x{size[1]}, expected {expected[0]}x{expected[1]}")
    if lo == hi:
        raise CaptureError("captured a blank image")


def capture_window(out: Path, pid: int, expected: tuple[int, int] = (1280, 720)) -> str:
    """Screenshot the client area of process ``pid``'s window; returns ``CAPTURED:WxH``."""
    if not POWERSHELL.exists():
        raise CaptureError(f"{POWERSHELL} not found; live capture needs WSL2 on Windows")
    script = _CAPTURE_PS.replace("{out}", to_windows_path(out)).replace("{pid}", str(pid))
    res = _run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", script], timeout=15)
    if res.returncode != 0:
        raise CaptureError(f"window capture failed: {res.stderr.strip()}")
    check_capture(out, expected)
    return res.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns a process exit code."""
    parser = argparse.ArgumentParser(description="Capture a screenshot of a live EmulationStation.")
    parser.add_argument("--reload", action="store_true", help="GET /reloadgames first")
    parser.add_argument("--navigate", default="", help="comma-separated inputs, e.g. 'a,right'")
    parser.add_argument("--out", type=Path, default=SCREENSHOTS_DIR / "live_es_capture.png")
    parser.add_argument("--pid", type=int, required=True, help="the engine's Windows process id")
    parser.add_argument(
        "--flip", metavar="ROM", help="after the inputs, give this game the Port badge"
    )
    parser.add_argument(
        "--settle", type=float, default=0.0, help="seconds to wait after the inputs"
    )
    args = parser.parse_args(argv)
    out: Path = args.out
    reload: bool = args.reload
    try:
        buttons = parse_inputs(args.navigate)
    except ValueError as exc:
        parser.error(str(exc))

    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        if reload:
            print("reloading theme and games (GET /reloadgames)")
            es_request("/reloadgames")
            time.sleep(0.8)
        for button in buttons:
            print(f"input: {button}")
            es_request("/input", button)
            time.sleep(0.4)
        time.sleep(args.settle)  # e.g. a launch through wsl.exe takes a moment to start
        if args.flip:
            print(f"flip: {args.flip} -> port")
            flip_to_port(args.flip)
            time.sleep(0.6)
        info = capture_window(out, args.pid)
    except CaptureError as exc:
        print(f"error: {exc}")
        return 1
    print(f"{info} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
