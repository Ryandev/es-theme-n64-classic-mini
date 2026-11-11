"""1600x980 visual diff report: reference, actual, heatmap, wireframe and zone scores."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from PIL import Image, ImageDraw

from n64mini.harness.metrics import ZONES, Metrics, heatmap
from n64mini.harness.render import SCREEN_H
from n64mini.theme_assets.fonts import Face, font

if TYPE_CHECKING:
    from pathlib import Path

type RGB = tuple[int, int, int]

REPORT_W: Final = 1600
REPORT_H: Final = 980
_PANEL_W: Final = 760
_PANEL_H: Final = 428
PASS_PCT: Final = 92.0
WARN_PCT: Final = 82.0


def _status(similarity: float) -> tuple[str, RGB]:
    if similarity >= PASS_PCT:
        return "PASS", (52, 211, 153)
    if similarity >= WARN_PCT:
        return "WARN", (251, 191, 36)
    return "DIFF", (248, 113, 113)


def _badge_color(similarity: float) -> RGB:
    if similarity >= 94.0:
        return (34, 197, 94)
    if similarity >= 85.0:
        return (245, 158, 11)
    return (239, 68, 68)


def _panel(  # noqa: PLR0913 - keyword-only panel styling
    board: Image.Image,
    xy: tuple[int, int],
    *,
    title: str,
    title_col: RGB,
    image: Image.Image,
    border: RGB,
    resample: Image.Resampling = Image.Resampling.LANCZOS,
) -> None:
    x, y = xy
    draw = ImageDraw.Draw(board)
    draw.text((x, y), title, font=font(Face.SANS_BOLD, 15), fill=title_col)
    board.paste(image.resize((_PANEL_W, _PANEL_H), resample), (x, y + 22))
    draw.rectangle((x, y + 22, x + _PANEL_W, y + 22 + _PANEL_H), outline=border, width=2)


def write_report(  # noqa: PLR0913 - keyword-only inputs, one per report panel
    out_path: Path,
    *,
    reference: Image.Image,
    reference_name: str,
    actual: Image.Image,
    wireframe: Image.Image | None,
    diff: Image.Image,
    metrics: Metrics,
) -> None:
    """Compose the 1600x980 diff report PNG at ``out_path``."""
    board = Image.new("RGB", (REPORT_W, REPORT_H), (16, 20, 30))
    d = ImageDraw.Draw(board)
    f_title = font(Face.SANS_BOLD, 22)
    f_sub = font(Face.SANS, 14)
    f_hdr = font(Face.SANS_BOLD, 15)
    f_mono = font(Face.SANS_BOLD, 13)

    d.rectangle((0, 0, REPORT_W, 58), fill=(26, 32, 46))
    d.line([(0, 58), (REPORT_W, 58)], fill=(0, 210, 190), width=2)
    d.text(
        (24, 10),
        "BATOCERA EMULATIONSTATION THEME HARNESS — VISUAL REGRESSION & DIFF REPORT",
        font=f_title,
        fill=(245, 248, 255),
    )
    overall = metrics["overall"]
    badge = _badge_color(overall["similarity_pct"])
    d.rounded_rectangle((1180, 12, 1576, 46), radius=6, fill=badge)
    d.text(
        (1198, 20),
        f"OVERALL SIMILARITY: {overall['similarity_pct']:.2f}%  (MAE: {overall['mae']:.2f})",
        font=f_hdr,
        fill=(12, 16, 24),
    )

    _panel(
        board,
        (24, 72),
        title=f"1. GOLDEN TARGET REFERENCE ({reference_name})",
        title_col=(130, 210, 255),
        image=reference,
        border=(70, 95, 130),
    )
    _panel(
        board,
        (816, 72),
        title="2. ACTUAL BATOCERA THEME RENDER (1280x720)",
        title_col=(120, 245, 170),
        image=actual,
        border=(70, 140, 100),
    )

    p3x, p3y = 24, 520
    _panel(
        board,
        (p3x, p3y),
        title="3. PIXEL DIFF HEATMAP (DARK BLUE = EXACT MATCH, CYAN/YELLOW/RED = DELTA)",
        title_col=(255, 205, 95),
        image=heatmap(diff),
        border=(140, 110, 60),
        resample=Image.Resampling.NEAREST,
    )
    for _, (_, _, _, zy1) in ZONES:
        sy = p3y + 22 + round((zy1 / SCREEN_H) * _PANEL_H)
        d.line([(p3x, sy), (p3x + _PANEL_W, sy)], fill=(90, 120, 165), width=1)
    d.rectangle(
        (p3x, p3y + 22, p3x + _PANEL_W, p3y + 22 + _PANEL_H), outline=(140, 110, 60), width=2
    )

    p4x, p4y = 816, 520
    d.text(
        (p4x, p4y),
        "4. ELEMENT WIREFRAME INSPECTOR & REGION SCORES",
        font=f_hdr,
        fill=(235, 140, 255),
    )
    board.paste((wireframe or actual).resize((410, 230), Image.Resampling.LANCZOS), (p4x, p4y + 24))
    d.rectangle((p4x, p4y + 24, p4x + 410, p4y + 254), outline=(160, 90, 190), width=2)

    lx, ly = p4x + 426, p4y + 26
    d.rounded_rectangle(
        (lx, ly, p4x + _PANEL_W, ly + 228), radius=8, fill=(24, 30, 44), outline=(55, 70, 95)
    )
    d.text((lx + 14, ly + 12), "HARNESS CAPABILITIES", font=f_hdr, fill=(240, 245, 255))
    legend: list[tuple[str, str, RGB]] = [
        ("Cyan Box:", "<image> bounds (Ctrl+I)", (0, 230, 255)),
        ("Magenta Box:", "<imagegrid> & tiles (Ctrl+G)", (255, 70, 220)),
        ("Yellow Box:", "<text> bounds (Ctrl+T)", (255, 230, 40)),
        ("Hot Reload:", "GET 127.0.0.1:1234/reloadgames", (140, 235, 175)),
        ("Navigate:", "POST 127.0.0.1:1234/input", (140, 235, 175)),
    ]
    by = ly + 42
    for key, value, col in legend:
        d.text((lx + 14, by), key, font=f_mono, fill=col)
        d.text((lx + 120, by), value, font=f_sub, fill=(210, 220, 235))
        by += 34

    ty = p4y + 268
    d.rounded_rectangle(
        (p4x, ty, p4x + _PANEL_W, p4y + 22 + _PANEL_H),
        radius=8,
        fill=(22, 28, 40),
        outline=(60, 75, 105),
    )
    for x_off, heading in (
        (14, "UI REGION (1280x720)"),
        (330, "SIMILARITY"),
        (465, "EXACT PX (<=8)"),
        (610, "MAE"),
        (685, "STATUS"),
    ):
        d.text((p4x + x_off, ty + 10), heading, font=f_mono, fill=(150, 175, 210))
    d.line([(p4x + 10, ty + 32), (p4x + _PANEL_W - 10, ty + 32)], fill=(55, 70, 95), width=1)

    ry = ty + 40
    for name, zone in metrics["zones"].items():
        status, scol = _status(zone["similarity_pct"])
        d.text((p4x + 14, ry), name, font=f_sub, fill=(235, 240, 250))
        d.text((p4x + 330, ry), f"{zone['similarity_pct']:6.2f}%", font=f_mono, fill=scol)
        d.text(
            (p4x + 465, ry), f"{zone['within_tol_pct']:6.2f}%", font=f_mono, fill=(210, 220, 235)
        )
        d.text((p4x + 610, ry), f"{zone['mae']:5.2f}", font=f_mono, fill=(210, 220, 235))
        d.rounded_rectangle((p4x + 680, ry - 1, p4x + 742, ry + 18), radius=4, fill=scol)
        d.text((p4x + 692, ry + 1), status, font=f_mono, fill=(12, 16, 24))
        ry += 32

    out_path.parent.mkdir(parents=True, exist_ok=True)
    board.save(out_path)
