"""AC-T14: the Showcase <-> Full switch. Invariants first, then the fail-loud cases."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import TYPE_CHECKING, ClassVar, override

import pytest
from hypothesis import given
from hypothesis import strategies as st

from n64mini.console import engine as en
from n64mini.console import library
from n64mini.console import showcase as sc
from n64mini.console.strip import MUSIC_FULL, STRIPS, SYSTEMS_CFG

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_HIDDEN_ANY = re.compile(r"\s*<hidden>.*?</hidden>", re.DOTALL)
_BLOCK = re.compile(r"<game\b[^>]*>.*?</game>", re.DOTALL)
_PATH = re.compile(r"<path>\./(.*?)</path>")

_names = st.text(alphabet=st.sampled_from("abcXYZ 09-&'()[]!"), min_size=1, max_size=12).map(
    lambda s: s.strip() or "x"
)


@st.composite
def _setup(draw: st.DrawFn) -> tuple[str, list[str], set[str]]:
    """A gamelist, the ROM files beside it, and the Showcase subset to keep."""
    roms = sorted({f"{n}.z64" for n in draw(st.lists(_names, min_size=1, max_size=8))})
    listed = draw(st.lists(st.sampled_from(roms), unique=True))
    indent = draw(st.sampled_from(["  ", "\t", "    "]))
    blocks: list[str] = []
    for rom in listed:
        hidden = draw(st.sampled_from(["", "<hidden>true</hidden>", "<hidden>false</hidden>"]))
        children = [f"<path>./{sc.xml_escape(rom)}</path>", "<name>n</name>", hidden]
        body = "".join(f"\n{indent}{indent}{c}" for c in children if c)
        blocks.append(f"{indent}<game>{body}\n{indent}</game>\n")
    body = "".join(blocks)
    root = "<gameList/>" if not body and draw(st.booleans()) else f"<gameList>\n{body}</gameList>"
    text = f'<?xml version="1.0"?>\n{root}\n'
    keep = set(draw(st.lists(st.sampled_from(roms), unique=True)))
    return text, roms, keep


def _entries(text: str) -> dict[str, str]:
    """Each entry's block by ROM name (unescaped)."""
    out: dict[str, str] = {}
    for block in _BLOCK.findall(text):
        m = _PATH.search(block)
        assert m is not None
        out[m.group(1).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")] = block
    return out


def _is_hidden(block: str) -> bool:
    return "<hidden>true</hidden>" in block


# ---------------------------------------------------------------- invariants
@given(_setup())
def test_showcase_then_full_gives_back_the_same_bytes(
    case: tuple[str, list[str], set[str]],
) -> None:
    text, roms, keep = case
    shown, state = sc.to_showcase(text, roms, keep)
    assert sc.to_full(shown, state) == text


@given(_setup())
def test_showcase_hides_exactly_the_games_outside_the_lineup(
    case: tuple[str, list[str], set[str]],
) -> None:
    text, roms, keep = case
    before = _entries(text)
    after = _entries(sc.to_showcase(text, roms, keep)[0])
    assert set(after) >= set(before)  # no entry is ever removed
    for rom in roms:
        if rom not in after:  # no entry, so the engine shows it: only right for a kept game
            assert rom in keep, rom
            continue
        owner_hid = rom in before and _is_hidden(before[rom])
        assert _is_hidden(after[rom]) == (rom not in keep or owner_hid), rom


@given(_setup())
def test_showcase_changes_nothing_but_hidden_flags(case: tuple[str, list[str], set[str]]) -> None:
    text, roms, keep = case
    before = _entries(text)
    after = _entries(sc.to_showcase(text, roms, keep)[0])
    for rom, block in before.items():
        assert _HIDDEN_ANY.sub("", after[rom]) == _HIDDEN_ANY.sub("", block)


def test_full_still_works_after_the_engine_rewrites_the_file() -> None:
    text = (
        "<gameList>\n  <game>\n    <path>./a.z64</path>\n  </game>\n"
        "  <game>\n    <path>./b.z64</path>\n    <hidden>true</hidden>\n  </game>\n</gameList>\n"
    )
    shown, state = sc.to_showcase(text, ["a.z64", "b.z64", "c.z64"], {"b.z64"})
    rewritten = shown.replace("\n    ", "\n\t\t").replace("\n  ", "\n\t")  # engine reformat
    full = _entries(sc.to_full(rewritten, state))
    assert set(full) == {"a.z64", "b.z64"}  # the entry Showcase added for c is gone
    assert not _is_hidden(full["a.z64"])
    assert _is_hidden(full["b.z64"])  # hidden by the owner, and still is


def test_an_added_entry_scraped_in_showcase_is_unhidden_not_dropped() -> None:
    text = "<gameList>\n  <game>\n    <path>./a.z64</path>\n  </game>\n</gameList>\n"
    shown, state = sc.to_showcase(text, ["a.z64", "b.z64"], {"a.z64"})
    scraped = shown.replace(
        "<path>./b.z64</path>",
        "<path>./b.z64</path>\n    <name>AeroGauge</name>\n    <thumbnail>./m/b.png</thumbnail>",
    )
    full = _entries(sc.to_full(scraped, state))
    assert set(full) == {"a.z64", "b.z64"}
    assert not _is_hidden(full["b.z64"])
    assert "<name>AeroGauge</name>" in full["b.z64"]
    assert "<thumbnail>./m/b.png</thumbnail>" in full["b.z64"]


def test_a_hidden_false_entry_gets_its_own_element_back() -> None:
    text = "<gameList><game><path>./a.z64</path><hidden>false</hidden></game></gameList>"
    shown, state = sc.to_showcase(text, ["a.z64"], set())
    assert "<hidden>true</hidden>" in shown
    assert state["hidden"] == {"a.z64": "<hidden>false</hidden>"}
    assert sc.to_full(shown, state) == text


# ---------------------------------------------------------------- choosing ROMs
_LINEUP = [sc.Game("Mario Kart 64", re.compile(r"^Mario Kart 64", re.IGNORECASE))]


@pytest.mark.parametrize(
    ("roms", "chosen"),
    [
        (["Mario Kart 64 (E) [!].z64", "Mario Kart 64 (U) [!].z64"], "Mario Kart 64 (U) [!].z64"),
        (["Mario Kart 64 (U) [!].v64", "Mario Kart 64 (U) [!].z64"], "Mario Kart 64 (U) [!].z64"),
        (["Mario Kart 64 (U).z64", "Mario Kart 64 (U) [!].z64"], "Mario Kart 64 (U) [!].z64"),
        (["Mario Kart 64 (J).v64"], "Mario Kart 64 (J).v64"),
    ],
)
def test_resolve_prefers_us_then_z64_then_verified(roms: list[str], chosen: str) -> None:
    assert sc.resolve(_LINEUP, [*roms, "Other.z64"]) == {"Mario Kart 64": chosen}


def test_resolve_fails_on_a_missing_game_or_a_tie() -> None:
    with pytest.raises(sc.ShowcaseError, match="Mario Kart 64: no ROM matches"):
        sc.resolve(_LINEUP, ["Other.z64"])
    with pytest.raises(sc.ShowcaseError, match="can't choose between"):
        sc.resolve(_LINEUP, ["Mario Kart 64 (U) [!].z64", "Mario Kart 64 (U) [!] (b1).z64"])


def test_the_shipped_lineup_loads() -> None:
    games = sc.load_lineup()
    assert len(games) == 30
    assert len({g.title for g in games}) == 30


def test_the_catalogue_adds_ported_games_outside_the_showcase() -> None:
    """AC-T15: every game with a playable port can be flipped, in the Showcase or not."""
    catalog = sc.load_catalog()
    assert len({g.title for g in catalog}) == len(catalog)
    extra = [g for g in catalog if not g.showcase]
    assert extra
    assert all(g.ports for g in extra)  # a non-Showcase game is only listed for its port
    assert {g.title for g in catalog if g.showcase} == {g.title for g in sc.load_lineup()}


@pytest.mark.parametrize(
    ("entry", "match"),
    [
        ('{"title": "A", "rom": "^A"}', "showcase"),
        ('{"title": "A", "rom": "^A", "showcase": "yes"}', "true or false"),
    ],
)
def test_a_catalogue_entry_must_say_whether_it_is_in_the_showcase(
    tmp_path: Path, entry: str, match: str
) -> None:
    catalog = tmp_path / "games.json"
    catalog.write_text(f'{{"games": [{entry}]}}')
    with pytest.raises(sc.ShowcaseError, match=match):
        sc.load_catalog(catalog)


# ---------------------------------------------------------------- the CLI, on files
def _console(tmp_path: Path, gamelist: str | None) -> tuple[Path, Path]:
    roms = tmp_path / "roms"
    roms.mkdir()
    for name in ("Keep (U) [!].z64", "Drop (U) [!].z64", "notes.txt"):
        (roms / name).write_text("")
    lineup = tmp_path / "lineup.json"
    lineup.write_text(json.dumps({"games": [{"title": "Keep", "rom": "^Keep", "showcase": True}]}))
    if gamelist is not None:
        (roms / "gamelist.xml").write_text(gamelist)
    return roms, lineup


def test_toggle_round_trip_through_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    original = "<gameList>\n  <game>\n    <path>./Keep (U) [!].z64</path>\n  </game>\n</gameList>\n"
    roms, lineup = _console(tmp_path, original)
    args = ["--roms", str(roms), "--lineup", str(lineup)]
    assert sc.main(["status", *args]) == 0
    assert sc.main(["toggle", *args]) == 0
    assert sc.main(["status", *args]) == 0
    showcase = (roms / "gamelist.xml").read_text()
    assert _is_hidden(_entries(showcase)["Drop (U) [!].z64"])
    assert not _is_hidden(_entries(showcase)["Keep (U) [!].z64"])
    assert sc.main(["toggle", *args]) == 0
    assert (roms / "gamelist.xml").read_text() == original
    assert not (roms / "gamelist.xml.showcase.json").exists()
    assert capsys.readouterr().out.split() == ["full", "showcase", "showcase", "full"]


def test_asking_for_the_current_mode_changes_nothing(tmp_path: Path) -> None:
    roms, lineup = _console(tmp_path, "<gameList/>")
    assert sc.switch("full", roms / "gamelist.xml", roms, lineup) == "full"
    assert (roms / "gamelist.xml").read_text() == "<gameList/>"


def test_a_missing_gamelist_is_created_by_showcase(tmp_path: Path) -> None:
    roms, lineup = _console(tmp_path, None)
    assert sc.switch("showcase", roms / "gamelist.xml", roms, lineup) == "showcase"
    assert _is_hidden(_entries((roms / "gamelist.xml").read_text())["Drop (U) [!].z64"])


@pytest.mark.parametrize(
    ("gamelist", "lineup_json", "match"),
    [
        ("<gameList><game>", None, "not valid XML"),
        ("<games/>", None, "root is <games>"),
        ("<gameList><game><name>x</name></game></gameList>", None, "without a <path>"),
        (
            "<gameList/>",
            '{"games": [{"title": "Gone", "rom": "^Gone", "showcase": true}]}',
            "none of the Showcase's games is in",
        ),
        ("<gameList/>", '{"games": []}', "lists no games"),
        ("<gameList/>", '{"games": [{"title": "Bad", "rom": "(", "showcase": true}]}', "lineup"),
    ],
)
def test_a_bad_input_fails_loud_and_changes_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    gamelist: str,
    lineup_json: str | None,
    match: str,
) -> None:
    roms, lineup = _console(tmp_path, gamelist)
    if lineup_json is not None:
        lineup.write_text(lineup_json)
    assert sc.main(["showcase", "--roms", str(roms), "--lineup", str(lineup)]) == 1
    assert match in capsys.readouterr().err
    assert (roms / "gamelist.xml").read_text() == gamelist
    assert not (roms / "gamelist.xml.showcase.json").exists()


