"""Region-by-region pixel comparison between a render/screenshot and the golden reference."""

from __future__ import annotations

from typing import Final, TypedDict

from PIL import Image, ImageChops, ImageStat

from n64mini.harness.render import SCREEN_H, SCREEN_W

type Box = tuple[int, int, int, int]

ZONES: Final[tuple[tuple[str, Box], ...]] = (
    ("Top Header & 5-Icon Bar", (0, 0, 1280, 104)),
    ("Chiseled 3D Title Banner", (0, 104, 1280, 185)),
    ("Center 5-Card Carousel", (0, 185, 1280, 490)),
    ("Bottom Ribbon & Controls", (0, 490, 1280, 720)),
)
EXACT_TOLERANCE: Final = 8  # max per-channel delta still counted as "exact"


class Score(TypedDict):
    """Similarity scores for one region of the screen."""

    mae: float
    rms: float
    similarity_pct: float
    within_tol_pct: float


class ZoneScore(Score):
    """A :class:`Score` plus the zone's pixel box."""

    box: Box


class Metrics(TypedDict):
    """Whole-screen and per-zone scores, as written to ``metrics.json``."""

    overall: Score
    zones: dict[str, ZoneScore]


def max_channel(img: Image.Image) -> Image.Image:
    """Single-band image holding max(R, G, B) per pixel."""
    r, g, b = img.convert("RGB").split()
    return ImageChops.lighter(ImageChops.lighter(r, g), b)


def _score(diff: Image.Image, box: Box) -> Score:
    crop = diff.crop(box)
    stat = ImageStat.Stat(crop)
    mae = sum(stat.mean) / 3.0
    rms = sum(stat.rms) / 3.0
    hist = max_channel(crop).histogram()
    within = sum(hist[: EXACT_TOLERANCE + 1])
    return {
        "mae": round(mae, 2),
        "rms": round(rms, 2),
        "similarity_pct": round(max(0.0, 100.0 * (1.0 - mae / 255.0)), 2),
        "within_tol_pct": round(100.0 * within / max(1, sum(hist)), 2),
    }


def _fit(img: Image.Image) -> Image.Image:
    if img.size != (SCREEN_W, SCREEN_H):
        img = img.resize((SCREEN_W, SCREEN_H), Image.Resampling.LANCZOS)
    return img.convert("RGB")


def compare(actual: Image.Image, reference: Image.Image) -> tuple[Image.Image, Metrics]:
    """Return the absolute-difference image and its overall + per-zone scores."""
    diff = ImageChops.difference(_fit(actual), _fit(reference))
    zones: dict[str, ZoneScore] = {name: {"box": box, **_score(diff, box)} for name, box in ZONES}
    return diff, {"overall": _score(diff, (0, 0, SCREEN_W, SCREEN_H)), "zones": zones}


def _heat(d: int) -> tuple[int, int, int]:
    if d <= 4:
        return (12, 18, 32)
    if d <= 16:
        t = (d - 4) / 12.0
        return (int(12 + 20 * t), int(18 + 140 * t), int(32 + 190 * t))
    if d <= 48:
        t = (d - 16) / 32.0
        return (int(32 + 223 * t), int(158 + 72 * t), int(222 * (1.0 - t)))
    t = min(1.0, (d - 48) / 120.0)
    return (255, int(230 * (1.0 - t)), int(40 * t))


_HEAT_LUT: Final = tuple(_heat(d) for d in range(256))


def heatmap(diff: Image.Image) -> Image.Image:
    """Colour each pixel by its max channel delta (dark blue = exact, red = large)."""
    m = max_channel(diff)
    bands = [m.point([_HEAT_LUT[d][c] for d in range(256)]) for c in range(3)]
    return Image.merge("RGB", bands)
