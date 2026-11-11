"""``n64mini-showcase``: switch the console's N64 list between Showcase and Full (AC-T14).

Showcase hides every N64 game that isn't in the lineup (``data/games.json``); Full undoes
exactly what Showcase did. What Showcase changed is recorded in a state file beside the
gamelist (``gamelist.xml.showcase.json``). Its presence *is* Showcase mode, and it's what makes
Full exact: each game gets back the very <hidden> element it had, so a game the owner hid
stays hidden.

The gamelist is edited as text, so Showcase then Full gives back the same bytes. When the engine
has rewritten the file in between (it saves play counts on exit), Full works per game instead.

Runs on the console, so it uses only the standard library.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, NotRequired, TypedDict, cast

from n64mini.console.engine import Engine, EngineError

if TYPE_CHECKING:
    from collections.abc import Callable
from xml.parsers import expat

CATALOG: Final = Path(__file__).with_name("data") / "games.json"
LINEUP: Final = CATALOG  # the Showcase is the catalogue's "showcase": true games
ROM_SUFFIXES: Final = frozenset({".z64", ".n64", ".v64", ".zip", ".7z"})
# Among several files for one game: US first, then the native byte order, then a verified dump.
_SUFFIX_RANK: Final = {".z64": 0, ".v64": 1, ".n64": 2, ".zip": 3, ".7z": 4}
GAME_RE: Final = re.compile(r"<game\b[^>]*>.*?</game>", re.DOTALL)
_PATH_RE: Final = re.compile(r"<path>(.*?)</path>", re.DOTALL)
_HIDDEN_RE: Final = re.compile(r"<hidden>(.*?)</hidden>", re.DOTALL)
_STUB_RE: Final = re.compile(r"</?game\b[^>]*>|<path>.*?</path>|<hidden>.*?</hidden>", re.DOTALL)
_HIDDEN: Final = "<hidden>true</hidden>"
LAUNCH_WAIT: Final = 60  # seconds for the engine to end the card's launch
AFTER_GAME_LOG: Final = Path("/userdata/system/logs/n64mini-showcase.log")
MEDIA_DIR: Final = "media/n64mini"  # our images beside the gamelist, so paths in it are relative
# The Showcase/Full card (AC-T14, DEC-6): a marker file the N64 system lists (its extension is
# added in es_systems_n64.cfg) and the launcher turns into a toggle. Its gamelist entry says
# where it takes you, and sorts it last, just left of the first game.
CARD: Final = "Showcase or Full.n64mini"
CARD_IMAGES: Final = {"full": "library-full.png", "showcase": "library-showcase.png"}
_CARD_NAMES: Final = {"full": "Full library", "showcase": "The Showcase"}
_CARD_DESCS: Final = {
    "full": "Every N64 game on the console. Press A to show them all.",
    "showcase": "The classics, and every game with a port. Press A to show just those.",
}
# Text-node escaping as pugixml writes it; &amp; is undone last so "&amp;lt;" stays "&lt;".
_ESCAPES: Final = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))
_UNESCAPES: Final = (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&"))


type AfterSwitch = Callable[[Path], str | None]


class ShowcaseError(Exception):
    """The lineup, the ROMs or the gamelist don't allow a safe switch; nothing was changed."""


class _State(TypedDict):
    hidden: dict[str, str | None]  # path -> the <hidden> element it had before, or None
    added: list[str]  # paths that had no entry; Showcase added one
    empty_root: NotRequired[str]  # the original <gameList/>, if Showcase had to open it up


@dataclass(frozen=True, slots=True)
class Game:
    """A catalogue game: title, ROM file-name pattern, Showcase membership and its PC ports."""

    title: str
    rom: re.Pattern[str]
    showcase: bool = True
    ports: tuple[str, ...] = ()


