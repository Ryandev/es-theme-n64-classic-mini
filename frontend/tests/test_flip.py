"""AC-T15: flipping a ported game's card between Emulator and Port."""

from __future__ import annotations

import hashlib
import json
import time
from typing import TYPE_CHECKING, ClassVar, override

import pytest

from n64mini.console import engine as en
from n64mini.console import flip as fl
from n64mini.console.showcase import CARD, CARD_IMAGES, list_roms

if TYPE_CHECKING:
    from pathlib import Path

PORTED = "Mario Kart 64 (U) [!].z64"
SECOND_DUMP = "Mario Kart 64 (U) [!].v64"
PLAIN = "Tetrisphere (U) [!].z64"
CATALOG: dict[str, list[dict[str, object]]] = {
    "games": [
        {
            "title": "MK64",
            "rom": "^Mario Kart 64",
            "showcase": True,
            "ports": [{"name": "Spaghetti"}],
        },
        {"title": "Tetris", "rom": "^Tetrisphere", "showcase": True, "ports": []},
    ]
}


class FakeEngine(en.Engine):
    """Records badge uploads instead of talking HTTP; can be told to fail."""

    running = False  # whether a game is running, for every instance (the CLI makes its own)

    def __init__(self, *, fail: bool = False) -> None:
        super().__init__()
        self.fail = fail
        self.uploads: list[tuple[str, bytes]] = []

    @override
    def game_running(self) -> bool:
        return FakeEngine.running

    @override
    def set_badge(self, path: str, png: bytes) -> None:
        if self.fail:
            raise fl.FlipError("engine unreachable")
        self.uploads.append((path, png))


@pytest.fixture
def console(tmp_path: Path) -> tuple[Path, Path]:
    """ROMs (a ported game in two dumps, a game without a port), a catalogue, an installed port.

    The installed ports file is ``catalog.with_name("ports.json")``; see ``_ports``.
    """
    roms = tmp_path / "roms"
    roms.mkdir()
    for name in (PORTED, SECOND_DUMP, PLAIN):
        (roms / name).write_bytes(b"")
    catalog = tmp_path / "games.json"
    catalog.write_text(json.dumps(CATALOG))
    _ports(catalog).write_text(json.dumps({"MK64": ["/ports/kart", "{rom}"]}))
    (roms / fl.BADGE_DIR).mkdir(parents=True)
    for mode, name in fl.BADGES.items():
        (roms / fl.BADGE_DIR / name).write_bytes(mode.encode())  # stands in for the PNG
    return roms, catalog


def _ports(catalog: Path) -> Path:
    return catalog.with_name("ports.json")


def test_only_the_chosen_dump_of_a_ported_game_counts(console: tuple[Path, Path]) -> None:
    roms, catalog = console
    assert fl.ported_roms(list_roms(roms), catalog) == {PORTED}


def test_mark_badges_ported_games_only_by_their_mode() -> None:
    text = (
        "<gameList>\n  <game>\n    <path>./Tetrisphere (U) [!].z64</path>\n  </game>\n"
        "  <game>\n    <path>./x.z64</path>\n"
        "    <cartridge>/engine/media/cartridges/x.png</cartridge>\n  </game>\n</gameList>\n"
    )
    marked = fl.mark(text, {PORTED, "x.z64"}, {"x.z64": "port"})
    assert "Tetrisphere (U) [!].z64</path>\n  </game>" in marked  # no badge without a port
    assert f"<path>./x.z64</path>\n    <cartridge>{fl.badge_path('port')}" in marked  # its mode
    assert f"<path>./{PORTED}</path>\n    <cartridge>{fl.badge_path('emulator')}" in marked
    assert fl.mark(marked, {PORTED, "x.z64"}, {"x.z64": "port"}) == marked  # idempotent