def test_full_fails_loud_if_hidden_games_vanished(tmp_path: Path) -> None:
    roms, lineup = _console(tmp_path, "<gameList/>")
    gamelist = roms / "gamelist.xml"
    sc.switch("showcase", gamelist, roms, lineup)
    gamelist.write_text("<gameList/>")  # someone deleted the entries Showcase made
    with pytest.raises(sc.ShowcaseError, match="no longer in the gamelist"):
        sc.switch("full", gamelist, roms, lineup)


def test_an_empty_or_missing_rom_folder_fails(tmp_path: Path) -> None:
    with pytest.raises(sc.ShowcaseError, match="ROM folder not found"):
        sc.list_roms(tmp_path / "nope")
    with pytest.raises(sc.ShowcaseError, match="no ROMs in"):
        sc.list_roms(tmp_path)


def test_full_removes_hidden_the_engine_moved_onto_one_line() -> None:
    text = "<gameList>\n  <game>\n    <path>./a.z64</path>\n  </game>\n</gameList>\n"
    shown, state = sc.to_showcase(text, ["a.z64"], set())
    one_line = re.sub(r"\n\s*", "", shown)  # the engine wrote everything on one line
    assert "<hidden>" not in sc.to_full(one_line, state)


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def read(self) -> bytes:
        return b""


