"""AC-T18: the Reset-button handler (n64mini.console.reset)."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import override

import pytest

from n64mini.console import reset as rs
from n64mini.console.engine import Engine, EngineError
from n64mini.console.flip import BADGE_DIR, MODES_FILE

PRESS = 30_000_000_000  # when Reset was pressed, in ns


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


def test_cli_decide_prints_idle_outside_a_game(
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


def test_stage_pending_stages_overflow_or_recent_slot_without_touching_saved_slots(
    tmp_path: Path,
) -> None:
    saves = tmp_path / "saves"
    rom = "Bomberman 64 (U) [!].z64"
    assert rs.stage_pending(saves, rom, since_ns=PRESS) is False
    saves.mkdir()
    assert rs.stage_pending(saves, "", since_ns=PRESS) is False
    assert rs.stage_pending(saves, rom, since_ns=PRESS) is False

    # Old user slot (.st0 written before the press) is not treated as a newly captured Reset state.
    st0 = saves / f"{rom}.st0"
    st0.write_bytes(b"keep-slot-0")
    os.utime(st0, ns=(1_000_000_000, 1_000_000_000))
    assert rs.stage_pending(saves, rom, since_ns=PRESS) is False
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
    os.utime(st8, ns=(32_000_000_000, 32_000_000_000))
    os.utime(st9, ns=(33_000_000_000, 33_000_000_000))
    assert rs.stage_pending(saves, rom, since_ns=PRESS, fallback_shot=fallback) is True
    assert (saves / f"{rom}.pending").read_bytes() == b"newest-overflow"
    assert (saves / f"{rom}.pending.png").read_bytes() == b"newest-shot"
    assert not fallback.exists()
    assert not st8.exists()
    assert not st9.exists()
    assert st0.read_bytes() == b"keep-slot-0"

    # Standalone mupen64plus writes no .st9.png -> fallback_shot becomes .pending.png.
    fallback.write_bytes(b"batocera-screenshot-bytes")
    st9.write_bytes(b"mupen-state")
    assert rs.stage_pending(saves, rom, since_ns=PRESS, fallback_shot=fallback) is True
    assert (saves / f"{rom}.pending").read_bytes() == b"mupen-state"
    assert (saves / f"{rom}.pending.png").read_bytes() == b"batocera-screenshot-bytes"
    assert not fallback.exists()

    # Emulator-written .png without a fallback_shot argument also stages cleanly.
    st9.write_bytes(b"overflow-no-fallback")
    st9_png.write_bytes(b"shot-no-fallback")
    assert rs.stage_pending(saves, rom, since_ns=PRESS) is True
    assert (saves / f"{rom}.pending.png").read_bytes() == b"shot-no-fallback"

    # Shotless overflow state without fallback clears any stale .pending.png.
    st9.write_bytes(b"no-shot-state")
    assert rs.stage_pending(saves, rom, since_ns=PRESS) is True
    assert (saves / f"{rom}.pending").read_bytes() == b"no-shot-state"
    assert not (saves / f"{rom}.pending.png").exists()

    # mupen64plus wrote into .st0..3 since the press: newest staged, older kept.
    (saves / f"{rom}.pending").unlink()
    st1 = saves / f"{rom}.st1"
    st1.write_bytes(b"recent-slot-1")
    os.utime(st0, ns=(30_500_000_000, 30_500_000_000))
    os.utime(st1, ns=(31_000_000_000, 31_000_000_000))
    assert rs.stage_pending(saves, rom, since_ns=PRESS) is True
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
        rs.wait_and_stage_pending(saves, rom, fallback, since_ns=0, sleep_fn=_noop_sleep) is False
    )

    def _flush_on_sleep(_dt: float) -> None:
        st9.write_bytes(b"flushed-bytes")

    assert (
        rs.wait_and_stage_pending(saves, rom, fallback, since_ns=0, sleep_fn=_flush_on_sleep)
        is True
    )
    assert (saves / f"{rom}.pending").read_bytes() == b"flushed-bytes"


def test_a_stale_resume_copy_is_not_staged_as_the_new_suspend_point(tmp_path: Path) -> None:
    # B3: after a Start-resume, .st9 holds the resumed copy. Reset must wait for mupen64plus to
    # flush the new state, not stage the stale copy (and lose the new screenshot).
    rom = "Bomberman 64 (U) [!].z64"
    st9 = tmp_path / f"{rom}.st9"
    st9.write_bytes(b"resumed-copy")
    os.utime(st9, ns=(10_000_000_000, 10_000_000_000))
    press = 60_000_000_000

    def _mupen_flushes(_dt: float) -> None:
        st9.write_bytes(b"new-suspend-point")
        (tmp_path / f"{rom}.st9.png").write_bytes(b"new-shot")

    staged = rs.wait_and_stage_pending(
        tmp_path, rom, tmp_path / "none.png", since_ns=press, sleep_fn=_mupen_flushes
    )
    assert staged is True
    assert (tmp_path / f"{rom}.pending").read_bytes() == b"new-suspend-point"
    assert (tmp_path / f"{rom}.pending.png").read_bytes() == b"new-shot"


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
    with pytest.raises(SystemExit) as refused:  # a stage without the press time is refused
        rs.main(["stage", *common])
    assert refused.value.code == 2
    common = [*common, "--since-ns", "0"]
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


def test_the_reset_command_returns_before_it_sends_keys_through_hotkeygen(tmp_path: Path) -> None:
    # hotkeygen runs the command with os.system and reads no keys until it returns, including the
    # save_state and exit keys the command sends through it. Found on the VM: nothing was saved,
    # the game never quit, and `open` waited forever. The fake hotkeygen blocks the same way until
    # released, so a command that doesn't detach times out here.
    calls = tmp_path / "calls.log"
    release = tmp_path / "release"
    shim = tmp_path / "bin"
    shim.mkdir()
    scripts = {
        "python3": (
            'case "$*" in *decide*) echo emulator ;; *time_ns*) echo 1 ;;'
            ' *) echo "py $*" >> "{log}" ;; esac'
        ),
        # Gives up after 20 s on its own: a test that fails before the release must not leave it
        # looping (one did, for 19 h).
        "hotkeygen": (
            'i=0; while [ ! -f "{release}" ] && [ $i -lt 400 ]; do sleep 0.05; i=$((i+1)); done;'
            ' echo "key $*" >> "{log}"'
        ),
        "batocera-screenshot": "exit 0",
    }
    for name, body in scripts.items():
        tool = shim / name
        tool.write_text("#!/bin/sh\n" + body.format(log=calls, release=release) + "\n")
        tool.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{shim}:{os.environ['PATH']}",
        "N64MINI_RESET_LOG": str(tmp_path / "reset.log"),
    }
    script = Path(rs.__file__).parent / "data" / "n64mini-reset.sh"

    expected = [
        ["key", "--send", "save_state"],
        ["py", "-m", "n64mini.console.reset"],
        ["key", "--send", "exit"],
        ["py", "-m", "n64mini.console.reset"],
    ]
    try:
        subprocess.run(["bash", str(script)], env=env, check=True, timeout=5)
    finally:
        release.touch()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _lines(calls) < len(expected):
        time.sleep(0.05)
    assert [line.split()[:3] for line in calls.read_text().splitlines()] == expected


def _lines(path: Path) -> int:
    return len(path.read_text().splitlines()) if path.exists() else 0
