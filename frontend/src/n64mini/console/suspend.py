"""``n64mini-suspend``: the 4-slot ``NUS-006`` Suspend Point List drawer (AC-T13, AC-T18).

Opened from the carousel with D-Pad Down (Browse mode) or automatically after pressing the Reset
button inside an emulator game (Pending Save mode, with ``<rom>.pending`` hovering above Slot 1):

- **Pending / Floating Save mode**: a wingless cyan ``NUS-006`` cartridge hovers slowly up and down
  above the selected slot with a cartridge-width yellow ``[C-Down] Save`` bar. Left/Right moves
  across Slots 1-4; ``C-Down`` drops the cartridge into the slot and plays ``suspend_save.wav``;
  ``Start`` resumes the game from the pending state; ``B`` discards the floating state with a
  cartoon "poof" cloud animation and plays ``suspend_poof.wav``.
- **Browse mode**: Slots 1-4 show saved grey/cyan ``NUS-006`` cartridges with screenshots and
  timestamps, or recessed dark ``NUS-006`` empty bays (``No Data``). ``A`` starts the game from the
  selected slot, ``C-Right`` lifts a floating copy so ``C-Down`` can save it into another slot,
  ``C-Left`` poof-deletes the selected slot, and ``B`` closes the drawer.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import select
import sys
import tempfile
import wave
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, Protocol, TypedDict

from PIL import Image, ImageDraw, ImageFont

from n64mini.console.flip import SELECTED_FILE

if TYPE_CHECKING:
    from collections.abc import Callable

SAVES_DIR: Final = Path("/userdata/saves/n64")
DEFAULT_THEME_DIR: Final = Path("/userdata/themes/n64-classic-mini")
RESUME_FILE: Final = Path(tempfile.gettempdir()) / "n64mini-resume.json"
SLOT_COUNT: Final = 4
EXIT_CLOSED: Final = 0
EXIT_LAUNCH: Final = 10
# Returned by the stdin poller when its input closes. Not an action: a dead input source must
# never press B, because B in Pending Save mode deletes the Reset suspend point.
INPUT_CLOSED: Final = "<input closed>"

SCREEN_SIZE: Final = (1280, 720)
DRAWER_Y: Final = 372
DRAWER_H: Final = 348
SLOT_W: Final = 216
SLOT_H: Final = 143
SLOT_XS: Final = (164, 418, 672, 926)
SLOT_Y: Final = 474
FLOAT_BASE_Y: Final = SLOT_Y - SLOT_H - 52
PROMPT_BAR_Y: Final = 660

_PUFF_VECTORS: Final[tuple[tuple[float, float, float], ...]] = (
    (0.0, -4.0, 1.18),
    (-48.0, -24.0, 0.96),
    (48.0, -22.0, 0.96),
    (-62.0, 16.0, 0.88),
    (62.0, 18.0, 0.88),
    (-26.0, 34.0, 0.82),
    (28.0, 32.0, 0.82),
)

_SDL_INIT_VIDEO: Final = 0x00000020
_SDL_INIT_AUDIO: Final = 0x00000010
_SDL_WINDOW_FLAGS: Final = 0x00001011
_SDL_RENDERER_ACCELERATED: Final = 0x00000002
_SDL_PIXELFORMAT_ABGR8888: Final = 0x16762004
_SDL_TEXTUREACCESS_STATIC: Final = 0
_SDL_BLENDMODE_BLEND: Final = 1
_AUDIO_S16LSB: Final = 0x8010

type SoundKind = Literal["save", "poof"]
type Action = Literal["left", "right", "c_down", "c_right", "c_left", "a", "b", "start"]


class SuspendError(Exception):
    """The Suspend Point List overlay could not initialize or complete an operation."""


class ResumePayload(TypedDict):
    """One-shot launch instructions consumed by ``n64mini.console.launch``."""

    rom: str
    slot: int | None
    state_file: str


@dataclass(frozen=True, slots=True)
class SlotInfo:
    """State of one of the four ``.st0..st3`` suspend slots for a ROM."""

    slot: int
    exists: bool
    mtime: float
    label: str
    state_path: Path
    shot_path: Path | None


@dataclass(frozen=True, slots=True)
class PendingState:
    """A floating suspend point waiting above the slots (from Reset or Copy)."""

    kind: Literal["reset", "copy"]
    state_path: Path
    shot_path: Path | None
    label: str


@dataclass(frozen=True, slots=True)
class SuspendModel:
    """Immutable UI state for the Suspend Point List."""

    rom: str
    saves_dir: Path
    selected_slot: int
    slots: tuple[SlotInfo, ...]
    pending: PendingState | None
    from_reset: bool


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """Result of applying one user ``Action`` to ``SuspendModel``."""

    model: SuspendModel
    effect: Literal["none", "saved", "poof_pending", "poof_slot", "close", "launch"]
    effect_slot: int
    sound: SoundKind | None = None
    resume: ResumePayload | None = None


@dataclass(frozen=True, slots=True)
class DropStep:
    """One animation keyframe as the floating cartridge drops into a save slot."""

    slot: int
    y: int
    flash_alpha: int


@dataclass(frozen=True, slots=True)
class PuffCircle:
    """One expanding/fading puff circle in the discard/delete 'poof' cloud."""

    cx: int
    cy: int
    radius: int
    alpha: int


@dataclass(frozen=True, slots=True)
class PoofStep:
    """One animation keyframe of a cartridge vanishing in a 'poof' cloud."""

    cart_scale: float
    cart_alpha: int
    puffs: tuple[PuffCircle, ...]


@dataclass(frozen=True, slots=True)
class PoofOverlay:
    """Active poof animation state drawn above a vanishing floating or slot cartridge."""

    step: PoofStep
    ghost: Image.Image
    center: tuple[int, int]


@dataclass(frozen=True, slots=True)
class SuspendAssets:
    """Pre-loaded theme art, fonts, and backdrop for compositing the Suspend Point List."""

    backdrop: Image.Image
    drawer: Image.Image
    cart_stock: Image.Image
    cart_selected: Image.Image
    cart_empty: Image.Image
    cart_target: Image.Image
    cart_mask: Image.Image
    buttons: dict[str, Image.Image]
    font_path: Path
    fallback_thumb: Image.Image


class SuspendBackend(Protocol):
    """Combined 60 FPS frame presenter and sound effect sink for the Suspend Point List."""

    def show(self, frame: Image.Image, delay_ms: int) -> None:
        """Present ``frame`` and pause ``delay_ms`` milliseconds."""

    def play(self, kind: SoundKind) -> None:
        """Play ``kind`` (``"save"`` or ``"poof"``)."""

    def close(self) -> None:
        """Release display and audio resources."""


def ease_out(t: float) -> float:
    """Cubic ease-out ``1 - (1 - t)^3`` on ``0..1``."""
    clamped = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - clamped) ** 3


def hover_offset_y(tick: int) -> int:
    """Slow sinusoidal vertical hover offset (in pixels) for the floating save cartridge."""
    return round(5.0 * math.sin(tick * 0.08))


def format_slot_timestamp(mtime: float) -> str:
    """Format a POSIX modification timestamp as ``MM/DD  HH:MM`` in local time."""
    dt = datetime.fromtimestamp(mtime, tz=UTC).astimezone()
    return dt.strftime("%m/%d  %H:%M")


def inspect_slots(saves_dir: Path, rom: str) -> tuple[SlotInfo, ...]:
    """Read the four ``.st0..st3`` suspend slots and optional ``.png`` screenshots for ``rom``."""
    items: list[SlotInfo] = []
    for slot in range(SLOT_COUNT):
        st = saves_dir / f"{rom}.st{slot}"
        png = saves_dir / f"{rom}.st{slot}.png"
        if rom and st.is_file() and st.stat().st_size > 0:
            mtime = st.stat().st_mtime
            shot = png if (png.is_file() and png.stat().st_size > 0) else None
            items.append(
                SlotInfo(
                    slot=slot,
                    exists=True,
                    mtime=mtime,
                    label=format_slot_timestamp(mtime),
                    state_path=st,
                    shot_path=shot,
                )
            )
        else:
            items.append(
                SlotInfo(
                    slot=slot, exists=False, mtime=0.0, label="", state_path=st, shot_path=None
                )
            )
    return tuple(items)


def inspect_pending(saves_dir: Path, rom: str) -> PendingState | None:
    """Return the staged ``<rom>.pending`` suspend point from ``n64mini-reset`` if present."""
    if not rom:
        return None
    st = saves_dir / f"{rom}.pending"
    if not st.is_file() or st.stat().st_size == 0:
        return None
    png = saves_dir / f"{rom}.pending.png"
    return PendingState(
        kind="reset",
        state_path=st,
        shot_path=png if (png.is_file() and png.stat().st_size > 0) else None,
        label="NEW  0:00",
    )


def load_model(saves_dir: Path, rom: str, *, initial_slot: int = 0) -> SuspendModel:
    """Build the initial ``SuspendModel`` for ``rom`` from ``saves_dir``."""
    slots = inspect_slots(saves_dir, rom)
    pending = inspect_pending(saves_dir, rom)
    clamped = max(0, min(SLOT_COUNT - 1, initial_slot))
    if pending is None and not slots[clamped].exists:
        first_occupied = next((s.slot for s in slots if s.exists), clamped)
        clamped = first_occupied
    return SuspendModel(
        rom=rom,
        saves_dir=saves_dir,
        selected_slot=clamped,
        slots=slots,
        pending=pending,
        from_reset=pending is not None,
    )


def _atomic_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.name}.tmp")
    with tmp.open("wb") as fh:
        fh.write(src.read_bytes())
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(dst)


def commit_pending(saves_dir: Path, rom: str, pending: PendingState, target_slot: int) -> None:
    """Write ``pending`` into ``<rom>.st<target_slot>`` (moving a reset state, copying a copy)."""
    dst_state = saves_dir / f"{rom}.st{target_slot}"
    dst_shot = saves_dir / f"{rom}.st{target_slot}.png"
    _atomic_copy(pending.state_path, dst_state)
    if pending.shot_path is not None and pending.shot_path.is_file():
        _atomic_copy(pending.shot_path, dst_shot)
    elif dst_shot.is_file():
        dst_shot.unlink()
    if pending.kind == "reset":
        discard_pending(pending)


def discard_pending(pending: PendingState) -> None:
    """Remove ``<rom>.pending`` files when discarding a Reset suspend point."""
    if pending.kind != "reset":
        return
    if pending.state_path.is_file():
        pending.state_path.unlink()
    if pending.shot_path is not None and pending.shot_path.is_file():
        pending.shot_path.unlink()


def delete_slot(saves_dir: Path, rom: str, slot: int) -> bool:
    """Delete ``<rom>.st<slot>`` and its ``.png`` screenshot if present."""
    st = saves_dir / f"{rom}.st{slot}"
    png = saves_dir / f"{rom}.st{slot}.png"
    existed = st.is_file()
    if existed:
        st.unlink()
    if png.is_file():
        png.unlink()
    return existed


def write_resume_request(payload: ResumePayload, resume_file: Path = RESUME_FILE) -> None:
    """Write ``payload`` atomically so ``n64mini.console.launch`` loads the chosen state."""
    resume_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = resume_file.with_name(f"{resume_file.name}.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(resume_file)


def _step_floating(model: SuspendModel, pending: PendingState, action: Action) -> StepOutcome:
    slot = model.selected_slot
    if action == "left":
        return StepOutcome(replace(model, selected_slot=(slot - 1) % SLOT_COUNT), "none", slot)
    if action == "right":
        return StepOutcome(replace(model, selected_slot=(slot + 1) % SLOT_COUNT), "none", slot)
    if action == "c_down":
        commit_pending(model.saves_dir, model.rom, pending, slot)
        refreshed = inspect_slots(model.saves_dir, model.rom)
        next_model = replace(model, slots=refreshed, pending=None, from_reset=False)
        return StepOutcome(next_model, "saved", slot, sound="save")
    if action == "start" and pending.kind == "reset":
        resume_st = model.saves_dir / f"{model.rom}.st9"
        _atomic_copy(pending.state_path, resume_st)
        discard_pending(pending)
        payload: ResumePayload = {"rom": model.rom, "slot": 9, "state_file": str(resume_st)}
        return StepOutcome(replace(model, pending=None), "launch", slot, resume=payload)
    if action == "b":
        discard_pending(pending)
        next_model = replace(model, pending=None)
        return StepOutcome(next_model, "poof_pending", slot, sound="poof")
    return StepOutcome(model, "none", slot)


def _step_browse(model: SuspendModel, action: Action) -> StepOutcome:
    slot = model.selected_slot
    current = model.slots[slot]
    if action == "left":
        return StepOutcome(replace(model, selected_slot=(slot - 1) % SLOT_COUNT), "none", slot)
    if action == "right":
        return StepOutcome(replace(model, selected_slot=(slot + 1) % SLOT_COUNT), "none", slot)
    if action in ("a", "start") and current.exists:
        payload: ResumePayload = {
            "rom": model.rom,
            "slot": slot,
            "state_file": str(current.state_path),
        }
        return StepOutcome(model, "launch", slot, resume=payload)
    if action == "c_right" and current.exists:
        copied = PendingState("copy", current.state_path, current.shot_path, current.label)
        return StepOutcome(replace(model, pending=copied, from_reset=False), "none", slot)
    if action == "c_left" and current.exists:
        delete_slot(model.saves_dir, model.rom, slot)
        refreshed = inspect_slots(model.saves_dir, model.rom)
        return StepOutcome(replace(model, slots=refreshed), "poof_slot", slot, sound="poof")
    if action == "b":
        return StepOutcome(model, "close", slot)
    return StepOutcome(model, "none", slot)


def step_action(model: SuspendModel, action: Action) -> StepOutcome:
    """Apply ``action`` to ``model``, performing slot I/O and returning ``StepOutcome``."""
    if model.pending is not None:
        return _step_floating(model, model.pending, action)
    return _step_browse(model, action)


def save_drop_frames(slot: int, start_dy: int = 0, steps: int = 12) -> list[DropStep]:
    """Keyframes dropping the floating cartridge into ``slot`` followed by a lock flash."""
    y0 = FLOAT_BASE_Y + start_dy
    frames: list[DropStep] = []
    for i in range(1, steps + 1):
        u = ease_out(i / steps)
        y = round(y0 + (SLOT_Y - y0) * u)
        flash = round(190 * (i / steps)) if i > steps - 3 else 0
        frames.append(DropStep(slot=slot, y=y, flash_alpha=flash))
    frames.extend(DropStep(slot=slot, y=SLOT_Y, flash_alpha=decay) for decay in (140, 80, 0))
    return frames


def poof_frames(center: tuple[int, int], steps: int = 18) -> list[PoofStep]:
    """Keyframes shrinking the cartridge and dispersing cartoon 'poof' smoke puffs."""
    cx, cy = center
    out: list[PoofStep] = []
    for i in range(1, steps + 1):
        t = i / steps
        if t <= 0.35:
            frac = t / 0.35
            cart_scale = 1.0 - 0.45 * frac
            cart_alpha = round(255 * (1.0 - frac))
        else:
            cart_scale = 0.0
            cart_alpha = 0
        u = ease_out(t)
        fade = max(0.0, 1.0 - max(0.0, (t - 0.25) / 0.75))
        puffs = tuple(
            PuffCircle(
                cx=round(cx + dx * u),
                cy=round(cy + dy * u),
                radius=max(
                    4,
                    round(
                        28.0
                        * r_scale
                        * (0.45 + 0.75 * math.sin(min(1.0, t * 1.25) * math.pi * 0.65))
                    ),
                ),
                alpha=round(240 * fade),
            )
            for dx, dy, r_scale in _PUFF_VECTORS
        )
        out.append(PoofStep(cart_scale=cart_scale, cart_alpha=cart_alpha, puffs=puffs))
    return out


def _load_rgba(
    path: Path, size: tuple[int, int], fallback: tuple[int, int, int, int]
) -> Image.Image:
    if path.is_file():
        with Image.open(path) as img:
            rgba = img.convert("RGBA")
            return rgba if rgba.size == size else rgba.resize(size, Image.Resampling.BILINEAR)
    return Image.new("RGBA", size, fallback)


def _font(font_path: Path, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if font_path.is_file():
        return ImageFont.truetype(font_path, max(8, size))
    return ImageFont.load_default()


def load_suspend_assets(
    theme_dir: Path = DEFAULT_THEME_DIR,
    backdrop: Image.Image | None = None,
    fallback_thumb: Image.Image | None = None,
) -> SuspendAssets:
    """Load pre-rendered ``Suspend Point List`` art and fonts from ``theme_dir``."""
    art = theme_dir / "art"
    if backdrop is None:
        raw_bg = _load_rgba(art / "n64_classic_bg.png", SCREEN_SIZE, (18, 20, 26, 255))
        for name, pos, sz in (
            ("brand.png", (40, 5), (1200, 82)),
            ("console.png", (40, 635), (1200, 82)),
        ):
            if (art / name).is_file():
                raw_bg.alpha_composite(_load_rgba(art / name, sz, (0, 0, 0, 0)), pos)
        backdrop = Image.alpha_composite(raw_bg, Image.new("RGBA", SCREEN_SIZE, (0, 0, 0, 118)))
    buttons = {
        kind: _load_rgba(art / f"btn_{kind}.png", (28, 28), (200, 200, 200, 255))
        for kind in ("dpad_lr", "a", "b", "start", "c_down", "c_right", "c_left")
    }
    thumb = fallback_thumb or Image.new("RGBA", (160, 120), (34, 42, 62, 255))
    return SuspendAssets(
        backdrop=backdrop,
        drawer=_load_rgba(art / "suspend_drawer.png", (1280, DRAWER_H), (90, 96, 108, 255)),
        cart_stock=_load_rgba(art / "cart_stock.png", (SLOT_W, SLOT_H), (138, 144, 154, 255)),
        cart_selected=_load_rgba(art / "cart_selected.png", (SLOT_W, SLOT_H), (118, 210, 255, 255)),
        cart_empty=_load_rgba(art / "cart_empty.png", (SLOT_W, SLOT_H), (20, 23, 30, 255)),
        cart_target=_load_rgba(art / "cart_target.png", (SLOT_W, SLOT_H), (22, 32, 43, 255)),
        cart_mask=_load_rgba(art / "cart_mask.png", (SLOT_W, SLOT_H), (255, 255, 255, 255)),
        buttons=buttons,
        font_path=theme_dir / "fonts" / "DejaVuSans-Bold.ttf",
        fallback_thumb=thumb,
    )


def _render_cart_with_shot(
    base_cart: Image.Image,
    assets: SuspendAssets,
    shot_path: Path | None,
    label: str,
) -> Image.Image:
    out = base_cart.copy()
    px0 = round(87 * SLOT_W / 348)
    px1 = round(261 * SLOT_W / 348)
    py0 = round(18 * SLOT_H / 230)
    py1 = round(201 * SLOT_H / 230)
    pw, ph = px1 - px0, py1 - py0
    footer_h = round(25 * SLOT_H / 230)
    well = Image.new("RGBA", (SLOT_W, SLOT_H), (0, 0, 0, 0))
    if shot_path is not None and shot_path.is_file():
        shot = _load_rgba(shot_path, (pw, ph - footer_h), (28, 34, 48, 255))
    else:
        shot = assets.fallback_thumb.resize((pw, ph - footer_h), Image.Resampling.BILINEAR)
    well.paste(shot, (px0, py0))
    draw = ImageDraw.Draw(well)
    draw.rectangle((px0, py1 - footer_h, px1, py1), fill=(10, 12, 18, 248))
    draw.line([(px0, py1 - footer_h), (px1, py1 - footer_h)], fill=(58, 66, 82, 255), width=1)
    face = _font(assets.font_path, 12)
    bbox = draw.textbbox((0, 0), label, font=face)
    fx = px0 + (pw - (bbox[2] - bbox[0])) / 2 - bbox[0]
    fy = py1 - footer_h + (footer_h - (bbox[3] - bbox[1])) / 2 - bbox[1]
    draw.text((fx, fy), label, font=face, fill=(238, 242, 250, 255))
    clipped = Image.new("RGBA", (SLOT_W, SLOT_H), (0, 0, 0, 0))
    clipped.paste(well, (0, 0), assets.cart_mask.getchannel("A"))
    out.alpha_composite(clipped)
    return out


def _draw_slot_tab(canvas: Image.Image, assets: SuspendAssets, slot_idx: int, slot_y: int) -> None:
    sx = SLOT_XS[slot_idx]
    shoulder_y = slot_y + round(38 * SLOT_H / 230) - 1
    cart_left_x = sx + round(6 * SLOT_W / 348)
    tab_w, tab_h = 26, 28
    tx0 = cart_left_x - tab_w + 2
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle(
        (tx0, shoulder_y, tx0 + tab_w, shoulder_y + tab_h),
        radius=4,
        fill=(116, 122, 136, 255),
        outline=(28, 32, 40, 255),
        width=2,
    )
    draw.line(
        [(tx0 + 3, shoulder_y + 2), (tx0 + tab_w - 3, shoulder_y + 2)],
        fill=(182, 188, 202, 255),
        width=2,
    )
    face = _font(assets.font_path, 16)
    num = str(slot_idx + 1)
    bbox = draw.textbbox((0, 0), num, font=face)
    nx = tx0 + (tab_w - 2 - (bbox[2] - bbox[0])) / 2 - bbox[0]
    ny = shoulder_y + (tab_h - (bbox[3] - bbox[1])) / 2 - bbox[1]
    draw.text((nx, ny), num, font=face, fill=(18, 20, 26, 255))


def _draw_slots(
    canvas: Image.Image, model: SuspendModel, assets: SuspendAssets, drawer_dy: int
) -> None:
    sy = SLOT_Y + drawer_dy
    draw = ImageDraw.Draw(canvas, "RGBA")
    empty_face = _font(assets.font_path, 16)
    for info in model.slots:
        sx = SLOT_XS[info.slot]
        _draw_slot_tab(canvas, assets, info.slot, sy)
        active = info.slot == model.selected_slot
        if info.exists:
            base = assets.cart_selected if (active and model.pending is None) else assets.cart_stock
            cart = _render_cart_with_shot(base, assets, info.shot_path, info.label)
            canvas.alpha_composite(cart, (sx, sy))
        else:
            empty_art = assets.cart_target if active else assets.cart_empty
            canvas.alpha_composite(empty_art, (sx, sy))
            bbox = draw.textbbox((0, 0), "No Data", font=empty_face)
            tx = sx + (SLOT_W - (bbox[2] - bbox[0])) / 2 - bbox[0]
            ty = sy + round(SLOT_H * 0.50) - (bbox[3] - bbox[1]) / 2 - bbox[1]
            col = (138, 176, 204, 255) if active else (104, 110, 124, 255)
            draw.text((tx, ty), "No Data", font=empty_face, fill=col)


def _draw_floating_save(
    canvas: Image.Image,
    pending: PendingState,
    slot: int,
    fy: int,
    assets: SuspendAssets,
) -> None:
    fx = SLOT_XS[slot]
    cart = _render_cart_with_shot(assets.cart_selected, assets, pending.shot_path, pending.label)
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle(
        (fx + 4, fy + 8, fx + SLOT_W - 2, fy + SLOT_H + 6), radius=10, fill=(0, 0, 0, 135)
    )
    canvas.alpha_composite(cart, (fx, fy))
    ay = fy + round(SLOT_H * 0.54)
    draw.polygon(
        [(fx - 20, ay), (fx - 5, ay - 15), (fx - 5, ay + 15)],
        fill=(42, 218, 76, 255),
        outline=(235, 255, 240, 255),
    )
    draw.polygon(
        [(fx + SLOT_W + 20, ay), (fx + SLOT_W + 5, ay - 15), (fx + SLOT_W + 5, ay + 15)],
        fill=(42, 218, 76, 255),
        outline=(235, 255, 240, 255),
    )
    rim_inset = round(3 * SLOT_W / 348)
    px0 = fx + rim_inset
    pill_w = SLOT_W - 2 * rim_inset
    pill_h = 36
    py0 = fy + SLOT_H + 6
    draw.rounded_rectangle(
        (px0, py0 + 2, px0 + pill_w, py0 + pill_h + 2), radius=8, fill=(0, 0, 0, 140)
    )
    draw.rounded_rectangle(
        (px0, py0, px0 + pill_w, py0 + pill_h),
        radius=8,
        fill=(245, 204, 46, 255),
        outline=(128, 88, 8, 255),
        width=2,
    )
    draw.line([(px0 + 8, py0 + 3), (px0 + pill_w - 8, py0 + 3)], fill=(255, 242, 168, 235), width=2)
    c_icon = assets.buttons["c_down"]
    face = _font(assets.font_path, 17)
    bbox = draw.textbbox((0, 0), "Save", font=face)
    cw = c_icon.width + 8 + (bbox[2] - bbox[0])
    icx = int(px0 + (pill_w - cw) // 2)
    icy = py0 + (pill_h - c_icon.height) // 2
    draw.ellipse((icx - 1, icy - 1, icx + c_icon.width, icy + c_icon.height), fill=(28, 22, 8, 255))
    canvas.alpha_composite(c_icon, (icx, icy))
    draw.text(
        (icx + c_icon.width + 8 - bbox[0], py0 + (pill_h - (bbox[3] - bbox[1])) / 2 - bbox[1]),
        "Save",
        font=face,
        fill=(20, 18, 10, 255),
    )


def _prompt_items(model: SuspendModel, assets: SuspendAssets) -> list[tuple[Image.Image, str]]:
    b = assets.buttons
    if model.pending is not None:
        items = [(b["dpad_lr"], "Select Slot"), (b["c_down"], "Save to Slot")]
        if model.pending.kind == "reset":
            items.append((b["start"], "Resume Game"))
        items.append((b["b"], "Discard / Back" if model.pending.kind == "reset" else "Cancel"))
        return items
    return [
        (b["dpad_lr"], "Select"),
        (b["a"], "Start Game"),
        (b["c_right"], "Copy"),
        (b["c_left"], "Delete"),
        (b["b"], "Back"),
    ]


def _draw_prompt_bar(
    canvas: Image.Image, model: SuspendModel, assets: SuspendAssets, drawer_dy: int
) -> None:
    y_bar = PROMPT_BAR_Y + drawer_dy
    bx0, bx1, bh = 112, 1168, 40
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle(
        (bx0, y_bar, bx1, y_bar + bh),
        radius=7,
        fill=(198, 34, 46, 255),
        outline=(112, 16, 24, 255),
        width=2,
    )
    draw.line([(bx0 + 6, y_bar + 2), (bx1 - 6, y_bar + 2)], fill=(238, 86, 96, 180), width=1)
    items = _prompt_items(model, assets)
    face = _font(assets.font_path, 15)
    gap_it, gap_grp = 7, 26
    widths = [
        ic.width
        + gap_it
        + round(draw.textbbox((0, 0), lbl, font=face)[2] - draw.textbbox((0, 0), lbl, font=face)[0])
        for ic, lbl in items
    ]
    cx = (SCREEN_SIZE[0] - (sum(widths) + gap_grp * (len(items) - 1))) // 2
    cy = y_bar + bh // 2
    for (ic, lbl), gw in zip(items, widths, strict=True):
        canvas.alpha_composite(ic, (cx, cy - ic.height // 2))
        bb = draw.textbbox((0, 0), lbl, font=face)
        tx = cx + ic.width + gap_it - bb[0]
        ty = cy - (bb[3] - bb[1]) / 2 - bb[1]
        draw.text((tx + 1, ty + 1), lbl, font=face, fill=(64, 8, 12, 220))
        draw.text((tx, ty), lbl, font=face, fill=(250, 252, 255, 255))
        cx += gw + gap_grp


def _draw_poof_overlay(
    canvas: Image.Image,
    poof: PoofStep,
    ghost_cart: Image.Image,
    center: tuple[int, int],
) -> None:
    if poof.cart_alpha > 0:
        sw = max(4, round(ghost_cart.width * poof.cart_scale))
        sh = max(4, round(ghost_cart.height * poof.cart_scale))
        scaled = ghost_cart.resize((sw, sh), Image.Resampling.BILINEAR)
        lut = [round(a * poof.cart_alpha / 255) for a in range(256)]
        scaled.putalpha(scaled.getchannel("A").point(lut))
        canvas.alpha_composite(scaled, (center[0] - sw // 2, center[1] - sh // 2))
    puff_layer = Image.new("RGBA", SCREEN_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(puff_layer, "RGBA")
    for p in poof.puffs:
        if p.alpha <= 0:
            continue
        r = p.radius
        draw.ellipse(
            (p.cx - r - 2, p.cy - r - 2, p.cx + r + 2, p.cy + r + 2),
            fill=(168, 178, 196, round(p.alpha * 0.75)),
        )
        draw.ellipse((p.cx - r, p.cy - r, p.cx + r, p.cy + r), fill=(244, 248, 255, p.alpha))
    canvas.alpha_composite(puff_layer)


def compose_suspend_frame(
    model: SuspendModel,
    assets: SuspendAssets,
    *,
    tick: int = 0,
    drawer_dy: int = 0,
    overlay: DropStep | PoofOverlay | None = None,
) -> Image.Image:
    """Composite one 1280x720 RGBA frame of the Suspend Point List."""
    canvas = assets.backdrop.copy()
    canvas.alpha_composite(assets.drawer, (0, DRAWER_Y + drawer_dy))
    _draw_slots(canvas, model, assets, drawer_dy)
    _draw_prompt_bar(canvas, model, assets, drawer_dy)
    if isinstance(overlay, DropStep):
        slot_info = model.slots[overlay.slot]
        cart_y = overlay.y + drawer_dy
        cart = _render_cart_with_shot(
            assets.cart_selected, assets, slot_info.shot_path, slot_info.label
        )
        canvas.alpha_composite(cart, (SLOT_XS[overlay.slot], cart_y))
        if overlay.flash_alpha > 0:
            flash_a = overlay.flash_alpha
            flash = Image.new("RGBA", (SLOT_W, SLOT_H), (220, 248, 255, flash_a))
            lut = [round(a * flash_a / 255) for a in range(256)]
            flash.putalpha(assets.cart_selected.getchannel("A").point(lut))
            canvas.alpha_composite(flash, (SLOT_XS[overlay.slot], SLOT_Y + drawer_dy))
    elif model.pending is not None:
        fy = FLOAT_BASE_Y + hover_offset_y(tick) + drawer_dy
        _draw_floating_save(canvas, model.pending, model.selected_slot, fy, assets)
    if isinstance(overlay, PoofOverlay):
        _draw_poof_overlay(canvas, overlay.step, overlay.ghost, overlay.center)
    return canvas


def parse_command_token(token: str) -> Action | None:
    """Map an input command word from ``stdin`` to a typed ``Action``."""
    mapping: dict[str, Action] = {
        "left": "left",
        "right": "right",
        "c_down": "c_down",
        "save": "c_down",
        "c_right": "c_right",
        "copy": "c_right",
        "c_left": "c_left",
        "delete": "c_left",
        "a": "a",
        "enter": "a",
        "b": "b",
        "esc": "b",
        "start": "start",
    }
    return mapping.get(token.strip().lower())


def _ghost_cart_for_poof(
    prev_model: SuspendModel,
    assets: SuspendAssets,
    slot: int,
) -> Image.Image:
    if prev_model.pending is not None:
        return _render_cart_with_shot(
            assets.cart_selected, assets, prev_model.pending.shot_path, prev_model.pending.label
        )
    info = prev_model.slots[slot]
    return _render_cart_with_shot(assets.cart_stock, assets, info.shot_path, info.label)


def _animate_outcome(
    outcome: StepOutcome,
    prev_model: SuspendModel,
    assets: SuspendAssets,
    tick: int,
    backend: SuspendBackend,
) -> int:
    model = outcome.model
    if outcome.effect == "saved":
        for dstep in save_drop_frames(outcome.effect_slot, hover_offset_y(tick)):
            backend.show(compose_suspend_frame(model, assets, tick=tick, overlay=dstep), 15)
            tick += 1
    elif outcome.effect in ("poof_pending", "poof_slot"):
        is_float = outcome.effect == "poof_pending"
        sx = SLOT_XS[outcome.effect_slot]
        sy = (FLOAT_BASE_Y + hover_offset_y(tick)) if is_float else SLOT_Y
        ghost = _ghost_cart_for_poof(prev_model, assets, outcome.effect_slot)
        center = (sx + SLOT_W // 2, sy + SLOT_H // 2)
        for pstep in poof_frames(center):
            poof_ov = PoofOverlay(step=pstep, ghost=ghost, center=center)
            backend.show(compose_suspend_frame(model, assets, tick=tick, overlay=poof_ov), 15)
            tick += 1
    return tick


def run_interactive_loop(
    model: SuspendModel,
    assets: SuspendAssets,
    backend: SuspendBackend,
    poll_stdin: Callable[[], list[str]],
    *,
    resume_file: Path = RESUME_FILE,
) -> tuple[int, SuspendModel]:
    """Run the 60 FPS interactive loop reading action tokens via ``poll_stdin``."""
    tick = 0
    for dy_idx in range(1, 7):
        dy = round(DRAWER_H * (1.0 - ease_out(dy_idx / 6)))
        backend.show(compose_suspend_frame(model, assets, tick=tick, drawer_dy=dy), 14)
        tick += 1
    while True:
        tokens = poll_stdin()
        if not tokens:
            backend.show(compose_suspend_frame(model, assets, tick=tick), 16)
            tick += 1
            continue
        for tok in tokens:
            if tok == INPUT_CLOSED:
                # Leave any pending state on disk: the next open offers it again.
                return EXIT_CLOSED, model
            action = parse_command_token(tok)
            if action is None:
                continue
            prev_model = model
            outcome = step_action(model, action)
            model = outcome.model
            if outcome.sound is not None:
                backend.play(outcome.sound)
            tick = _animate_outcome(outcome, prev_model, assets, tick, backend)
            if outcome.effect == "poof_pending" and prev_model.from_reset:
                return EXIT_CLOSED, model
            if outcome.resume is not None:
                write_resume_request(outcome.resume, resume_file)
                return EXIT_LAUNCH, model
            if outcome.effect == "close":
                return EXIT_CLOSED, model


class _SdlRect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int), ("w", ctypes.c_int), ("h", ctypes.c_int)]


class _SdlAudioSpec(ctypes.Structure):
    _fields_ = [
        ("freq", ctypes.c_int),
        ("format", ctypes.c_uint16),
        ("channels", ctypes.c_uint8),
        ("silence", ctypes.c_uint8),
        ("samples", ctypes.c_uint16),
        ("padding", ctypes.c_uint16),
        ("size", ctypes.c_uint32),
        ("callback", ctypes.c_void_p),
        ("userdata", ctypes.c_void_p),
    ]


class XImage(ctypes.Structure):
    """C layout of ``XImage`` returned by ``XGetImage``."""

    _fields_ = [
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("xoffset", ctypes.c_int),
        ("format", ctypes.c_int),
        ("data", ctypes.c_void_p),
        ("byte_order", ctypes.c_int),
        ("bitmap_unit", ctypes.c_int),
        ("bitmap_bit_order", ctypes.c_int),
        ("bitmap_pad", ctypes.c_int),
        ("depth", ctypes.c_int),
        ("bytes_per_line", ctypes.c_int),
        ("bits_per_pixel", ctypes.c_int),
        ("red_mask", ctypes.c_ulong),
        ("green_mask", ctypes.c_ulong),
        ("blue_mask", ctypes.c_ulong),
    ]


def capture_x11_backdrop(x11_loader: Callable[[], ctypes.CDLL] | None = None) -> Image.Image | None:
    """Capture and dim the live X11 root window before mapping the SDL2 overlay."""
    loader = x11_loader or (lambda: ctypes.CDLL("libX11.so.6"))
    try:
        x11 = loader()
    except OSError:
        return None
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x11.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x11.XGetImage.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_ulong,
        ctypes.c_int,
    ]
    x11.XGetImage.restype = ctypes.POINTER(XImage)
    x11.XDestroyImage.argtypes = [ctypes.POINTER(XImage)]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    dpy = x11.XOpenDisplay(None)
    if not dpy:
        return None
    root = x11.XDefaultRootWindow(dpy)
    sw = int(x11.XDisplayWidth(dpy, 0))
    sh = int(x11.XDisplayHeight(dpy, 0))
    xim = x11.XGetImage(dpy, root, 0, 0, sw, sh, 0xFFFFFFFF, 2)
    if not xim:
        x11.XCloseDisplay(dpy)
        return None
    raw = bytearray(ctypes.string_at(xim.contents.data, sw * sh * 4))
    x11.XDestroyImage(xim)
    x11.XCloseDisplay(dpy)
    raw[0::4], raw[2::4] = raw[2::4], raw[0::4]
    raw[3::4] = b"\xff" * (sw * sh)
    snap = Image.frombytes("RGBA", (sw, sh), bytes(raw))
    if (sw, sh) != SCREEN_SIZE:
        snap = snap.resize(SCREEN_SIZE, Image.Resampling.BILINEAR)
    return Image.alpha_composite(snap, Image.new("RGBA", SCREEN_SIZE, (0, 0, 0, 118)))


class SdlBackend:
    """Combined 60 FPS SDL2 video presenter and sample-accurate PCM WAV audio player."""

    def __init__(self, theme_dir: Path, sdl: ctypes.CDLL) -> None:
        self._sdl = sdl
        self._sdl.SDL_CreateWindow.restype = ctypes.c_void_p
        self._sdl.SDL_CreateRenderer.restype = ctypes.c_void_p
        self._sdl.SDL_CreateTexture.restype = ctypes.c_void_p
        if int(self._sdl.SDL_Init(_SDL_INIT_VIDEO)) != 0:
            raise SuspendError("SDL_Init video failed")
        sw, sh = SCREEN_SIZE
        self._win = ctypes.c_void_p(
            self._sdl.SDL_CreateWindow(b"n64mini-suspend", 0, 0, sw, sh, _SDL_WINDOW_FLAGS)
        )
        self._ren = ctypes.c_void_p(
            self._sdl.SDL_CreateRenderer(self._win, -1, _SDL_RENDERER_ACCELERATED)
        )
        self._sdl.SDL_RenderSetLogicalSize(self._ren, sw, sh)
        self._tex = ctypes.c_void_p(
            self._sdl.SDL_CreateTexture(
                self._ren, _SDL_PIXELFORMAT_ABGR8888, _SDL_TEXTUREACCESS_STATIC, sw, sh
            )
        )
        self._sdl.SDL_SetTextureBlendMode(self._tex, _SDL_BLENDMODE_BLEND)
        self._audio_dev = 0
        self._pcm: dict[SoundKind, bytes] = {}
        self._init_audio(theme_dir)

    def _init_audio(self, theme_dir: Path) -> None:
        if int(self._sdl.SDL_InitSubSystem(_SDL_INIT_AUDIO)) != 0:
            return
        spec = _SdlAudioSpec(freq=22050, format=_AUDIO_S16LSB, channels=1, samples=1024)
        dev = int(self._sdl.SDL_OpenAudioDevice(None, 0, ctypes.byref(spec), None, 0))
        if dev <= 0:
            return
        self._audio_dev = dev
        self._sdl.SDL_PauseAudioDevice(dev, 0)
        sound_files: tuple[tuple[SoundKind, str], ...] = (
            ("save", "suspend_save.wav"),
            ("poof", "suspend_poof.wav"),
        )
        for kind, name in sound_files:
            wav_path = theme_dir / "sounds" / name
            if wav_path.is_file():
                with wave.open(str(wav_path), "rb") as w:
                    self._pcm[kind] = w.readframes(w.getnframes())

    def play(self, kind: SoundKind) -> None:
        """Queue PCM samples for ``kind`` on the open SDL2 audio device."""
        pcm = self._pcm.get(kind)
        if self._audio_dev > 0 and pcm:
            self._sdl.SDL_ClearQueuedAudio(self._audio_dev)
            self._sdl.SDL_QueueAudio(self._audio_dev, pcm, len(pcm))

    def show(self, frame: Image.Image, delay_ms: int) -> None:
        """Upload ``frame`` to the streaming texture, present it, and sleep ``delay_ms``."""
        raw = frame.tobytes("raw", "RGBA")
        self._sdl.SDL_UpdateTexture(self._tex, ctypes.c_void_p(0), raw, SCREEN_SIZE[0] * 4)
        self._sdl.SDL_RenderClear(self._ren)
        dst = _SdlRect(0, 0, SCREEN_SIZE[0], SCREEN_SIZE[1])
        self._sdl.SDL_RenderCopy(self._ren, self._tex, ctypes.c_void_p(0), ctypes.byref(dst))
        self._sdl.SDL_RenderPresent(self._ren)
        if delay_ms > 0:
            self._sdl.SDL_Delay(delay_ms)

    def close(self) -> None:
        """Drain queued SFX briefly, close audio, and destroy SDL2 window/renderer."""
        if self._audio_dev > 0:
            self._sdl.SDL_Delay(120)
            self._sdl.SDL_CloseAudioDevice(self._audio_dev)
        self._sdl.SDL_DestroyTexture(self._tex)
        self._sdl.SDL_DestroyRenderer(self._ren)
        self._sdl.SDL_DestroyWindow(self._win)
        self._sdl.SDL_Quit()


def game_thumb(roms_dir: Path, rom: str) -> Image.Image | None:
    """Load the scraped box-art thumbnail for ``rom`` if available."""
    stem = Path(rom).stem
    for candidate in (
        roms_dir / "images" / f"{stem}-thumb.png",
        roms_dir / "images" / f"{stem}-image.png",
    ):
        if candidate.is_file():
            return _load_rgba(candidate, (160, 120), (34, 42, 62, 255))
    return None


def drain_stdin_tokens(buf: str) -> tuple[list[str], str]:
    """Split complete newline-terminated lines from ``buf`` into whitespace-delimited tokens."""
    if "\n" not in buf:
        return [], buf
    parts = buf.split("\n")
    tokens = [tok for line in parts[:-1] for tok in line.split() if tok]
    return tokens, parts[-1]


def selected_rom_name(selected_file: Path) -> str:
    """Return the ROM file name recorded by ``game-selected``, or ``""`` when absent."""
    if not selected_file.is_file():
        return ""
    raw = selected_file.read_text(encoding="utf-8").strip()
    return Path(raw).name if raw else ""


def make_stdin_poller() -> Callable[[], list[str]]:
    """Build a non-blocking ``sys.stdin`` token poller for ``n64mini-topbar``."""
    state = {"buf": ""}

    def _poll() -> list[str]:
        ready, _, _ = select.select([sys.stdin], [], [], 0.0)
        if not ready:
            return []
        chunk = os.read(sys.stdin.fileno(), 256).decode("utf-8", errors="ignore")
        if not chunk:
            return [INPUT_CLOSED]
        tokens, rest = drain_stdin_tokens(state["buf"] + chunk)
        state["buf"] = rest
        return tokens

    return _poll


def main(
    argv: list[str] | None = None,
    *,
    sdl_loader: Callable[[], ctypes.CDLL] | None = None,
    x11_loader: Callable[[], ctypes.CDLL] | None = None,
    stdin_poller: Callable[[], list[str]] | None = None,
) -> int:
    """CLI entry point for ``n64mini-suspend``."""
    parser = argparse.ArgumentParser(description="Open the N64 Classic Mini Suspend Point List.")
    parser.add_argument("--rom", default="", help="ROM file name (defaults to selected card).")
    parser.add_argument("--roms", type=Path, default=Path("/userdata/roms/n64"))
    parser.add_argument("--saves", type=Path, default=SAVES_DIR)
    parser.add_argument("--selected", type=Path, default=SELECTED_FILE)
    parser.add_argument("--theme", type=Path, default=DEFAULT_THEME_DIR)
    parser.add_argument("--render-png", type=Path, default=None)
    args = parser.parse_args(argv)

    rom: str = args.rom or selected_rom_name(args.selected)
    if not rom:
        return 2
    model = load_model(args.saves, rom)
    backdrop = None if args.render_png is not None else capture_x11_backdrop(x11_loader)
    assets = load_suspend_assets(
        args.theme, backdrop=backdrop, fallback_thumb=game_thumb(args.roms, rom)
    )
    if args.render_png is not None:
        compose_suspend_frame(model, assets).save(args.render_png)
        return 0

    loader = sdl_loader or (lambda: ctypes.CDLL("libSDL2-2.0.so.0"))
    try:
        backend = SdlBackend(args.theme, loader())
    except (SuspendError, OSError):
        return 1
    try:
        code, _ = run_interactive_loop(model, assets, backend, stdin_poller or make_stdin_poller())
        return code
    finally:
        backend.close()


if __name__ == "__main__":
    raise SystemExit(main())
