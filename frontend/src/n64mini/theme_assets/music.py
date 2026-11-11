"""Theme sound effects: the suspend-point save chime and the discard "poof" (AC-T13).

Synthesized in Python's standard library with the same N64-style FM bell as the menu music.
The menu music itself is not built here: it comes from the menu-music project as a committed
file (``themes/n64-classic-mini/music/theme.wav``, AC-T12).
"""

from __future__ import annotations

import io
import math
import struct
import wave
from typing import Final

RATE: Final = 22050

_A4: Final = 440.0

_TABLE_SIZE: Final = 4096

_TABLE_MASK: Final = _TABLE_SIZE - 1

_SIN: Final[tuple[float, ...]] = tuple(
    math.sin(2.0 * math.pi * i / _TABLE_SIZE) for i in range(_TABLE_SIZE)
)

_SAW_WAVE: Final[tuple[float, ...]] = tuple(
    sum(
        ((-1.0) ** (k + 1)) * (1.0 / k) * math.sin(2.0 * math.pi * k * i / _TABLE_SIZE)
        for k in range(1, 13)
    )
    * 0.62
    for i in range(_TABLE_SIZE)
)

_PULSE_WAVE: Final[tuple[float, ...]] = tuple(
    sum(
        (2.0 / (k * math.pi))
        * math.sin(0.28 * k * math.pi)
        * math.cos(2.0 * math.pi * k * i / _TABLE_SIZE)
        for k in range(1, 12)
    )
    * 0.85
    for i in range(_TABLE_SIZE)
)


def _freq(note: int) -> float:
    return float(_A4 * (2.0 ** ((note - 69) / 12.0)))


def _osc(phase: float) -> float:
    return _SIN[int(phase * _TABLE_SIZE) & _TABLE_MASK]


def _osc_saw(phase: float) -> float:
    return _SAW_WAVE[int(phase * _TABLE_SIZE) & _TABLE_MASK]


def _osc_pulse(phase: float) -> float:
    return _PULSE_WAVE[int(phase * _TABLE_SIZE) & _TABLE_MASK]


def _envelope(t: float, length: float, attack: float = 0.005, release: float = 0.022) -> float:
    """Smooth attack and release window so synthesized notes never click."""
    return min(1.0, t / attack, max(0.0, length - t) / release)


def _synth_chime(note: int, length: float) -> list[float]:
    """Crystal FM synth-bell layered with a warm detuned PWM synth lead."""
    f = _freq(note)
    n_samples = round(length * RATE)
    out = [0.0] * n_samples
    for i in range(n_samples):
        t = i / RATE
        env = _envelope(t, length, 0.003, 0.025) * math.exp(-3.6 * t)
        mod = 0.36 * math.exp(-13.0 * t) * _osc(t * f * 3.0)
        bell_lo = _osc(t * f + mod) + 0.45 * _osc_pulse(t * f * 0.997)
        bell_hi = _osc(t * f * 2.0 + mod) + 0.30 * _osc_saw(t * f * 1.003)
        out[i] = 0.195 * env * (0.68 * bell_lo + 0.32 * bell_hi)
    return out


def _pack_wav(samples: list[float], peak_gain: float = 0.92) -> bytes:
    """Center, normalize to ``peak_gain``, and pack mono 16-bit PCM WAV bytes."""
    n = len(samples)
    mean_dc = sum(samples) / n
    centered = [s - mean_dc for s in samples]
    peak = max(abs(s) for s in centered) or 1.0
    scale = peak_gain * 32767.0 / peak
    pcm = struct.pack(f"<{n}h", *(round(s * scale) for s in centered))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(pcm)
    return buf.getvalue()


def build_suspend_save_sfx() -> bytes:
    """Synthesize the cartridge-slot lock click + ascending N64 crystal chime WAV (AC-T13)."""
    total = round(0.38 * RATE)
    buf = [0.0] * total
    phase = 0.0
    for i in range(round(0.045 * RATE)):
        t = i / RATE
        freq = 105.0 + 160.0 * math.exp(-55.0 * t)
        phase += freq / RATE
        latch = 0.45 * math.exp(-120.0 * t) * _osc_pulse(t * 680.0)
        buf[i] += 0.55 * math.exp(-38.0 * t) * (_osc(phase) + latch)
    for delay_s, note, dur, gain in (
        (0.018, 76, 0.20, 0.90),  # E5 crystal bell
        (0.082, 83, 0.24, 1.00),  # B5 fifth lock
        (0.115, 88, 0.24, 0.72),  # E6 octave sparkle
    ):
        start = round(delay_s * RATE)
        patch = _synth_chime(note, dur)
        for j, sample in enumerate(patch[: total - start]):
            buf[start + j] += sample * gain
    return _pack_wav(buf, peak_gain=0.88)


def build_suspend_poof_sfx() -> bytes:
    """Synthesize the cartoon puff/vanish ('poof') WAV when discarding a suspend point (AC-T13)."""
    total = round(0.32 * RATE)
    buf = [0.0] * total
    seed = 19960623
    phase = 0.0
    lp = 0.0
    for i in range(total):
        t = i / RATE
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        noise = (seed / 1073741823.5) - 1.0
        cutoff = 360.0 + 2400.0 * math.exp(-11.5 * t)
        alpha = (1.0 / RATE) / ((1.0 / (2.0 * math.pi * cutoff)) + (1.0 / RATE))
        lp += alpha * (noise - lp)
        puff_env = math.sin(min(1.0, t / 0.032) * (math.pi / 2.0)) * math.exp(-9.2 * t)
        secondary = 0.42 * math.exp(-((t - 0.085) ** 2) / 0.0018)
        pop_freq = 64.0 + 195.0 * math.exp(-26.0 * t)
        phase += pop_freq / RATE
        pop = 0.48 * math.exp(-22.0 * t) * _osc(phase)
        buf[i] = (puff_env + secondary) * lp + pop
    return _pack_wav(buf, peak_gain=0.86)
