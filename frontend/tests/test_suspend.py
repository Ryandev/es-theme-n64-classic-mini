"""AC-T13 / AC-T18: the 4-slot NUS-006 Suspend Point List drawer (n64mini.console.suspend)."""

from __future__ import annotations

import ctypes
import json
import os
import re
import select
import sys
from pathlib import Path

import pytest
from PIL import Image

from n64mini.console import suspend as sp
from n64mini.paths import THEME_DIR

ROM = "Super Mario 64 (U) [!].z64"


class _FakeSink:
    def __init__(self) -> None:
        self.frames: list[tuple[tuple[int, int], int]] = []
        self.sounds: list[sp.SoundKind] = []

    def show(self, frame: Image.Image, delay_ms: int) -> None:
        self.frames.append((frame.size, delay_ms))

    def play(self, kind: sp.SoundKind) -> None:
        self.sounds.append(kind)

    def close(self) -> None:
        pass


class _FakeCall:
    def __init__(self, ret: object = 0) -> None:
        self.ret = ret
        self.argtypes: object = None
        self.restype: object = None
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *args: object) -> object:
        self.calls.append(args)
        return self.ret


class _FakeSdl(ctypes.CDLL):
    def __init__(
        self,
        *,
        video_ret: int = 0,
        audio_init_ret: int = 0,
        audio_dev: int = 2,
    ) -> None:
        # Deliberately bypass ctypes.CDLL.__init__ (no shared library opened in unit tests).
        self.SDL_Init = _FakeCall(video_ret)
        self.SDL_InitSubSystem = _FakeCall(audio_init_ret)
        self.SDL_CreateWindow = _FakeCall(0x101)
        self.SDL_CreateRenderer = _FakeCall(0x202)
        self.SDL_RenderSetLogicalSize = _FakeCall(0)
        self.SDL_CreateTexture = _FakeCall(0x303)
        self.SDL_SetTextureBlendMode = _FakeCall(0)
        self.SDL_OpenAudioDevice = _FakeCall(audio_dev)
        self.SDL_PauseAudioDevice = _FakeCall(0)
        self.SDL_ClearQueuedAudio = _FakeCall(0)
        self.SDL_QueueAudio = _FakeCall(0)
        self.SDL_UpdateTexture = _FakeCall(0)
        self.SDL_RenderClear = _FakeCall(0)
        self.SDL_RenderCopy = _FakeCall(0)
        self.SDL_RenderPresent = _FakeCall(0)
        self.SDL_Delay = _FakeCall(0)
        self.SDL_CloseAudioDevice = _FakeCall(0)
        self.SDL_DestroyTexture = _FakeCall(0)
        self.SDL_DestroyRenderer = _FakeCall(0)
        self.SDL_DestroyWindow = _FakeCall(0)
        self.SDL_Quit = _FakeCall(0)


def test_hover_ease_and_timestamp_helpers() -> None:
    assert sp.ease_out(-0.5) == 0.0
    assert sp.ease_out(0.0) == 0.0
    assert sp.ease_out(0.5) == pytest.approx(0.875)
    assert sp.ease_out(1.0) == 1.0
    assert sp.ease_out(1.5) == 1.0

    offsets = [sp.hover_offset_y(t) for t in range(80)]
    assert offsets[0] == 0
    assert max(offsets) == 5
    assert min(offsets) == -5
    assert re.fullmatch(r"\d{2}/\d{2}  \d{2}:\d{2}", sp.format_slot_timestamp(1_700_000_000.0))