class _FakeEngine:
    """Stands in for the engine's HTTP server; records what was asked of it."""

    status: ClassVar[int] = 200
    requests: ClassVar[list[tuple[str, str]]] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        assert (host, port, timeout) == ("127.0.0.1", 1234, 10)

    def request(self, method: str, url: str, **_kwargs: object) -> None:
        _FakeEngine.requests.append((method, url))

    def getresponse(self) -> _FakeResponse:
        return _FakeResponse(_FakeEngine.status)

    def close(self) -> None:
        pass


@pytest.mark.parametrize(("status", "code"), [(200, 0), (500, 1)])
def test_reload_asks_the_running_engine_and_reports_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, code: int
) -> None:
    roms, lineup = _console(tmp_path, "<gameList/>")
    monkeypatch.setattr("http.client.HTTPConnection", _FakeEngine)
    monkeypatch.setattr(_FakeEngine, "status", status)
    monkeypatch.setattr(_FakeEngine, "requests", [])
    args = ["showcase", "--roms", str(roms), "--lineup", str(lineup), "--reload"]
    assert sc.main(args) == code
    assert _FakeEngine.requests == [("GET", "/reloadgames")]


def test_an_empty_gamelist_element_is_restored_exactly() -> None:
    """<gameList/> has no </gameList> to insert before; Full must give the empty element back.

    A fixed-input test: the property tests only reach this when Hypothesis happens to draw an
    empty list, which made coverage (and so the ratchet) vary between runs.
    """
    text = '<?xml version="1.0"?>\n<gameList/>\n'
    shown, state = sc.to_showcase(text, ["a.z64"], set())
    assert state.get("empty_root") == "<gameList/>"
    assert _is_hidden(_entries(shown)["a.z64"])
    assert sc.to_full(shown, state) == text