def test_mark_adds_a_badge_to_an_existing_entry_and_opens_an_empty_root() -> None:
    entry = (
        f"<gameList>\n  <game>\n    <path>./{PORTED}</path>\n    <name>MK</name>\n  </game>\n"
        "</gameList>"
    )
    assert f"<name>MK</name>\n    <cartridge>{fl.badge_path('emulator')}</cartridge>" in fl.mark(
        entry, {PORTED}
    )
    assert f"<path>./{PORTED}</path>" in fl.mark("<gameList/>", {PORTED})


def test_init_copies_the_badges_and_marks_the_gamelist(
    console: tuple[Path, Path], tmp_path: Path
) -> None:
    roms, catalog = console
    art = tmp_path / "art"
    art.mkdir()
    for name in fl.BADGES.values():
        (art / name).write_bytes(b"png")
    assert fl.init(roms, art, _ports(catalog), catalog) == 1
    assert (roms / fl.BADGE_DIR / "flip-port.png").read_bytes() == b"png"
    assert fl.badge_path("emulator") in (roms / "gamelist.xml").read_text()
    (art / "flip-port.png").unlink()
    with pytest.raises(fl.FlipError, match="badge not found"):
        fl.init(roms, art, _ports(catalog), catalog)


def test_flip_toggles_a_ported_game_and_ignores_others(console: tuple[Path, Path]) -> None:
    roms, catalog = console
    engine = FakeEngine()
    path = str(roms / PORTED)
    assert (
        fl.flip(str(roms / PLAIN), engine, roms, _ports(catalog), catalog) is None
    )  # nothing to flip
    assert (
        fl.flip(str(roms / SECOND_DUMP), engine, roms, _ports(catalog), catalog) is None
    )  # not the chosen dump
    assert engine.uploads == []
    assert fl.flip(path, engine, roms, _ports(catalog), catalog) == "port"
    assert fl.load_modes(roms) == {PORTED: "port"}
    assert fl.flip(path, engine, roms, _ports(catalog), catalog) == "emulator"
    assert fl.load_modes(roms) == {}
    assert engine.uploads == [(path, b"port"), (path, b"emulator")]  # the right badge each time


def test_a_failed_badge_upload_leaves_the_mode_unchanged(console: tuple[Path, Path]) -> None:
    roms, catalog = console
    with pytest.raises(fl.FlipError, match="unreachable"):
        fl.flip(str(roms / PORTED), FakeEngine(fail=True), roms, _ports(catalog), catalog)
    assert fl.load_modes(roms) == {}


@pytest.mark.parametrize("content", ['["port"]', '{"a.z64": "emulator"}'])
def test_a_malformed_modes_file_fails_loud(console: tuple[Path, Path], content: str) -> None:
    roms, _ = console
    (roms / fl.BADGE_DIR / fl.MODES_FILE).write_text(content)
    with pytest.raises(fl.FlipError, match="must map ROM file names"):
        fl.load_modes(roms)


def test_the_game_id_is_the_engines_md5_of_the_path() -> None:
    """HttpApi::getFileDataId: MD5 of the game's path, as hex."""
    path = "/userdata/roms/n64/Mario Kart 64 (U) [!].z64"
    assert en.game_id(path) == hashlib.md5(path.encode(), usedforsecurity=False).hexdigest()


def test_only_n64_selections_are_recorded(tmp_path: Path) -> None:
    note = tmp_path / "selected"
    fl.record_selected("snes", "/roms/snes/x.sfc", note)
    assert not note.exists()
    fl.record_selected("n64", "/roms/n64/y.z64", note)
    assert note.read_text() == "/roms/n64/y.z64"


# ---------------------------------------------------------------- the engine's HTTP API
class _Response:
    def __init__(self, status: int) -> None:
        self.status = status

    def read(self) -> bytes:
        return b"404 game not found"


class _Connection:
    """Stands in for http.client.HTTPConnection; records the one request."""

    status = 200
    sent: ClassVar[list[tuple[str, str, bytes | None, dict[str, str]]]] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        assert (host, port, timeout) == ("127.0.0.1", 1234, 10)

    def request(self, method: str, url: str, body: bytes | None, headers: dict[str, str]) -> None:
        _Connection.sent.append((method, url, body, headers))

    def getresponse(self) -> _Response:
        return _Response(_Connection.status)

    def close(self) -> None:
        pass