def test_inspect_and_load_model_across_empty_saved_and_pending_states(tmp_path: Path) -> None:
    saves = tmp_path / "saves"
    saves.mkdir()
    assert all(not s.exists for s in sp.inspect_slots(saves, ""))
    assert sp.inspect_pending(saves, "") is None
    assert sp.inspect_pending(saves, ROM) is None

    # Hard power-off resilience: 0-byte truncated .st0 or .pending files are ignored cleanly.
    (saves / f"{ROM}.st0").write_bytes(b"")
    (saves / f"{ROM}.pending").write_bytes(b"")
    assert sp.inspect_slots(saves, ROM)[0].exists is False
    assert sp.inspect_pending(saves, ROM) is None
    (saves / f"{ROM}.st0").unlink()
    (saves / f"{ROM}.pending").unlink()

    empty_model = sp.load_model(saves, ROM)
    assert empty_model.selected_slot == 0
    assert empty_model.pending is None
    assert empty_model.from_reset is False

    # Slot 1 with screenshot, Slot 3 without screenshot; Slot 0 empty -> auto-selects Slot 1.
    (saves / f"{ROM}.st1").write_bytes(b"s1")
    Image.new("RGBA", (32, 24), (20, 80, 180, 255)).save(saves / f"{ROM}.st1.png")
    (saves / f"{ROM}.st3").write_bytes(b"s3")
    browse = sp.load_model(saves, ROM)
    assert browse.selected_slot == 1
    assert browse.slots[1].exists is True
    assert browse.slots[1].shot_path is not None
    assert browse.slots[3].exists is True
    assert browse.slots[3].shot_path is None
    # Explicit initial_slot that already exists keeps its selection.
    assert sp.load_model(saves, ROM, initial_slot=3).selected_slot == 3

    # Pending Reset state without screenshot, then with screenshot -> selects Slot 0 in Reset mode.
    (saves / f"{ROM}.pending").write_bytes(b"pend")
    pend_no_shot = sp.inspect_pending(saves, ROM)
    assert pend_no_shot is not None
    assert pend_no_shot.shot_path is None
    Image.new("RGBA", (32, 24), (200, 60, 40, 255)).save(saves / f"{ROM}.pending.png")
    reset_model = sp.load_model(saves, ROM)
    assert reset_model.selected_slot == 0
    assert reset_model.from_reset is True
    assert reset_model.pending is not None
    assert reset_model.pending.shot_path is not None


def test_commit_discard_and_delete_slot_io(tmp_path: Path) -> None:
    saves = tmp_path / "saves"
    saves.mkdir()
    pend_st = saves / f"{ROM}.pending"
    pend_png = saves / f"{ROM}.pending.png"
    pend_st.write_bytes(b"reset-bytes")
    pend_png.write_bytes(b"reset-png")

    # Commit reset state into Slot 0 moves state + screenshot and removes .pending files.
    pending_reset = sp.PendingState("reset", pend_st, pend_png, "NEW  0:00")
    sp.commit_pending(saves, ROM, pending_reset, 0)
    assert (saves / f"{ROM}.st0").read_bytes() == b"reset-bytes"
    assert (saves / f"{ROM}.st0.png").read_bytes() == b"reset-png"
    assert not pend_st.exists()
    assert not pend_png.exists()

    # Discard when reset files are already absent is safe; discard on a copy keeps source intact.
    sp.discard_pending(sp.PendingState("reset", pend_st, None, "NEW"))
    src_copy = saves / f"{ROM}.st0"
    copy_shotless = sp.PendingState("copy", src_copy, None, "11/09  16:00")
    sp.discard_pending(copy_shotless)
    assert src_copy.is_file()

    # Overwriting Slot 0 (which has .st0.png) with a shotless copy removes the stale .st0.png,
    # and copying into an empty Slot 2 (no .st2.png) writes cleanly without deleting src_copy.
    sp.commit_pending(saves, ROM, copy_shotless, 0)
    assert not (saves / f"{ROM}.st0.png").exists()
    sp.commit_pending(saves, ROM, copy_shotless, 2)
    assert (saves / f"{ROM}.st2").read_bytes() == b"reset-bytes"
    assert src_copy.is_file()

    # Delete slot with screenshot vs missing slot.
    (saves / f"{ROM}.st2.png").write_bytes(b"png2")
    assert sp.delete_slot(saves, ROM, 2) is True
    assert not (saves / f"{ROM}.st2").exists()
    assert not (saves / f"{ROM}.st2.png").exists()
    assert sp.delete_slot(saves, ROM, 2) is False