def _game(entry: dict[str, object]) -> Game:
    showcase = entry["showcase"]
    if not isinstance(showcase, bool):
        raise TypeError(f"{entry['title']}: showcase must be true or false")
    ports = cast("list[dict[str, str]]", entry.get("ports", []))
    return Game(
        str(entry["title"]),
        re.compile(str(entry["rom"]), re.IGNORECASE),
        showcase,
        tuple(str(port["name"]) for port in ports),
    )


def load_catalog(path: Path = CATALOG) -> list[Game]:
    """Read and check every catalogue game. Raises ShowcaseError if it's malformed."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        games = [_game(g) for g in raw["games"]]
    except (OSError, ValueError, KeyError, TypeError, re.error) as exc:
        raise ShowcaseError(f"lineup {path}: {exc}") from exc
    return games


def load_lineup(path: Path = LINEUP) -> list[Game]:
    """The Showcase games of the catalogue. Raises ShowcaseError if there are none."""
    games = [g for g in load_catalog(path) if g.showcase]
    if not games:
        raise ShowcaseError(f"lineup {path} lists no games")
    return games


def _rank(name: str) -> tuple[int, int, int]:
    return (
        0 if "(U)" in name else 1,
        _SUFFIX_RANK.get(Path(name).suffix.lower(), 9),
        0 if "[!]" in name else 1,
    )


def resolve(lineup: list[Game], roms: list[str]) -> dict[str, str]:
    """Map each Showcase title to one ROM file name. Raises if a game has none, or a tie."""
    chosen: dict[str, str] = {}
    problems: list[str] = []
    for game in lineup:
        matches = sorted((r for r in roms if game.rom.search(r)), key=lambda r: (_rank(r), r))
        if not matches:
            problems.append(f"{game.title}: no ROM matches /{game.rom.pattern}/")
        elif len(matches) > 1 and _rank(matches[0]) == _rank(matches[1]):
            problems.append(f"{game.title}: can't choose between {matches[0]!r} and {matches[1]!r}")
        else:
            chosen[game.title] = matches[0]
    if problems:
        raise ShowcaseError("the lineup doesn't fit these ROMs:\n  " + "\n  ".join(problems))
    return chosen


def list_roms(roms_dir: Path) -> list[str]:
    """The ROM files directly in ``roms_dir``. Raises if there are none."""
    if not roms_dir.is_dir():
        raise ShowcaseError(f"ROM folder not found: {roms_dir}")
    roms = sorted(p.name for p in roms_dir.iterdir() if p.suffix.lower() in ROM_SUFFIXES)
    if not roms:
        raise ShowcaseError(f"no ROMs in {roms_dir}")
    return roms


def xml_escape(text: str) -> str:
    """Escape a text node the way pugixml writes one (&, < and >)."""
    for raw, entity in _ESCAPES:
        text = text.replace(raw, entity)
    return text


def _unescape(text: str) -> str:
    for entity, raw in _UNESCAPES:
        text = text.replace(entity, raw)
    return text


def element_text(block: str, tag: str) -> str | None:
    """The unescaped text of the first ``<tag>`` in a ``<game>`` entry; None if it has none."""
    m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.DOTALL)
    return None if m is None else _unescape(m.group(1).strip())


def entry_path(block: str) -> str:
    """The ROM file name a ``<game>`` entry points at (unescaped, without ``./``)."""
    m = _PATH_RE.search(block)
    if not m:
        raise ShowcaseError(f"a <game> without a <path>: {block[:80]!r}")
    return _unescape(m.group(1).strip()).removeprefix("./")


def _check_gamelist(text: str) -> None:
    roots: list[str] = []
    parser = expat.ParserCreate()
    is_final = True  # Parse's flag is positional-only, so it can't be passed by keyword
    parser.StartElementHandler = lambda name, _attrs: roots.append(name) if not roots else None
    try:
        parser.Parse(text, is_final)
    except expat.ExpatError as exc:
        raise ShowcaseError(f"gamelist is not valid XML: {exc}") from exc
    if roots != ["gameList"]:
        raise ShowcaseError(f"gamelist root is <{roots[0] if roots else ''}>, expected <gameList>")


def set_element(block: str, tag: str, element: str) -> tuple[str, str | None]:
    """``block`` with its ``<tag>`` element replaced by ``element``, or added if it had none.

    Returns the new block and the element it had before (None if none), which is all that's
    needed to undo the change exactly. A new element goes on its own line, indented like the
    entry's other children.
    """
    if m := re.search(rf"<{tag}>(.*?)</{tag}>|<{tag}\s*/>", block, re.DOTALL):
        return block[: m.start()] + element + block[m.end() :], m.group(0)
    close = block.rindex("</game>")
    line_start = block.rfind("\n", 0, close)
    child = re.search(r"\n([ \t]*)<", block)
    indent = child.group(1) if child else "    "
    at = line_start if line_start != -1 else close
    return block[:at] + f"\n{indent}{element}" + block[at:], None


def _hide(block: str) -> tuple[str, str | None]:
    return set_element(block, "hidden", _HIDDEN)


def _unhide(block: str, original: str | None) -> str:
    if original is not None:
        return _HIDDEN_RE.sub(lambda _: original, block, count=1)
    exact = re.search(r"\n[ \t]*" + re.escape(_HIDDEN), block)
    if exact:  # the line Showcase inserted
        return block[: exact.start()] + block[exact.end() :]
    return _HIDDEN_RE.sub("", block, count=1)  # the engine reformatted the entry


def _appended(paths: list[str]) -> str:
    """The entries Showcase adds, just before </gameList>, for ROMs without one."""
    return "".join(
        f"  <game>\n    <path>./{xml_escape(r)}</path>\n    {_HIDDEN}\n  </game>\n" for r in paths
    )


def to_showcase(text: str, roms: list[str], keep: set[str]) -> tuple[str, _State]:
    """Hide every ROM not in ``keep``; returns the new gamelist and what was changed."""
    _check_gamelist(text)
    state: _State = {"hidden": {}, "added": []}
    listed: set[str] = set()

    def edit(m: re.Match[str]) -> str:
        block, path = m.group(0), entry_path(m.group(0))
        listed.add(path)
        if path in keep:
            return block
        new, original = _hide(block)
        state["hidden"][path] = original
        return new

    text = GAME_RE.sub(edit, text)
    missing = [r for r in roms if r not in keep and r not in listed]
    if missing:
        empty = re.search(r"<gameList\s*/>", text)  # an empty root has no </gameList>
        if empty and "</gameList>" not in text:
            state["empty_root"] = empty.group(0)
            text = text[: empty.start()] + "<gameList>\n</gameList>" + text[empty.end() :]
        end = text.rindex("</gameList>")
        text = text[:end] + _appended(missing) + text[end:]
        state["added"] = missing
    return text, state


def to_full(text: str, state: _State) -> str:
    """Undo exactly what to_showcase recorded in ``state``."""
    _check_gamelist(text)
    added = set(state["added"])
    block = _appended(state["added"]) + "</gameList>"
    if added and block in text:  # untouched since Showcase: remove the very bytes it added
        text = text.replace(block, "</gameList>", 1)
        added = set[str]()
    seen: set[str] = set()

    def edit(m: re.Match[str]) -> str:
        entry, path = m.group(0), entry_path(m.group(0))
        if path in added:  # the engine rewrote the file; drop the stub unless it gained metadata
            seen.add(path)
            return _unhide(entry, None) if _STUB_RE.sub("", entry).strip() else ""
        if path in state["hidden"]:
            seen.add(path)
            return _unhide(entry, state["hidden"][path])
        return entry

    text = GAME_RE.sub(edit, text)
    lost = (added | set(state["hidden"])) - seen
    if lost:
        raise ShowcaseError(f"{len(lost)} game(s) Showcase hid are no longer in the gamelist")
    if "empty_root" in state:
        text = text.replace("<gameList>\n</gameList>", state["empty_root"], 1)
    return text


def _state_path(gamelist: Path) -> Path:
    return gamelist.with_name(gamelist.name + ".showcase.json")


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)  # atomic on one filesystem: never a half-written gamelist


def card_entry(mode: str) -> str:
    """The card's gamelist entry while the list is in ``mode``: it offers the other one."""
    target = "full" if mode == "showcase" else "showcase"
    return (
        "<game>\n"
        f"    <path>./{xml_escape(CARD)}</path>\n"
        f"    <name>{_CARD_NAMES[target]}</name>\n"
        "    <sortname>~</sortname>\n"
        f"    <desc>{_CARD_DESCS[target]}</desc>\n"
        f"    <image>./{MEDIA_DIR}/{CARD_IMAGES[target]}</image>\n"
        "  </game>"
    )