# ---------------------------------------------------------------- the Showcase/Full card (AC-T14)
def test_the_card_is_never_hidden_and_always_offers_the_other_list(tmp_path: Path) -> None:
    roms, lineup = _console(tmp_path, "<gameList/>")
    (roms / sc.CARD).write_text("")
    gamelist = roms / "gamelist.xml"
    assert sc.switch("showcase", gamelist, roms, lineup) == "showcase"
    card = _entries(gamelist.read_text())[sc.CARD]
    assert not _is_hidden(card)
    assert "<name>Full library</name>" in card
    assert f"<image>./{sc.MEDIA_DIR}/library-full.png</image>" in card
    assert "<sortname>~</sortname>" in card  # after every game: just left of the first one
    assert sc.switch("full", gamelist, roms, lineup) == "full"
    card = _entries(gamelist.read_text())[sc.CARD]
    assert "<name>The Showcase</name>" in card
    assert f"<image>./{sc.MEDIA_DIR}/library-showcase.png</image>" in card


def test_with_the_card_in_place_a_round_trip_still_gives_back_the_same_bytes(
    tmp_path: Path,
) -> None:
    full = sc.place_card(
        "<gameList>\n  <game>\n    <path>./Keep (U) [!].z64</path>\n  </game>\n</gameList>\n",
        "full",
    )
    roms, lineup = _console(tmp_path, full)
    (roms / sc.CARD).write_text("")
    gamelist = roms / "gamelist.xml"
    sc.switch("showcase", gamelist, roms, lineup)
    assert gamelist.read_text() != full
    sc.switch("full", gamelist, roms, lineup)
    assert gamelist.read_text() == full