def test_step_action_transitions_in_floating_and_browse_modes(tmp_path: Path) -> None:
    saves = tmp_path / "saves"
    saves.mkdir()
    (saves / f"{ROM}.pending").write_bytes(b"pending-save")
    model = sp.load_model(saves, ROM)

    # Floating navigation wraps across Slots 0..3; unhandled 'a' is a no-op.
    assert sp.step_action(model, "left").model.selected_slot == 3
    assert sp.step_action(model, "right").model.selected_slot == 1
    assert sp.step_action(model, "a").effect == "none"

    # Start on a Reset pending state stages .st9 and returns a launch resume payload.
    resumed = sp.step_action(model, "start")
    assert resumed.effect == "launch"
    assert resumed.resume == {"rom": ROM, "slot": 9, "state_file": str(saves / f"{ROM}.st9")}
    assert (saves / f"{ROM}.st9").read_bytes() == b"pending-save"

    # Re-stage .pending and save into Slot 1 via C-Down -> plays 'save' sound, enters Browse mode.
    (saves / f"{ROM}.pending").write_bytes(b"slot1-save")
    m_float = sp.step_action(sp.load_model(saves, ROM), "right").model
    saved = sp.step_action(m_float, "c_down")
    assert saved.effect == "saved"
    assert saved.sound == "save"
    assert saved.effect_slot == 1
    assert saved.model.pending is None
    assert saved.model.slots[1].exists is True

    # Browse mode: Left/Right wrap, empty slot ignores A / C-Right / C-Left / C-Down.
    m_empty = sp.step_action(saved.model, "left").model  # Slot 0 (empty)
    assert m_empty.selected_slot == 0
    for noop_act in ("a", "c_right", "c_left", "c_down"):
        assert sp.step_action(m_empty, noop_act).effect == "none"

    # Occupied Slot 1: A launches, C-Right lifts a floating copy (Start is no-op, B poofs).
    m_occ = sp.step_action(m_empty, "right").model
    launched = sp.step_action(m_occ, "a")
    assert launched.effect == "launch"
    assert launched.resume == {"rom": ROM, "slot": 1, "state_file": str(saves / f"{ROM}.st1")}

    copied = sp.step_action(m_occ, "c_right").model
    assert copied.pending is not None
    assert copied.pending.kind == "copy"
    assert sp.step_action(copied, "start").effect == "none"
    cancelled = sp.step_action(copied, "b")
    assert cancelled.effect == "poof_pending"
    assert cancelled.sound == "poof"
    assert (saves / f"{ROM}.st1").is_file()  # source slot preserved on copy cancel

    # Browse mode: C-Left poof-deletes Slot 1; B closes the drawer.
    deleted = sp.step_action(cancelled.model, "c_left")
    assert deleted.effect == "poof_slot"
    assert deleted.sound == "poof"
    assert not (saves / f"{ROM}.st1").exists()
    assert sp.step_action(deleted.model, "b").effect == "close"


