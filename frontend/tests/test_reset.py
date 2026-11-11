"""AC-T18: the Reset-button handler (n64mini.console.reset) and slot compaction."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, override

from n64mini.console import reset as rs
from n64mini.console.engine import Engine, EngineError
from n64mini.console.flip import BADGE_DIR, MODES_FILE

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class _FakeEngine(Engine):
    def __init__(self, *, running: bool = False, fail: bool = False) -> None:
        super().__init__()
        self._running = running
        self._fail = fail

    @override
    def game_running(self) -> bool:
        if self._fail:
            raise EngineError("engine unreachable")
        return self._running


def test_selected_rom_reads_filename_or_empty_when_missing(tmp_path: Path) -> None:
    missing = tmp_path / "no-selected"
    assert rs.selected_rom(missing) == ""
    empty = tmp_path / "empty"
    empty.write_text("   \n", encoding="utf-8")
    assert rs.selected_rom(empty) == ""
    selected = tmp_path / "selected"
    selected.write_text("/userdata/roms/n64/Bomberman 64 (U) [!].z64\n", encoding="utf-8")
    assert rs.selected_rom(selected) == "Bomberman 64 (U) [!].z64"


def test_decide_returns_idle_outside_a_running_game_or_when_engine_fails(tmp_path: Path) -> None:
    roms = tmp_path / "roms"
    roms.mkdir()
    selected = tmp_path / "selected"
    selected.write_text("/userdata/roms/n64/Bomberman 64 (U) [!].z64", encoding="utf-8")
    assert rs.decide(_FakeEngine(running=False), roms, selected) == "idle"
    assert rs.decide(_FakeEngine(fail=True), roms, selected) == "idle"


def test_decide_distinguishes_emulator_and_port_when_a_game_runs(tmp_path: Path) -> None:
    roms = tmp_path / "roms"
    media = roms / BADGE_DIR
    media.mkdir(parents=True)
    selected = tmp_path / "selected"
    rom = "Bomberman 64 (U) [!].z64"
    # No selected file yet -> defaults to emulator
    assert rs.decide(_FakeEngine(running=True), roms, selected) == "emulator"

    selected.write_text(f"/userdata/roms/n64/{rom}", encoding="utf-8")
    assert rs.decide(_FakeEngine(running=True), roms, selected) == "emulator"

    # Flipped to port -> returns "port"
    (media / MODES_FILE).write_text(json.dumps({rom: "port"}), encoding="utf-8")
    assert rs.decide(_FakeEngine(running=True), roms, selected) == "port"

    # Corrupt modes.json -> falls back to "emulator" rather than crashing the Reset button
    (media / MODES_FILE).write_text("{corrupt", encoding="utf-8")
    assert rs.decide(_FakeEngine(running=True), roms, selected) == "emulator"


def test_compact_slots_renumbers_states_and_screenshots_from_slot_zero(tmp_path: Path) -> None:
    saves = tmp_path / "saves"
    rom = "Bomberman 64 (U) [!].z64"
    assert rs.compact_slots(saves, rom) == 0
    saves.mkdir()
    assert rs.compact_slots(saves, "") == 0

    st0 = saves / f"{rom}.st0"
    st2 = saves / f"{rom}.st2"
    st2_png = saves / f"{rom}.st2.png"
    st5 = saves / f"{rom}.st5"
    st0.write_bytes(b"first")
    st2.write_bytes(b"second")
    st2_png.write_bytes(b"png-second")
    st5.write_bytes(b"third")
    os.utime(st0, ns=(1_000_000_000, 1_000_000_000))
    os.utime(st2, ns=(2_000_000_000, 2_000_000_000))
    os.utime(st5, ns=(3_000_000_000, 3_000_000_000))

    assert rs.compact_slots(saves, rom) == 3
    assert (saves / f"{rom}.st0").read_bytes() == b"first"
    assert (saves / f"{rom}.st1").read_bytes() == b"second"
    assert (saves / f"{rom}.st1.png").read_bytes() == b"png-second"
    assert (saves / f"{rom}.st2").read_bytes() == b"third"
    assert not (saves / f"{rom}.st2.png").exists()
    assert not (saves / f"{rom}.st5").exists()


def test_pin_mupen_slot_updates_current_state_slot_when_present(tmp_path: Path) -> None:
    cfg = tmp_path / "mupen64plus.cfg"
    assert rs.pin_mupen_slot(cfg) is False
    cfg.write_text("[Core]\nAutoStateSlotIncrement = True\n", encoding="utf-8")
    assert rs.pin_mupen_slot(cfg) is False
    cfg.write_text(
        "[Core]\nCurrentStateSlot = 1\nAutoStateSlotIncrement = True\n",
        encoding="utf-8",
    )
    assert rs.pin_mupen_slot(cfg, 8) is True
    assert "CurrentStateSlot = 8" in cfg.read_text(encoding="utf-8")


def test_cli_decide_compact_and_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    roms = tmp_path / "roms"
    saves = tmp_path / "saves"
    roms.mkdir()
    saves.mkdir()
    rom = "Bomberman 64 (U) [!].z64"
    selected = tmp_path / "selected"
    selected.write_text(f"/userdata/roms/n64/{rom}", encoding="utf-8")
    cfg = tmp_path / "mupen64plus.cfg"
    cfg.write_text("[Core]\nCurrentStateSlot = 1\n", encoding="utf-8")
    (saves / f"{rom}.st1").write_bytes(b"state")

    monkeypatch.setattr(rs, "Engine", lambda: _FakeEngine(running=False))
    common = [
        "--roms",
        str(roms),
        "--saves",
        str(saves),
        "--selected",
        str(selected),
        "--mupen-cfg",
        str(cfg),
    ]
    assert rs.main(["decide", *common]) == 0
    assert capsys.readouterr().out.strip() == "idle"

    assert rs.main(["compact", *common]) == 0
    assert (saves / f"{rom}.st0").read_bytes() == b"state"

    (saves / f"{rom}.st3").write_bytes(b"state2")
    assert rs.main(["finish", *common]) == 0
    assert (saves / f"{rom}.st1").read_bytes() == b"state2"
    assert "CurrentStateSlot = 8" in cfg.read_text(encoding="utf-8")


def test_stage_pending_stages_overflow_or_recent_slot_without_touching_saved_slots(
    tmp_path: Path,
) -> None:
    saves = tmp_path / "saves"
    rom = "Bomberman 64 (U) [!].z64"
    assert rs.stage_pending(saves, rom) is False
    saves.mkdir()
    assert rs.stage_pending(saves, "") is False
    assert rs.stage_pending(saves, rom) is False

    # Old user slot (.st0 > 10s ago) is not treated as a newly captured Reset state.
    st0 = saves / f"{rom}.st0"
    st0.write_bytes(b"keep-slot-0")
    os.utime(st0, ns=(1_000_000_000, 1_000_000_000))
    assert rs.stage_pending(saves, rom, now_ns=30_000_000_000) is False
    assert st0.read_bytes() == b"keep-slot-0"

    # Overflow slots .st8 and .st9 (+ .st9.png): newest becomes .pending, .st8 + fallback cleaned.
    fallback = tmp_path / "reset-shot.png"
    fallback.write_bytes(b"unused-fallback")
    st8 = saves / f"{rom}.st8"
    st9 = saves / f"{rom}.st9"
    st9_png = saves / f"{rom}.st9.png"
    st8.write_bytes(b"older-overflow")
    st9.write_bytes(b"newest-overflow")
    st9_png.write_bytes(b"newest-shot")
    os.utime(st8, ns=(2_000_000_000, 2_000_000_000))
    os.utime(st9, ns=(3_000_000_000, 3_000_000_000))
    assert rs.stage_pending(saves, rom, fallback_shot=fallback) is True
    assert (saves / f"{rom}.pending").read_bytes() == b"newest-overflow"
    assert (saves / f"{rom}.pending.png").read_bytes() == b"newest-shot"
    assert not fallback.exists()
    assert not st8.exists()
    assert not st9.exists()
    assert st0.read_bytes() == b"keep-slot-0"

    # Standalone mupen64plus writes no .st9.png -> fallback_shot becomes .pending.png.
    fallback.write_bytes(b"batocera-screenshot-bytes")
    st9.write_bytes(b"mupen-state")
    assert rs.stage_pending(saves, rom, fallback_shot=fallback) is True
    assert (saves / f"{rom}.pending").read_bytes() == b"mupen-state"
    assert (saves / f"{rom}.pending.png").read_bytes() == b"batocera-screenshot-bytes"
    assert not fallback.exists()

    # Emulator-written .png without a fallback_shot argument also stages cleanly.
    st9.write_bytes(b"overflow-no-fallback")
    st9_png.write_bytes(b"shot-no-fallback")
    assert rs.stage_pending(saves, rom) is True
    assert (saves / f"{rom}.pending.png").read_bytes() == b"shot-no-fallback"

    # Shotless overflow state without fallback clears any stale .pending.png.
    st9.write_bytes(b"no-shot-state")
    assert rs.stage_pending(saves, rom) is True
    assert (saves / f"{rom}.pending").read_bytes() == b"no-shot-state"
    assert not (saves / f"{rom}.pending.png").exists()

    # Fallback when mupen64plus wrote into .st0..3 within the last 10s: newest staged, older kept.
    (saves / f"{rom}.pending").unlink()
    st1 = saves / f"{rom}.st1"
    st1.write_bytes(b"recent-slot-1")
    os.utime(st0, ns=(25_000_000_000, 25_000_000_000))
    os.utime(st1, ns=(28_000_000_000, 28_000_000_000))
    assert rs.stage_pending(saves, rom, now_ns=30_000_000_000) is True
    assert (saves / f"{rom}.pending").read_bytes() == b"recent-slot-1"
    assert st0.read_bytes() == b"keep-slot-0"


def test_wait_and_stage_pending_polls_for_flushed_state(tmp_path: Path) -> None:
    saves = tmp_path / "saves"
    saves.mkdir()
    rom = "Bomberman 64 (U) [!].z64"
    fallback = tmp_path / "reset-shot.png"
    st9 = saves / f"{rom}.st9"

    def _noop_sleep(_dt: float) -> None:
        return None

    st9.write_bytes(b"")
    assert (
        rs.wait_and_stage_pending(saves, rom, fallback, attempts=2, sleep_fn=_noop_sleep) is False
    )

    def _flush_on_sleep(_dt: float) -> None:
        st9.write_bytes(b"flushed-bytes")

    assert (
        rs.wait_and_stage_pending(saves, rom, fallback, attempts=3, sleep_fn=_flush_on_sleep)
        is True
    )
    assert (saves / f"{rom}.pending").read_bytes() == b"flushed-bytes"


def test_notify_suspend_drawer_and_cli_stage_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fifo = tmp_path / "topbar.cmd"
    assert rs.notify_suspend_drawer(fifo) is False
    os.mkfifo(fifo)
    # No reader on FIFO -> O_WRONLY | O_NONBLOCK raises ENXIO -> returns False cleanly.
    assert rs.notify_suspend_drawer(fifo) is False

    rfd = os.open(str(fifo), os.O_RDWR | os.O_NONBLOCK)
    try:
        assert rs.notify_suspend_drawer(fifo) is True
        assert os.read(rfd, 64) == b"suspend\n"

        def _bad_write(_fd: int, _data: bytes) -> int:
            raise OSError("broken pipe")

        monkeypatch.setattr(os, "write", _bad_write)
        assert rs.notify_suspend_drawer(fifo) is False
    finally:
        os.close(rfd)

    monkeypatch.undo()
    roms = tmp_path / "roms"
    saves = tmp_path / "saves"
    roms.mkdir()
    saves.mkdir()
    rom = "Bomberman 64 (U) [!].z64"
    selected = tmp_path / "selected"
    selected.write_text(f"/userdata/roms/n64/{rom}", encoding="utf-8")
    cfg = tmp_path / "mupen64plus.cfg"
    cfg.write_text("[Core]\nCurrentStateSlot = 1\n", encoding="utf-8")
    monkeypatch.setattr(rs, "Engine", lambda: _FakeEngine(running=False))

    (saves / f"{rom}.st9").write_bytes(b"staged-via-cli")
    common = [
        "--roms",
        str(roms),
        "--saves",
        str(saves),
        "--selected",
        str(selected),
        "--mupen-cfg",
        str(cfg),
        "--fifo",
        str(fifo),
    ]
    assert rs.main(["stage", *common]) == 0
    assert (saves / f"{rom}.pending").read_bytes() == b"staged-via-cli"

    rfd2 = os.open(str(fifo), os.O_RDWR | os.O_NONBLOCK)
    try:
        (saves / f"{rom}.st9").write_bytes(b"opened-via-cli")
        assert rs.main(["open", *common]) == 0
        assert (saves / f"{rom}.pending").read_bytes() == b"opened-via-cli"
        assert os.read(rfd2, 64) == b"suspend\n"
    finally:
        os.close(rfd2)
