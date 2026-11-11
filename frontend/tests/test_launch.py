"""AC-T15: the N64 launch wrapper runs the port for a flipped game, else the emulator unchanged."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import ClassVar, override

import pytest
from PIL import Image

from n64mini.console import engine as en
from n64mini.console import launch as ln
from n64mini.console.flip import BADGE_DIR, MODES_FILE
from n64mini.console.showcase import CARD

PORTED = "Mario Kart 64 (U) [!].z64"
CATALOG = {
    "games": [
        {"title": "MK64", "rom": "^Mario Kart 64", "showcase": True, "ports": [{"name": "Kart"}]}
    ]
}
SCRIPT = Path(ln.__file__).with_name("data") / "n64mini-launch.sh"


@pytest.fixture
def console(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A ROM folder, a catalogue and a ports file path (not yet written)."""
    roms = tmp_path / "roms"
    (roms / BADGE_DIR).mkdir(parents=True)
    (roms / PORTED).write_bytes(b"")
    catalog = tmp_path / "games.json"
    catalog.write_text(json.dumps(CATALOG))
    return roms, catalog, tmp_path / "ports.json"


def _flip_to_port(roms: Path) -> None:
    (roms / BADGE_DIR / MODES_FILE).write_text(json.dumps({PORTED: "port"}))


def _args(roms: Path) -> list[str]:
    return ["-system", "n64", "-rom", str(roms / PORTED), "-emulator", "libretro"]


def test_an_unflipped_game_goes_to_emulatorlauncher_with_saves_pinned_to_slot_9(
    console: tuple[Path, Path, Path],
) -> None:
    # B5: without the pin mupen64plus saves into the slot it last used, a player's .st0..3
    # included (seen on the VM: the first Reset wrote .st2).
    roms, catalog, ports = console
    decision = ln.decide(_args(roms), roms, ports, catalog)
    assert decision == {
        "mode": "emulator",
        "rom": PORTED,
        "command": ["emulatorlauncher", *_args(roms), "-state_slot", "9"],
    }
    # The engine adds its own next free slot to a plain launch; that is replaced, not kept.
    engine_slot = [*_args(roms), "-state_slot", "2"]
    assert ln.decide(engine_slot, roms, ports, catalog)["command"] == [
        "emulatorlauncher",
        *_args(roms),
        "-state_slot",
        "9",
    ]


def test_a_flipped_game_runs_its_installed_port(console: tuple[Path, Path, Path]) -> None:
    roms, catalog, ports = console
    _flip_to_port(roms)
    ports.write_text(json.dumps({"MK64": ["/ports/kart/SpaghettiKart", "--rom", "{rom}"]}))
    decision = ln.decide(_args(roms), roms, ports, catalog)
    assert decision["command"] == ["/ports/kart/SpaghettiKart", "--rom", str(roms / PORTED)]
    assert decision["mode"] == "port"


@pytest.mark.parametrize(
    ("ports_json", "match"),
    [
        (None, "MK64: its PC port isn't installed. Flip the card back to N64"),
        ('{"MK64": []}', "non-empty command list"),
        ('{"MK64": ["a", 1]}', "non-empty command list"),
        ('["x"]', "non-empty command list"),
    ],
)
def test_a_flipped_game_without_a_usable_port_fails_instead_of_falling_back(
    console: tuple[Path, Path, Path], ports_json: str | None, match: str
) -> None:
    roms, catalog, ports = console
    _flip_to_port(roms)
    if ports_json is not None:
        ports.write_text(ports_json)
    with pytest.raises(ln.LaunchError, match=match):
        ln.decide(_args(roms), roms, ports, catalog)