def test_compose_suspend_frame_and_keyframes_with_real_and_fallback_assets(
    tmp_path: Path,
) -> None:
    saves = tmp_path / "saves"
    saves.mkdir()
    (saves / f"{ROM}.st0").write_bytes(b"s0")
    shot0 = saves / f"{ROM}.st0.png"
    Image.new("RGBA", (64, 48), (40, 120, 210, 255)).save(shot0)
    (saves / f"{ROM}.st1").write_bytes(b"s1")  # no screenshot -> uses fallback_thumb
    (saves / f"{ROM}.pending").write_bytes(b"pend")

    # 1. Fallback assets when theme directory is empty (covers default font + solid RGBA fallbacks).
    empty_theme = tmp_path / "empty_theme"
    (empty_theme / "art").mkdir(parents=True)
    # Place one oversized PNG to exercise _load_rgba resize branch.
    Image.new("RGBA", (64, 64), (10, 20, 30, 255)).save(empty_theme / "art" / "btn_a.png")
    fallback_assets = sp.load_suspend_assets(empty_theme)
    reset_model = sp.load_model(saves, ROM)
    f_pending = sp.compose_suspend_frame(reset_model, fallback_assets, tick=10)
    assert f_pending.size == sp.SCREEN_SIZE

    # 2. Real theme assets: compose Reset floating, Copy floating, Browse, Drop, and Poof frames.
    real_assets = sp.load_suspend_assets(THEME_DIR)
    frame_reset = sp.compose_suspend_frame(reset_model, real_assets, tick=0)
    assert frame_reset.size == sp.SCREEN_SIZE

    # Copy floating mode ('Cancel' prompt without 'Resume Game').
    copy_model = sp.SuspendModel(
        rom=ROM,
        saves_dir=saves,
        selected_slot=2,
        slots=reset_model.slots,
        pending=sp.PendingState("copy", saves / f"{ROM}.st0", shot0, "11/09  16:00"),
        from_reset=False,
    )
    assert sp.compose_suspend_frame(copy_model, real_assets, tick=5).size == sp.SCREEN_SIZE

    # Save drop keyframes (with flash_alpha == 0 and flash_alpha > 0).
    drops = sp.save_drop_frames(0, start_dy=-3, steps=6)
    assert drops[0].flash_alpha == 0
    assert any(d.flash_alpha > 0 for d in drops)
    assert drops[-1].y == sp.SLOT_Y
    browse_model = sp.SuspendModel(ROM, saves, 0, reset_model.slots, None, from_reset=False)
    for dstep in (drops[0], drops[-2]):
        img_drop = sp.compose_suspend_frame(browse_model, real_assets, overlay=dstep)
        assert img_drop.size == sp.SCREEN_SIZE

    # Poof keyframes (early shrinking ghost + opaque puffs, late alpha==0 ghost + faded puffs).
    psteps = sp.poof_frames((272, 545), steps=10)
    assert psteps[0].cart_alpha > 0
    assert psteps[-1].cart_alpha == 0
    assert psteps[-1].puffs[0].alpha == 0
    for pstep in (psteps[0], psteps[-1]):
        poof_ov = sp.PoofOverlay(step=pstep, ghost=real_assets.cart_selected, center=(272, 545))
        img = sp.compose_suspend_frame(browse_model, real_assets, overlay=poof_ov)
        assert img.size == sp.SCREEN_SIZE