def place_card(text: str, mode: str) -> str:
    """``text`` with the card's entry set for ``mode``: replaced where it is, else added last."""
    _check_gamelist(text)
    found = False

    def edit(m: re.Match[str]) -> str:
        nonlocal found
        if entry_path(m.group(0)) != CARD:
            return m.group(0)
        found = True
        return card_entry(mode)

    text = GAME_RE.sub(edit, text)
    if found:
        return text
    empty = re.search(r"<gameList\s*/>", text)
    if empty and "</gameList>" not in text:
        text = text[: empty.start()] + "<gameList>\n</gameList>" + text[empty.end() :]
    end = text.rindex("</gameList>")
    return text[:end] + "  " + card_entry(mode) + "\n" + text[end:]


def current_mode(gamelist: Path) -> str:
    """``showcase`` while Showcase's record of what it hid exists, else ``full``."""
    return "showcase" if _state_path(gamelist).exists() else "full"


def switch(mode: str, gamelist: Path, roms_dir: Path, lineup: Path = LINEUP) -> str:
    """Put the list in ``mode`` (showcase, full or toggle); returns the mode it ends in."""
    state_file = _state_path(gamelist)
    current = current_mode(gamelist)
    target = {"toggle": "full" if current == "showcase" else "showcase"}.get(mode, mode)
    if target == current:
        return current
    text = (
        gamelist.read_text(encoding="utf-8") if gamelist.exists() else "<gameList>\n</gameList>\n"
    )
    card = (roms_dir / CARD).exists()
    if card:  # in place before the switch, so the switch itself never moves it
        text = place_card(text, current)
    if target == "showcase":
        roms = list_roms(roms_dir)
        # A Showcase game the console has no ROM for is left out (DEC-7); none at all is an error.
        present = [g for g in load_lineup(lineup) if any(g.rom.search(r) for r in roms)]
        if not present:
            raise ShowcaseError(f"none of the Showcase's games is in {roms_dir}")
        keep = set(resolve(present, roms).values()) | {CARD}
        new_text, state = to_showcase(text, roms, keep)
        _write(state_file, json.dumps(state, indent=1))
        _write(gamelist, place_card(new_text, target) if card else new_text)
    else:
        state = cast("_State", json.loads(state_file.read_text(encoding="utf-8")))
        new_text = to_full(text, state)
        _write(gamelist, place_card(new_text, target) if card else new_text)
        state_file.unlink()
    return target