@pytest.mark.parametrize("status", [200, 404])
def test_set_badge_uploads_the_image_to_the_media_endpoint(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """Engine fact 20: a path field is set by uploading the image, not by JSON."""
    monkeypatch.setattr("http.client.HTTPConnection", _Connection)
    monkeypatch.setattr(_Connection, "status", status)
    monkeypatch.setattr(_Connection, "sent", [])
    path = "/userdata/roms/n64/x.z64"
    if status == 200:
        en.Engine().set_badge(path, b"PNG")
    else:
        with pytest.raises(en.EngineError, match="404 game not found"):
            en.Engine().set_badge(path, b"PNG")
    assert _Connection.sent == [
        (
            "POST",
            f"/systems/n64/games/{en.game_id(path)}/media/cartridge",
            b"PNG",
            {"Content-Type": "image/png"},
        )
    ]


# ---------------------------------------------------------------- the CLI
def test_cli_init_selected_and_flip(
    console: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    roms, catalog = console
    art = tmp_path / "art"
    art.mkdir()
    for name in fl.BADGES.values():
        (art / name).write_bytes(b"png")
    note = tmp_path / "selected"
    monkeypatch.setattr(fl, "SELECTED_FILE", note)
    monkeypatch.setattr(fl, "Engine", FakeEngine)
    common = ["--roms", str(roms), "--catalog", str(catalog), "--ports", str(_ports(catalog))]
    assert fl.main([*common, "flip"]) == 0  # nothing highlighted yet: nothing happens
    assert fl.main([*common, "init", "--badges", str(art)]) == 0
    assert fl.main([*common, "selected", "n64", str(roms / PORTED), "MK64"]) == 0
    assert fl.main([*common, "flip"]) == 0
    assert fl.main([*common, "selected", "n64", str(roms / PLAIN)]) == 0
    assert fl.main([*common, "flip"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "1 games can be flipped",
        "port",
        "no installed port",
    ]
    (art / "flip-port.png").unlink()
    assert fl.main([*common, "init", "--badges", str(art)]) == 1
    assert "badge not found" in capsys.readouterr().err


def test_the_flip_hotkey_does_nothing_while_a_game_runs(
    console: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC-T15: the hotkey fires in every context, a running game's too, where it must not flip."""
    roms, catalog = console
    note = tmp_path / "selected"
    note.write_text(str(roms / PORTED))
    monkeypatch.setattr(fl, "SELECTED_FILE", note)
    monkeypatch.setattr(fl, "Engine", FakeEngine)
    monkeypatch.setattr(FakeEngine, "running", True)
    common = ["--roms", str(roms), "--catalog", str(catalog), "--ports", str(_ports(catalog))]
    assert fl.main([*common, "flip"]) == 0
    assert capsys.readouterr().out == ""
    assert fl.load_modes(roms) == {}


@pytest.mark.parametrize(("status", "running"), [(200, True), (201, False)])
def test_game_running_reads_the_engines_answer(
    monkeypatch: pytest.MonkeyPatch, status: int, *, running: bool
) -> None:
    """``GET /runningGame``: 200 with the game, 201 "NO GAME RUNNING" (HttpServerThread.cpp)."""
    monkeypatch.setattr("http.client.HTTPConnection", _Connection)
    monkeypatch.setattr(_Connection, "status", status)
    monkeypatch.setattr(_Connection, "sent", [])
    assert en.Engine().game_running() is running
    assert _Connection.sent == [("GET", "/runningGame", None, {})]


def test_notify_and_reload_use_the_engines_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("http.client.HTTPConnection", _Connection)
    monkeypatch.setattr(_Connection, "status", 200)
    monkeypatch.setattr(_Connection, "sent", [])
    en.Engine().notify("Setting up port:sk2…")
    en.Engine().reload_games()
    en.Engine().set_metadata("/r/x.z64", {"name": "N"})
    assert _Connection.sent == [
        ("POST", "/notify", "Setting up port:sk2…".encode(), {"Content-Type": "text/plain"}),
        ("GET", "/reloadgames", None, {}),
        (
            "POST",
            f"/systems/n64/games/{en.game_id('/r/x.z64')}",
            b'{"name": "N"}',
            {"Content-Type": "application/json"},
        ),
    ]


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.parametrize("comes_up", [True, False])
def test_waiting_for_the_engine_gives_up_at_the_deadline(
    monkeypatch: pytest.MonkeyPatch, *, comes_up: bool
) -> None:
    clock = _Clock()
    calls: list[float] = []

    def answer(_self: en.Engine) -> bool:
        calls.append(clock.now)
        if comes_up and len(calls) == 3:
            return False
        raise ConnectionRefusedError

    def sleep(seconds: float) -> None:
        clock.now += seconds

    monkeypatch.setattr(en.Engine, "game_running", answer)
    monkeypatch.setattr(time, "sleep", sleep)
    assert en.Engine().wait_until_up(5, clock) is comes_up
    assert calls == ([0.0, 1.0, 2.0] if comes_up else [0.0, 1.0, 2.0, 3.0, 4.0, 5.0])


def test_a_ported_game_without_an_installed_port_cannot_be_flipped(
    console: tuple[Path, Path],
) -> None:
    """Owner's ruling (AC-T15, option b): nothing to flip, so no badge and no flip."""
    roms, catalog = console
    _ports(catalog).write_text("{}")
    engine = FakeEngine()
    assert fl.flippable_roms(list_roms(roms), _ports(catalog), catalog) == set()
    assert fl.flip(str(roms / PORTED), engine, roms, _ports(catalog), catalog) is None
    assert engine.uploads == []


def test_removing_a_port_clears_its_badge_and_resets_the_game(
    console: tuple[Path, Path], tmp_path: Path
) -> None:
    roms, catalog = console
    art = tmp_path / "art"
    art.mkdir()
    for name in fl.BADGES.values():
        (art / name).write_bytes(b"png")
    fl.init(roms, art, _ports(catalog), catalog)
    fl.flip(str(roms / PORTED), FakeEngine(), roms, _ports(catalog), catalog)
    assert fl.load_modes(roms) == {PORTED: "port"}
    _ports(catalog).write_text("{}")  # the port is uninstalled
    assert fl.init(roms, art, _ports(catalog), catalog) == 0
    assert fl.load_modes(roms) == {}
    assert "<cartridge>" not in (roms / "gamelist.xml").read_text()


def test_init_places_the_library_card_when_it_is_installed(
    console: tuple[Path, Path], tmp_path: Path
) -> None:
    """AC-T14: the Showcase/Full card gets its art and entry with the badges, if it's there."""
    roms, catalog = console
    art = tmp_path / "art"
    art.mkdir()
    for name in [*fl.BADGES.values(), *CARD_IMAGES.values()]:
        (art / name).write_bytes(name.encode())
    fl.init(roms, art, _ports(catalog), catalog)
    assert CARD not in (roms / "gamelist.xml").read_text()  # no card file: no entry, no art
    assert not (roms / fl.BADGE_DIR / CARD_IMAGES["full"]).exists()
    (roms / CARD).write_text("")
    fl.init(roms, art, _ports(catalog), catalog)
    text = (roms / "gamelist.xml").read_text()
    assert "<name>The Showcase</name>" in text  # the list is Full, so the card offers Showcase
    for name in CARD_IMAGES.values():
        assert (roms / fl.BADGE_DIR / name).read_bytes() == name.encode()
    (art / CARD_IMAGES["showcase"]).unlink()
    with pytest.raises(fl.FlipError, match=r"library-showcase\.png"):
        fl.init(roms, art, _ports(catalog), catalog)