def test_stdin_tokens_rom_helpers_and_x11_backdrop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert sp.parse_command_token("  SAVE\n") == "c_down"
    assert sp.parse_command_token("copy") == "c_right"
    assert sp.parse_command_token("delete") == "c_left"
    assert sp.parse_command_token("enter") == "a"
    assert sp.parse_command_token("esc") == "b"
    assert sp.parse_command_token("unknown") is None

    assert sp.drain_stdin_tokens("lef") == ([], "lef")
    assert sp.drain_stdin_tokens("left  c_down\nri") == (["left", "c_down"], "ri")

    sel = tmp_path / "selected"
    assert sp.selected_rom_name(sel) == ""
    sel.write_text("   \n", encoding="utf-8")
    assert sp.selected_rom_name(sel) == ""
    sel.write_text(f"/userdata/roms/n64/{ROM}\n", encoding="utf-8")
    assert sp.selected_rom_name(sel) == ROM

    roms = tmp_path / "roms"
    (roms / "images").mkdir(parents=True)
    assert sp.game_thumb(roms, ROM) is None
    img_path = roms / "images" / f"{Path(ROM).stem}-image.png"
    Image.new("RGBA", (40, 30), (90, 40, 20, 255)).save(img_path)
    thumb = sp.game_thumb(roms, ROM)
    assert thumb is not None
    assert thumb.size == (160, 120)

    # make_stdin_poller: not ready -> [], chunk -> tokens, EOF b"" -> INPUT_CLOSED (never "b").
    chunks = [b"left\nc_down\n", b""]
    ready_flags = [False, True, True]

    class _FakeStdin:
        def fileno(self) -> int:
            return 0

    def _fake_read(_fd: int, _n: int) -> bytes:
        return chunks.pop(0)

    def _fake_select(*_args: object) -> tuple[list[int], list[int], list[int]]:
        return ([0], [], []) if ready_flags.pop(0) else ([], [], [])

    monkeypatch.setattr(sys, "stdin", _FakeStdin())
    monkeypatch.setattr(os, "read", _fake_read)
    monkeypatch.setattr(select, "select", _fake_select)
    poller = sp.make_stdin_poller()
    assert poller() == []
    assert poller() == ["left", "c_down"]
    assert poller() == [sp.INPUT_CLOSED]

    # capture_x11_backdrop: OSError, null display, null XImage, 1280x720, and scaled 640x360.
    def _no_x11() -> ctypes.CDLL:
        raise OSError("no libX11")

    assert sp.capture_x11_backdrop(_no_x11) is None

    class _FakeX11(ctypes.CDLL):
        def __init__(
            self,
            *,
            dpy: int = 1,
            width: int = 1280,
            height: int = 720,
            has_img: bool = True,
        ) -> None:
            self._buf = ctypes.create_string_buffer(b"\x20\x40\x80\x00" * (width * height))
            self._ximg = sp.XImage(width=width, height=height, data=ctypes.addressof(self._buf))
            self.XOpenDisplay = _FakeCall(dpy)
            self.XDefaultRootWindow = _FakeCall(10)
            self.XDisplayWidth = _FakeCall(width)
            self.XDisplayHeight = _FakeCall(height)
            ptr = ctypes.pointer(self._ximg) if has_img else ctypes.POINTER(sp.XImage)()
            self.XGetImage = _FakeCall(ptr)
            self.XDestroyImage = _FakeCall(0)
            self.XCloseDisplay = _FakeCall(0)

    assert sp.capture_x11_backdrop(lambda: _FakeX11(dpy=0)) is None
    assert sp.capture_x11_backdrop(lambda: _FakeX11(has_img=False)) is None
    cap_720 = sp.capture_x11_backdrop(lambda: _FakeX11(width=1280, height=720))
    assert cap_720 is not None
    assert cap_720.size == sp.SCREEN_SIZE
    assert sp.load_suspend_assets(THEME_DIR, backdrop=cap_720).backdrop is cap_720
    cap_scaled = sp.capture_x11_backdrop(lambda: _FakeX11(width=640, height=360))
    assert cap_scaled is not None
    assert cap_scaled.size == sp.SCREEN_SIZE


def test_sdl_backend_video_and_pcm_audio(tmp_path: Path) -> None:
    with pytest.raises(sp.SuspendError, match="SDL_Init video failed"):
        sp.SdlBackend(THEME_DIR, _FakeSdl(video_ret=-1))

    for bad_audio_sdl in (_FakeSdl(audio_init_ret=-1), _FakeSdl(audio_dev=0)):
        backend_silent = sp.SdlBackend(THEME_DIR, bad_audio_sdl)
        backend_silent.play("save")
        backend_silent.show(Image.new("RGBA", sp.SCREEN_SIZE), 0)
        backend_silent.close()
        assert len(bad_audio_sdl.SDL_QueueAudio.calls) == 0
        assert len(bad_audio_sdl.SDL_CloseAudioDevice.calls) == 0

    empty_theme = tmp_path / "no_sounds_theme"
    empty_theme.mkdir()
    sdl_no_wav = _FakeSdl()
    backend_no_wav = sp.SdlBackend(empty_theme, sdl_no_wav)
    backend_no_wav.play("save")
    backend_no_wav.close()
    assert len(sdl_no_wav.SDL_CloseAudioDevice.calls) == 1

    sdl_ok = _FakeSdl()
    backend_ok = sp.SdlBackend(THEME_DIR, sdl_ok)
    backend_ok.play("save")
    backend_ok.play("poof")
    backend_ok.show(Image.new("RGBA", sp.SCREEN_SIZE), 16)
    backend_ok.close()
    assert len(sdl_ok.SDL_QueueAudio.calls) == 2
    assert len(sdl_ok.SDL_Delay.calls) == 2


