"""``n64mini.console.library``: the Showcase/Full switch as the card runs it (AC-T14, AC-T20).

``showcase``'s switch, then the strip drawn again for the new list, then the engine reloaded.
Kept apart from ``showcase`` because the strip needs Pillow, and every game launch imports
``showcase`` (``launch.py``); only this detached toggle should pay for loading it.
"""

from __future__ import annotations

import contextlib
import struct
import sys
from pathlib import Path
from typing import Final

from n64mini.console import showcase, strip
from n64mini.console.engine import Engine, EngineError

MUSIC_WAV: Final = Path("themes/n64-classic-mini/music/theme.wav")


def _silent_wav(samples: int = 2205) -> bytes:
    """Build a minimal 22.05 kHz 16-bit mono PCM silent WAV file."""
    data_size = samples * 2
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        22050,
        44100,
        2,
        16,
        b"data",
        data_size,
    )
    return header + (b"\x00\x00" * samples)


def toggle_music(
    userdata: Path = Path("/userdata"),
    *,
    reload: bool = False,
    engine: Engine | None = None,
) -> bool:
    """Toggle background theme music on/off, sync topbar icons, and optionally reload the engine."""
    wav_path = userdata / MUSIC_WAV
    full_path = userdata / strip.MUSIC_FULL
    if not wav_path.is_file() and not full_path.is_file():
        return False
    if full_path.is_file():
        full_path.replace(wav_path)
        music_on = True
    else:
        wav_path.replace(full_path)
        wav_path.write_bytes(_silent_wav())
        music_on = False
    strip.sync_topbar(userdata)
    if reload:
        eng = engine or Engine()
        with contextlib.suppress(EngineError, OSError):
            eng.reload_games()
    return music_on


def redraw_strip(roms_dir: Path) -> str | None:
    """Draw the strips for Showcase mode in ``roms_dir``, or clear them for Full Library mode."""
    userdata = roms_dir.parents[1]
    try:
        if (roms_dir / "gamelist.xml").is_file() and not (
            roms_dir / "gamelist.xml.showcase.json"
        ).is_file():
            strip.sync_topbar(userdata)
            strip.clear(userdata / strip.STRIPS)
        else:
            strip.redraw(userdata)
    except (strip.StripError, OSError) as exc:
        return f"the strip under the cards wasn't drawn: {exc}"
    return None


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ``showcase``'s, with the strip drawn after the switch, plus ``music``."""
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "music":
        do_reload = "--reload" in args[1:]
        ud = Path("/userdata")
        for idx, arg in enumerate(args[1:], start=1):
            if arg == "--userdata" and idx + 1 < len(args):
                ud = Path(args[idx + 1])
        toggle_music(ud, reload=do_reload)
        return 0
    return showcase.main(args, after_switch=redraw_strip)


if __name__ == "__main__":
    raise SystemExit(main())