def test_placing_the_card_replaces_its_entry_in_place() -> None:
    other = "  <game>\n    <path>./Keep (U) [!].z64</path>\n  </game>\n"
    text = sc.place_card(f"<gameList>\n{other}</gameList>\n", "full")
    assert text.startswith(f"<gameList>\n{other}  <game>")  # added last
    again = sc.place_card(text, "showcase")
    assert again.count("<game>") == 2
    assert again.startswith(f"<gameList>\n{other}  <game>")  # replaced where it was
    assert sc.place_card(again, "full") == text
    assert sc.place_card("<gameList/>", "full").startswith("<gameList>\n  <game>")


def test_without_the_card_file_no_entry_is_written(tmp_path: Path) -> None:
    roms, lineup = _console(tmp_path, "<gameList/>")
    sc.switch("showcase", roms / "gamelist.xml", roms, lineup)
    assert sc.CARD not in (roms / "gamelist.xml").read_text()
    sc.switch("full", roms / "gamelist.xml", roms, lineup)
    assert sc.CARD not in (roms / "gamelist.xml").read_text()


# ---------------------------------------------------------------- the card's launch (AC-T14)
class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_the_switch_waits_for_the_launch_to_end() -> None:
    clock = _Clock()
    answers = iter([True, True, False])  # the launch shows as a game twice, then ends
    sc.wait_for_launch_to_end(lambda: next(answers), clock, clock.sleep)
    assert clock.now == 1.0  # two half-second waits, then on at once


def test_a_launch_that_never_ends_fails() -> None:
    clock = _Clock()
    with pytest.raises(sc.ShowcaseError, match=f"after {sc.LAUNCH_WAIT} s"):
        sc.wait_for_launch_to_end(lambda: True, clock, clock.sleep)


def _no_detach(_log: Path) -> None:
    """Stands in for detach_output, which would redirect pytest's own output."""


class _CardEngine(en.Engine):
    """Records the calls the toggle makes to the engine."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, ...]] = []

    @override
    def set_metadata(self, path: str, fields: Mapping[str, str]) -> None:
        self.calls.append(("metadata", path, fields["name"], fields["desc"][:12]))

    @override
    def set_media(self, path: str, field: str, png: bytes) -> None:
        self.calls.append(("media", path, field, png.decode()))

    @override
    def reload_games(self) -> None:
        self.calls.append(("reload",))


def test_the_card_gets_its_new_face_in_the_engine_before_the_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found on the VM: the engine wrote its stale copy of the just-launched card over the file,
    so the card's face is set in its memory, which is what it writes."""
    roms, lineup = _console(tmp_path, "<gameList/>")
    (roms / sc.CARD).write_text("")
    (roms / sc.MEDIA_DIR).mkdir(parents=True)
    for name in sc.CARD_IMAGES.values():
        (roms / sc.MEDIA_DIR / name).write_text(name)
    engine = _CardEngine()
    monkeypatch.setattr(sc, "Engine", lambda: engine)
    assert sc.main(["toggle", "--roms", str(roms), "--lineup", str(lineup), "--reload"]) == 0
    card = str(roms / sc.CARD)
    assert engine.calls == [
        ("metadata", card, "Full library", "Every N64 ga"),
        ("media", card, "image", "library-full.png"),
        ("reload",),
    ]


def test_after_a_card_launch_a_failure_goes_on_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sc, "detach_output", _no_detach)  # tested on its own
    roms, lineup = _console(tmp_path, "<gameList/>")
    (roms / "Keep (U) [!].z64").unlink()  # the lineup game is gone: Showcase can't be built
    monkeypatch.setattr("http.client.HTTPConnection", _FakeEngine)
    monkeypatch.setattr(_FakeEngine, "status", 201)  # no game running
    monkeypatch.setattr(_FakeEngine, "requests", [])
    args = ["toggle", "--roms", str(roms), "--lineup", str(lineup), "--after-game"]
    assert sc.main(args) == 1
    assert _FakeEngine.requests == [("GET", "/runningGame"), ("POST", "/notify")]
    assert "none of the Showcase's games is in" in capsys.readouterr().err