def test_a_flipped_game_missing_from_the_catalogue_fails(
    console: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    roms, _, ports = console
    _flip_to_port(roms)
    empty = tmp_path / "empty.json"
    empty.write_text('{"games": []}')
    with pytest.raises(ln.LaunchError, match="isn't in the catalogue"):
        ln.decide(_args(roms), roms, ports, empty)


def test_launch_arguments_must_name_a_rom() -> None:
    with pytest.raises(ln.LaunchError, match="no -rom"):
        ln.rom_argument(["-system", "n64"])
    with pytest.raises(ln.LaunchError, match="no -rom"):
        ln.rom_argument(["-rom"])


class _Screen(en.Engine):
    """Records what would go on screen; can be unreachable."""

    shown: ClassVar[list[str]] = []
    down = False

    @override
    def notify(self, message: str) -> None:
        if _Screen.down:
            raise ConnectionRefusedError("no engine")
        _Screen.shown.append(message)


@pytest.fixture
def screen(monkeypatch: pytest.MonkeyPatch) -> type[_Screen]:
    monkeypatch.setattr(ln, "Engine", _Screen)
    monkeypatch.setattr(_Screen, "shown", [])
    monkeypatch.setattr(_Screen, "down", False)
    return _Screen


def test_main_prints_the_command_or_records_it(
    console: tuple[Path, Path, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    screen: type[_Screen],
) -> None:
    roms, _, _ = console
    assert ln.main(_args(roms)) == 0
    assert capsys.readouterr().out.split("\0")[:-1] == [
        "emulatorlauncher",
        *_args(roms),
        "-state_slot",
        "9",
    ]
    log = tmp_path / "launches.jsonl"
    ports = tmp_path / "ports.json"
    assert ln.main(["--ports", str(ports), "--dry-run", str(log), *_args(roms)]) == 0
    assert json.loads(log.read_text())["mode"] == "emulator"
    assert ln.main(["-system", "n64"]) == 1
    assert "no -rom" in capsys.readouterr().err
    assert screen.shown == ["no -rom in the launch arguments"]  # on screen too, not just the log
    assert ln.main(["--dry-run", str(log), "-system", "n64"]) == 1  # failures are recorded too
    assert len(screen.shown) == 1  # the harness records instead of showing
    assert json.loads(log.read_text().splitlines()[-1]) == {
        "mode": "error",
        "error": "no -rom in the launch arguments",
        "args": ["-system", "n64"],
    }


def test_a_windows_rom_path_maps_to_its_folder_under_wsl() -> None:
    """The engine harness runs the Windows engine; it passes Windows paths."""
    rom = "C:\\Users\\me\\AppData\\Local\\Temp\\h\\roms\\n64\\Mario Kart 64 (U) [!].z64"
    assert ln.roms_dir_of(rom) == Path("/mnt/c/Users/me/AppData/Local/Temp/h/roms/n64")
    assert ln.rom_file_name(rom) == PORTED


def test_the_shell_wrapper_execs_exactly_the_chosen_command(
    console: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    """End to end: emulatorlauncher gets Batocera's arguments plus the save-slot pin."""
    roms, _, _ = console
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    received = tmp_path / "received"
    stub = bin_dir / "emulatorlauncher"
    stub.write_text(f'#!/bin/bash\nprintf "%s\\0" "$@" > "{received}"\n')
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{Path(sys.executable).parent}:{os.environ['PATH']}"}
    result = subprocess.run(
        [str(SCRIPT), *_args(roms)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    sent = [*_args(roms), "-state_slot", "9"]
    assert received.read_bytes().split(b"\0")[:-1] == [a.encode() for a in sent]


def test_a_failure_with_no_engine_to_show_it_stays_in_the_log(
    capsys: pytest.CaptureFixture[str], screen: type[_Screen]
) -> None:
    screen.down = True
    assert ln.main(["-system", "n64"]) == 1
    err = capsys.readouterr().err
    assert "n64mini-launch: no -rom in the launch arguments" in err
    assert "n64mini-launch: couldn't show it on screen: no engine" in err


def test_the_library_card_switches_the_list_instead_of_launching(
    console: tuple[Path, Path, Path],
) -> None:
    """AC-T14: the card runs the toggle detached (the launch must end so the engine saves its
    gamelist first), with this Python, and reloads the engine."""
    roms, catalog, ports = console
    args = ["-system", "n64", "-rom", str(roms / CARD)]
    assert ln.decide(args, roms, ports, catalog) == {
        "mode": "library",
        "rom": CARD,
        "command": [
            *("setsid", "-f", sys.executable, "-m", "n64mini.console.library", "toggle"),
            *("--after-game", "--reload", "--roms", str(roms)),
        ],
    }


def test_flip_expand_frames_flip_centre_card_and_ease_out_to_full_screen() -> None:
    """AC-T17: the selected centre card flips over, expands with ease-out, and cross-fades."""
    frames = ln.flip_expand_frames(flip_steps=8, expand_steps=12, crossfade_steps=6)
    flip = [f for f in frames if f["phase"] == "flip"]
    expand = [f for f in frames if f["phase"] == "expand"]
    crossfade = [f for f in frames if f["phase"] == "crossfade"]
    assert len(flip) == 8
    assert len(expand) == 12
    assert len(crossfade) == 6
    assert [f["face"] for f in flip] == ["front"] * 4 + ["back"] * 4
    assert flip[3]["w"] == 2  # edge-on at the midpoint of the flip
    assert (flip[-1]["x"], flip[-1]["y"], flip[-1]["w"], flip[-1]["h"]) == ln.CARD_BOX
    # Cubic ease-out covers 87.5% of the expansion by the halfway mark and finishes full screen.
    mid = expand[len(expand) // 2 - 1]
    assert mid["w"] > ln.CARD_BOX[2] + 0.85 * (ln.SCREEN_BOX[2] - ln.CARD_BOX[2])
    assert (expand[-1]["x"], expand[-1]["y"], expand[-1]["w"], expand[-1]["h"]) == ln.SCREEN_BOX
    assert expand[-1]["alpha"] == 255
    assert [f["face"] for f in crossfade] == ["splash"] * 6
    assert crossfade[0]["alpha"] < crossfade[-1]["alpha"] == 255


def test_compose_and_render_flip_frames_across_all_phases(tmp_path: Path) -> None:
    """AC-T17: compose_launch_surfaces resolves box art and render_flip_frame draws each phase."""
    roms = tmp_path / "roms"
    images = roms / "images"
    images.mkdir(parents=True)
    rom = roms / PORTED
    rom.write_bytes(b"")
    theme = tmp_path / "theme"
    art = theme / "art"
    art.mkdir(parents=True)

    # 1. Missing theme files and missing game art use clean solid fallbacks.
    surfaces_fallback = ln.compose_launch_surfaces(rom, theme)
    assert surfaces_fallback.bg.size == (1280, 720)
    assert surfaces_fallback.front.size == (234, 268)

    # 2. Fallback to images/<stem>-thumb.png when gamelist.xml has no matching entry.
    thumb_fallback = images / "Mario Kart 64 (U) [!]-thumb.png"
    Image.new("RGBA", (64, 64), (200, 40, 40, 255)).save(thumb_fallback)
    (roms / "gamelist.xml").write_text(
        "<gameList>"
        "<game><path>./Other.z64</path><thumbnail>./images/other.png</thumbnail></game>"
        f"<game><path>./{PORTED}</path></game>"
        f"<game><path>./{PORTED}</path><thumbnail>./images/missing.png</thumbnail></game>"
        "</gameList>",
        encoding="utf-8",
    )
    assert ln.compose_launch_surfaces(rom, theme).front.getpixel((117, 117)) == (200, 40, 40, 255)

    # 3. Gamelist relative and absolute thumbnail resolution + full theme art layers.
    gamelist_art = images / "mk64-box.png"
    Image.new("RGBA", (64, 64), (40, 180, 80, 255)).save(gamelist_art)
    (roms / "gamelist.xml").write_text(
        f"<gameList><game><path>./{PORTED}</path>"
        "<thumbnail>./images/mk64-box.png</thumbnail></game></gameList>",
        encoding="utf-8",
    )
    for name, sz, color in (
        ("n64_classic_bg.png", (1280, 720), (20, 24, 32, 255)),
        ("brand.png", (1200, 82), (10, 10, 10, 255)),
        ("console.png", (1200, 82), (45, 48, 54, 255)),
        ("launch_splash.png", (1280, 720), (0, 0, 0, 255)),
        ("tile_selected.png", (234, 268), (240, 196, 48, 255)),
    ):
        Image.new("RGBA", sz, color).save(art / name)
    surfaces = ln.compose_launch_surfaces(rom, theme)
    assert surfaces.front.getpixel((117, 117)) == (40, 180, 80, 255)

    frames = ln.flip_expand_frames(flip_steps=4, expand_steps=4, crossfade_steps=2)
    rendered = [ln.render_flip_frame(f, surfaces) for f in frames]
    assert all(img.size == (1280, 720) for img in rendered)


class _FakeFunc:
    def __init__(self, ret: int = 1) -> None:
        self.ret = ret
        self.restype: object = None
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *args: object) -> int:
        self.calls.append(args)
        return self.ret


class _FakeSdl:
    def __init__(self, init_ret: int = 0) -> None:
        self.SDL_Init = _FakeFunc(init_ret)
        self.SDL_CreateWindow = _FakeFunc(0x100)
        self.SDL_CreateRenderer = _FakeFunc(0x200)
        self.SDL_RenderSetLogicalSize = _FakeFunc(0)
        self.SDL_CreateTexture = _FakeFunc(0x300)
        self.SDL_UpdateTexture = _FakeFunc(0)
        self.SDL_SetTextureBlendMode = _FakeFunc(0)
        self.SDL_SetTextureAlphaMod = _FakeFunc(0)
        self.SDL_SetRenderDrawColor = _FakeFunc(0)
        self.SDL_RenderClear = _FakeFunc(0)
        self.SDL_RenderCopy = _FakeFunc(0)
        self.SDL_RenderPresent = _FakeFunc(0)
        self.SDL_Delay = _FakeFunc(0)
        self.SDL_DestroyTexture = _FakeFunc(0)
        self.SDL_DestroyRenderer = _FakeFunc(0)
        self.SDL_DestroyWindow = _FakeFunc(0)
        self.SDL_Quit = _FakeFunc(0)


def test_sdl_canvas_and_animate_cli_entry_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-T17: _SdlCanvas uploads all 4 surfaces, renders flip/expand/crossfade/hold, and closes."""
    rom = tmp_path / PORTED
    rom.write_bytes(b"")
    surfaces = ln.compose_launch_surfaces(rom, tmp_path / "theme")
    fake = _FakeSdl(init_ret=0)
    current: list[_FakeSdl] = [fake]

    def _cdll(_name: str) -> _FakeSdl:
        return current[0]

    monkeypatch.setattr(ctypes, "CDLL", _cdll)
    frames = ln.flip_expand_frames(flip_steps=2, expand_steps=2, crossfade_steps=2)
    ok = ln.play_sdl_animation(surfaces, frames, hold_steps=2, frame_ms=1)
    assert ok is True
    assert len(fake.SDL_CreateTexture.calls) == 4
    assert len(fake.SDL_RenderPresent.calls) == len(frames) + 2
    assert len(fake.SDL_DestroyTexture.calls) == 4
    assert len(fake.SDL_Quit.calls) == 1

    # SDL_Init failure returns False cleanly without crashing the launch.
    current[0] = _FakeSdl(init_ret=-1)
    assert ln.play_sdl_animation(surfaces, frames) is False

    current[0] = _FakeSdl(0)
    assert ln.main(["--animate", "-rom", str(rom)]) == 0
    assert ln.main(["--animate", str(rom)]) == 0
    assert ln.main(["--animate"]) == 1
    assert ln.main(["--animate", "-rom"]) == 1


def test_consume_resume_args_and_decide_resume_slot(
    console: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    """AC-T13: launching a suspend slot passes -state_slot 9 -state_filename once and unlinks."""
    roms, catalog, ports = console
    resume = tmp_path / "resume.json"
    assert ln.consume_resume_args(PORTED, resume) == []

    # Corrupt JSON, non-dict JSON, wrong ROM, or missing state_file all return [] and unlink.
    for payload in (
        "{corrupt",
        "[1, 2]",
        json.dumps({"rom": "Other.z64", "slot": 0, "state_file": "/nope"}),
        json.dumps({"rom": PORTED, "slot": 0, "state_file": 123}),
        json.dumps({"rom": PORTED, "slot": 0, "state_file": str(tmp_path / "missing.st0")}),
    ):
        resume.write_text(payload, encoding="utf-8")
        assert ln.consume_resume_args(PORTED, resume) == []
        assert not resume.exists()

    st1 = tmp_path / f"{PORTED}.st1"
    st1.write_bytes(b"saved-state")
    resume.write_text(
        json.dumps({"rom": PORTED, "slot": 1, "state_file": str(st1)}), encoding="utf-8"
    )
    decision = ln.decide(_args(roms), roms, ports, catalog, resume_file=resume)
    assert decision["command"] == [
        "emulatorlauncher",
        *_args(roms),
        "-state_slot",
        "9",
        "-state_filename",
        str(st1),
    ]
    assert not resume.exists()  # one-shot consumption

    # Pending floating state (slot=None) also pins -state_slot 9 and passes -state_filename.
    resume.write_text(
        json.dumps({"rom": PORTED, "slot": None, "state_file": str(st1)}), encoding="utf-8"
    )
    assert ln.consume_resume_args(PORTED, resume) == [
        "-state_slot",
        "9",
        "-state_filename",
        str(st1),
    ]

    # Explicit -state_filename from caller is preserved without consuming resume.json.
    resume.write_text(
        json.dumps({"rom": PORTED, "slot": 2, "state_file": str(st1)}), encoding="utf-8"
    )
    explicit = [*_args(roms), "-state_filename", "/explicit/state.st0"]
    dec_explicit = ln.decide(explicit, roms, ports, catalog, resume_file=resume)
    assert dec_explicit["command"] == ["emulatorlauncher", *explicit, "-state_slot", "9"]
    assert resume.is_file()