def wait_for_launch_to_end(
    running: Callable[[], bool],
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Wait until the engine has ended the card's launch (no game running)."""
    deadline = clock() + LAUNCH_WAIT
    while running():
        if clock() >= deadline:
            raise ShowcaseError(f"the engine still shows the launch after {LAUNCH_WAIT} s")
        sleep(0.5)


def show_card(engine: Engine, roms_dir: Path, mode: str) -> None:
    """Give the running engine the card's face for ``mode``, in its memory.

    The card was just launched, so the engine holds it as changed (playcount, lastplayed) and
    writes its own copy of the entry back whenever it next saves, over the gamelist file. Set
    there, the new name, text and image are what it writes.
    """
    target = "full" if mode == "showcase" else "showcase"
    path = str(roms_dir / CARD)
    engine.set_metadata(path, {"name": _CARD_NAMES[target], "desc": _CARD_DESCS[target]})
    engine.set_media(path, "image", (roms_dir / MEDIA_DIR / CARD_IMAGES[target]).read_bytes())


def detach_output(log: Path) -> None:
    """Point stdin at /dev/null and stdout/stderr at ``log``, closing the launch's pipes.

    The engine runs a launch with its output piped and ends the launch only when the pipe
    closes. The card's toggle runs on after its launch (it waits for the engine), so it must
    let go of the pipe first, or the engine and the toggle wait for each other.
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout.flush()
    sys.stderr.flush()
    with Path(os.devnull).open("rb") as null, log.open("ab") as out:
        os.dup2(null.fileno(), 0)
        os.dup2(out.fileno(), 1)
        os.dup2(out.fileno(), 2)


def _after_game() -> None:
    detach_output(AFTER_GAME_LOG)
    try:
        wait_for_launch_to_end(Engine().game_running)
    except (EngineError, OSError) as exc:  # no engine at all: nothing will save over the switch
        print(f"showcase: no engine to wait for ({exc})", file=sys.stderr)


def _fail(message: str, *, on_screen: bool) -> int:
    print(f"showcase: {message}", file=sys.stderr)
    if on_screen:  # started detached by the card: nobody reads stderr
        try:
            Engine().notify(f"Showcase/Full: {message}")
        except (EngineError, OSError) as exc:
            print(f"showcase: couldn't show it on screen: {exc}", file=sys.stderr)
    return 1


def _show(roms_dir: Path, mode: str) -> int:
    """Show the running engine the switch: the card's new face, then the reloaded lists."""
    engine = Engine()
    try:
        if (roms_dir / CARD).exists():
            show_card(engine, roms_dir, mode)
        engine.reload_games()
    except (EngineError, OSError) as exc:
        return _fail(f"the engine didn't take the change ({exc}); restart it", on_screen=False)
    return 0


def main(argv: list[str] | None = None, after_switch: AfterSwitch | None = None) -> int:
    """CLI entry point. ``after_switch`` runs once the list is switched, before the reload.

    It gets the ROM folder and returns a problem to report, or None.
    """
    parser = argparse.ArgumentParser(description="Switch the N64 list: Showcase or Full.")
    parser.add_argument("mode", choices=["showcase", "full", "toggle", "status"])
    parser.add_argument("--roms", type=Path, default=Path("/userdata/roms/n64"))
    parser.add_argument("--gamelist", type=Path, help="default: <roms>/gamelist.xml")
    parser.add_argument("--lineup", type=Path, default=LINEUP)
    parser.add_argument("--reload", action="store_true", help="ask the running engine to reload")
    parser.add_argument(
        "--after-game",
        action="store_true",
        help="started by the card's launch: wait for the engine to finish it first",
    )
    args = parser.parse_args(argv)
    roms_dir: Path = args.roms
    gamelist: Path = args.gamelist or roms_dir / "gamelist.xml"
    if args.mode == "status":
        print(current_mode(gamelist))
        return 0
    try:
        if args.after_game:
            _after_game()
        mode = switch(args.mode, gamelist, roms_dir, args.lineup)
    except ShowcaseError as exc:
        return _fail(str(exc), on_screen=args.after_game)
    print(mode)
    problem = after_switch(roms_dir) if after_switch else None
    shown = _show(roms_dir, mode) if args.reload else 0
    return _fail(problem, on_screen=args.after_game) if problem else shown


if __name__ == "__main__":
    raise SystemExit(main())