def test_after_a_card_launch_with_no_engine_the_switch_still_happens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sc, "detach_output", _no_detach)  # tested on its own
    roms, lineup = _console(tmp_path, "<gameList/>")

    def refused(*_args: object, **_kwargs: object) -> None:
        raise ConnectionRefusedError("no engine")

    monkeypatch.setattr("http.client.HTTPConnection", refused)
    args = ["toggle", "--roms", str(roms), "--lineup", str(lineup), "--after-game"]
    assert sc.main(args) == 0
    assert capsys.readouterr().out == "showcase\n"
    assert sc.current_mode(roms / "gamelist.xml") == "showcase"


def test_a_failure_to_show_on_screen_is_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sc, "detach_output", _no_detach)  # tested on its own
    roms, lineup = _console(tmp_path, "<gameList/>")
    (roms / "Keep (U) [!].z64").unlink()

    def refused(*_args: object, **_kwargs: object) -> None:
        raise ConnectionRefusedError("no engine")

    monkeypatch.setattr("http.client.HTTPConnection", refused)
    args = ["toggle", "--roms", str(roms), "--lineup", str(lineup), "--after-game"]
    assert sc.main(args) == 1
    assert "showcase: couldn't show it on screen: no engine" in capsys.readouterr().err


def test_an_unreachable_engine_is_a_failed_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    roms, lineup = _console(tmp_path, "<gameList/>")

    def refused(*_args: object, **_kwargs: object) -> None:
        raise ConnectionRefusedError("no engine")

    monkeypatch.setattr("http.client.HTTPConnection", refused)
    assert sc.main(["showcase", "--roms", str(roms), "--lineup", str(lineup), "--reload"]) == 1
    assert "the engine didn't take the change" in capsys.readouterr().err