def test_interactive_loop_and_cli_main(tmp_path: Path) -> None:
    saves = tmp_path / "saves"
    saves.mkdir()
    empty_theme = tmp_path / "empty_theme"
    empty_theme.mkdir()
    assets = sp.load_suspend_assets(empty_theme)

    (saves / f"{ROM}.pending").write_bytes(b"from-reset")
    sink_discard = _FakeSink()
    poll_discard = iter([[], ["left"], ["noop", "b"]])
    rc_discard, _ = sp.run_interactive_loop(
        sp.load_model(saves, ROM),
        assets,
        sink_discard,
        lambda: next(poll_discard),
    )
    assert rc_discard == sp.EXIT_CLOSED
    assert sink_discard.sounds == ["poof"]

    (saves / f"{ROM}.pending").write_bytes(b"save-then-play")
    (saves / f"{ROM}.st2").write_bytes(b"slot-two")
    sink_full = _FakeSink()
    resume_file = tmp_path / "resume.json"
    poll_full = iter([["c_down", "c_right", "b", "right", "right", "c_left", "left", "left", "a"]])
    rc_launch, _ = sp.run_interactive_loop(
        sp.load_model(saves, ROM),
        assets,
        sink_full,
        lambda: next(poll_full),
        resume_file=resume_file,
    )
    assert rc_launch == sp.EXIT_LAUNCH
    assert sink_full.sounds == ["save", "poof", "poof"]
    assert json.loads(resume_file.read_text(encoding="utf-8"))["slot"] == 0

    assert sp.main(["--selected", str(tmp_path / "none")]) == 2
    out_png = tmp_path / "rendered.png"
    assert (
        sp.main(
            [
                "--rom",
                ROM,
                "--saves",
                str(saves),
                "--theme",
                str(empty_theme),
                "--render-png",
                str(out_png),
            ]
        )
        == 0
    )
    assert out_png.is_file()

    def _fail_sdl() -> ctypes.CDLL:
        raise OSError("no SDL2")

    assert (
        sp.main(
            ["--rom", ROM, "--saves", str(saves), "--theme", str(empty_theme)],
            sdl_loader=_fail_sdl,
            x11_loader=_fail_sdl,
        )
        == 1
    )

    sdl_cli = _FakeSdl()
    assert (
        sp.main(
            ["--rom", ROM, "--saves", str(saves), "--theme", str(empty_theme)],
            sdl_loader=lambda: sdl_cli,
            x11_loader=_fail_sdl,
            stdin_poller=lambda: ["b"],
        )
        == 0
    )
    assert len(sdl_cli.SDL_Quit.calls) == 1


def test_closed_input_closes_the_drawer_without_deleting_the_reset_suspend_point(
    tmp_path: Path,
) -> None:
    # B1: the poller used to turn EOF into "b", which in Pending Save mode deletes the state.
    (tmp_path / f"{ROM}.pending").write_bytes(b"from-reset")
    shot = tmp_path / f"{ROM}.pending.png"
    Image.new("RGB", (32, 24), (10, 20, 30)).save(shot)
    shot_bytes = shot.read_bytes()
    sink = _FakeSink()
    # A "b" queued behind the closure must never be reached.
    polls = iter([[sp.INPUT_CLOSED, "b"]])
    rc, model = sp.run_interactive_loop(
        sp.load_model(tmp_path, ROM),
        sp.load_suspend_assets(tmp_path),
        sink,
        lambda: next(polls),
    )
    assert rc == sp.EXIT_CLOSED
    assert sink.sounds == []
    assert model.pending is not None
    assert (tmp_path / f"{ROM}.pending").read_bytes() == b"from-reset"
    assert shot.read_bytes() == shot_bytes