def test_detaching_lets_go_of_the_launch_pipe(tmp_path: Path) -> None:
    """Found on the VM: the toggle kept the launch's output pipe open, so the engine never
    ended the launch while the toggle waited for it to."""
    log = tmp_path / "logs" / "showcase.log"
    code = (
        "import sys; from pathlib import Path; from n64mini.console import showcase as sc; "
        "print('before'); sc.detach_output(Path(sys.argv[1])); print('after'); "
        "print('oops', file=sys.stderr)"
    )
    done = subprocess.run(
        [sys.executable, "-c", code, str(log)],
        stdin=subprocess.PIPE,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert done.stdout == "before\n"  # the pipe saw nothing after detaching: it was let go
    assert sorted(log.read_text().splitlines()) == ["after", "oops"]  # order: stdout is buffered


def test_detaching_points_the_three_streams_away(tmp_path: Path) -> None:
    """In-process (the subprocess test above is the real one): stdin reads nothing, output
    goes to the log. The process's own descriptors are put back afterwards."""
    log = tmp_path / "showcase.log"
    saved = [os.dup(fd) for fd in (0, 1, 2)]
    try:
        sc.detach_output(log)
        os.write(1, b"out\n")
        os.write(2, b"err\n")
        assert os.read(0, 10) == b""  # /dev/null
    finally:
        for fd, copy in enumerate(saved):
            os.dup2(copy, fd)
            os.close(copy)
    assert log.read_text() == "out\nerr\n"


def test_a_showcase_game_with_no_rom_is_left_out(tmp_path: Path) -> None:
    """DEC-7: a missing lineup game doesn't stop the switch; the others still make the Showcase."""
    roms, lineup = _console(tmp_path, "<gameList/>")
    games = [
        {"title": "Keep", "rom": "^Keep", "showcase": True},
        {"title": "Gone", "rom": "^Gone", "showcase": True},
    ]
    lineup.write_text(json.dumps({"games": games}))
    assert sc.switch("showcase", roms / "gamelist.xml", roms, lineup) == "showcase"
    entries = _entries((roms / "gamelist.xml").read_text())
    assert _is_hidden(entries["Drop (U) [!].z64"])
    assert "Keep (U) [!].z64" not in entries  # kept: no entry needed


def test_a_tie_between_dumps_still_stops_the_switch(tmp_path: Path) -> None:
    roms, lineup = _console(tmp_path, "<gameList/>")
    (roms / "Keep (U) [!] (alt).z64").write_text("")  # same rank as "Keep (U) [!].z64"
    with pytest.raises(sc.ShowcaseError, match="can't choose between"):
        sc.switch("showcase", roms / "gamelist.xml", roms, lineup)


def test_the_cards_toggle_redraws_the_strip_before_the_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-T20: the strip follows the new list; drawn after the switch, before the reload."""
    roms, lineup = _console(tmp_path, "<gameList/>")
    engine = _CardEngine()
    monkeypatch.setattr(sc, "Engine", lambda: engine)
    seen: list[str] = []

    def after(folder: Path) -> str | None:
        seen.append(sc.current_mode(folder / "gamelist.xml"))
        assert engine.calls == []  # not reloaded yet
        return None

    args = ["toggle", "--roms", str(roms), "--lineup", str(lineup), "--reload"]
    assert sc.main(args, after_switch=after) == 0
    assert seen == ["showcase"]  # it saw the switched list
    assert engine.calls == [("reload",)]


def test_a_strip_that_fails_after_the_switch_still_reloads_and_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    roms, lineup = _console(tmp_path, "<gameList/>")
    engine = _CardEngine()
    monkeypatch.setattr(sc, "Engine", lambda: engine)
    args = ["toggle", "--roms", str(roms), "--lineup", str(lineup), "--reload"]
    assert sc.main(args, after_switch=lambda _: "no strip") == 1
    assert engine.calls == [("reload",)]  # the switch itself is shown
    assert "showcase: no strip" in capsys.readouterr().err


def test_library_redraws_the_strip_under_the_userdata_folder(tmp_path: Path) -> None:
    """The card's toggle (launch.py runs library) draws <userdata>/system/n64mini/strip."""
    userdata = tmp_path / "userdata"
    roms = userdata / "roms" / "n64"
    roms.mkdir(parents=True)
    (roms / "a.z64").write_bytes(b"")
    assert library.redraw_strip(roms) is not None  # no system config: reported, not raised
    cfg = userdata / SYSTEMS_CFG
    cfg.parent.mkdir(parents=True)
    cfg.write_text("<extension>.z64</extension>")
    assert library.redraw_strip(roms) is None
    assert (userdata / STRIPS / "a.z64.png").is_file()
    # Full library mode (gamelist.xml exists without showcase.json) clears the strip PNGs
    (roms / "gamelist.xml").write_text("<gameList/>")
    assert library.redraw_strip(roms) is None
    assert not (userdata / STRIPS / "a.z64.png").exists()


def test_library_is_showcases_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    roms, lineup = _console(tmp_path, "<gameList/>")
    assert library.main(["status", "--roms", str(roms), "--lineup", str(lineup)]) == 0
    assert capsys.readouterr().out == "full\n"


def test_library_toggles_music_and_reloads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    userdata = tmp_path / "userdata"
    assert not library.toggle_music(userdata)
    wav = userdata / library.MUSIC_WAV
    wav.parent.mkdir(parents=True)
    wav.write_bytes(b"original-music")
    engine = _CardEngine()
    assert not library.toggle_music(userdata, reload=True, engine=engine)
    assert (userdata / MUSIC_FULL).read_bytes() == b"original-music"
    assert wav.read_bytes().startswith(b"RIFF")
    assert engine.calls == [("reload",)]
    monkeypatch.setattr(
        sys, "argv", ["library.py", "music", "--userdata", str(userdata), "--reload"]
    )
    assert library.main() == 0
    assert wav.read_bytes() == b"original-music"
    assert not (userdata / MUSIC_FULL).exists()
    assert library.main(["music", "--userdata", str(userdata)]) == 0
    assert (userdata / MUSIC_FULL).is_file()
