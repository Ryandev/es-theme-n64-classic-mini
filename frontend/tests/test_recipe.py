"""AC-S04: a port's recipe (the owner's ROM, preparation commands, ports.json) and the manifest."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

from n64mini.console import recipe as rc
from n64mini.console import setup as st
from n64mini.console.flip import PORTS_FILE
from n64mini.console.showcase import load_catalog

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "bundle" / st.MANIFEST

Z64 = b"\x80\x37\x12\x40" + bytes(range(256)) * 4  # a tiny "ROM" in the console's byte order
SK2 = "Snowboard Kids 2"  # a catalogue game: /^Snowboard Kids 2 \(U\)/


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data, usedforsecurity=False).hexdigest()


def _v64(z64: bytes) -> bytes:  # bytes swapped in pairs, as Doctor V64 dumps are
    out = bytearray(z64)
    out[0::2], out[1::2] = z64[1::2], z64[0::2]
    return bytes(out)


def _n64(z64: bytes) -> bytes:  # 32-bit words reversed (little-endian dumps)
    out = bytearray(z64)
    out[0::4], out[1::4], out[2::4], out[3::4] = z64[3::4], z64[2::4], z64[1::4], z64[0::4]
    return bytes(out)


def _paths(tmp_path: Path) -> rc.Paths:
    userdata = tmp_path / "userdata"
    installed = userdata / "system/n64mini/ports/sk2"
    paths = rc.Paths(
        userdata=userdata,
        build=installed.with_name("sk2.new"),
        installed=installed,
        data=userdata / rc.DATA_DIR / "sk2",
        roms=userdata / "roms/n64",
    )
    paths.build.mkdir(parents=True)
    paths.roms.mkdir(parents=True)
    return paths


def _recipe(**fields: object) -> rc.Recipe:
    raw: dict[str, object] = {"game": SK2, "command": ["{dir}/game", "--data", "{data}"]}
    return rc.parse(raw | fields)


# ---------------------------------------------------------------- byte orders
def test_every_dump_byte_order_comes_back_as_the_same_z64() -> None:
    assert rc.to_z64(Z64) == Z64
    assert rc.to_z64(_v64(Z64)) == Z64
    assert rc.to_z64(_n64(Z64)) == Z64


def test_a_file_that_is_not_an_n64_rom_has_no_z64_form() -> None:
    assert rc.to_z64(b"PK\x03\x04 a zip file") is None


# ---------------------------------------------------------------- finding the owner's ROM
def test_the_dump_the_port_accepts_is_chosen_whatever_its_byte_order(tmp_path: Path) -> None:
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Snowboard Kids 2 (U) [!].z64").write_bytes(Z64[:-1] + b"\x00")  # another version
    (roms / "Snowboard Kids 2 (U) [b1].v64").write_bytes(_v64(Z64))
    (roms / "Snowboard Kids (U) [!].z64").write_bytes(Z64)  # a different game: never read
    assert rc.find_rom(roms, SK2, frozenset({_sha1(Z64)})) == Z64


def test_no_dump_of_the_game_means_the_port_waits(tmp_path: Path) -> None:
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Snowboard Kids 2 (U) [!].zip").write_bytes(Z64)  # zipped: not read
    msg = rf"no ROM of {SK2} in {roms} \(names matching /\^Snowboard Kids 2 \\\(U\\\)/\)"
    with pytest.raises(rc.RomMissingError, match=msg):
        rc.find_rom(roms, SK2, frozenset({_sha1(Z64)}))
    with pytest.raises(rc.RomMissingError, match="no ROM of"):
        rc.find_rom(tmp_path / "absent", SK2, frozenset({_sha1(Z64)}))


def test_only_the_wrong_version_means_the_port_waits_and_says_which(tmp_path: Path) -> None:
    roms = tmp_path / "roms"
    roms.mkdir()
    other = Z64[:-1] + b"\x00"
    (roms / "Snowboard Kids 2 (U) [!].z64").write_bytes(other)
    (roms / "Snowboard Kids 2 (U) [o1].n64").write_bytes(b"not a rom")
    with pytest.raises(rc.RomMissingError) as caught:
        rc.find_rom(roms, SK2, frozenset({_sha1(Z64)}))
    assert str(caught.value) == (
        f"no dump of {SK2} is the version the port needs: "
        f"Snowboard Kids 2 (U) [!].z64 (SHA-1 {_sha1(other)}), "
        "Snowboard Kids 2 (U) [o1].n64 (not an N64 ROM)"
    )


def test_a_game_missing_from_the_catalogue_is_a_manifest_error(tmp_path: Path) -> None:
    with pytest.raises(rc.RecipeError, match="'Nope 64' isn't in the catalogue"):
        rc.find_rom(tmp_path, "Nope 64", frozenset())
    bad = tmp_path / "games.json"
    bad.write_text("{}")
    with pytest.raises(rc.RecipeError, match="lineup"):
        rc.find_rom(tmp_path, SK2, frozenset(), catalog=bad)


# ---------------------------------------------------------------- parsing a recipe
def test_a_full_recipe_parses() -> None:
    recipe = _recipe(
        rom="a/rom.z64",
        rom_sha1=[_sha1(Z64).upper()],
        run=[{"argv": ["tool", "x"], "cwd": "a"}, {"argv": ["tool"]}],
        remove=["a"],
        links=["out.o2r"],
    )
    assert recipe == rc.Recipe(
        game=SK2,
        command=("{dir}/game", "--data", "{data}"),
        rom_sha1=frozenset({_sha1(Z64)}),
        rom="a/rom.z64",
        run=(rc.Run(("tool", "x"), "a"), rc.Run(("tool",), "")),
        remove=("a",),
        links=("out.o2r",),
    )


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"rom": "rom.z64"}, "rom and rom_sha1 go together"),
        ({"rom_sha1": [_sha1(Z64)]}, "rom and rom_sha1 go together"),
        ({"rom": "r", "rom_sha1": ["abc"]}, "rom_sha1 must be SHA-1 digests"),
        ({"command": []}, "command must be a non-empty list of words"),
        ({"command": ["ok", ""]}, "command must be a non-empty list of words"),
        ({"run": {"argv": ["x"]}}, "run must be a list of commands"),
        ({"run": ["x"]}, "run argv must be a non-empty list of words"),
        ({"run": [{"argv": ["x"], "cwd": "../up"}]}, "run cwd must be a relative path"),
        ({"remove": ["/etc"]}, "remove must be a relative path"),
        ({"links": ["a/../../b"]}, "links must be a relative path"),
    ],
)
def test_a_malformed_recipe_is_refused(fields: dict[str, object], message: str) -> None:
    with pytest.raises(rc.RecipeError, match=message):
        _recipe(**fields)


def test_placeholders_expand_and_an_unknown_one_is_refused(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    assert paths.expand("{userdata}/x:{dir}:{data}:{build}") == (
        f"{paths.userdata}/x:{paths.installed}:{paths.data}:{paths.build}"
    )
    with pytest.raises(rc.RecipeError, match=r"unknown placeholder \{home\} in '\{home\}/x'"):
        paths.expand("{home}/x")


# ---------------------------------------------------------------- preparing
def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_prepare_places_the_rom_runs_the_commands_and_tidies_up(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    (paths.roms / "Snowboard Kids 2 (U) [!].v64").write_bytes(_v64(Z64))
    copy = (
        "import pathlib,sys; "
        "pathlib.Path(sys.argv[2]).write_bytes(pathlib.Path(sys.argv[1]).read_bytes())"
    )
    recipe = _recipe(
        rom="src/base.z64",
        rom_sha1=[_sha1(Z64)],
        run=[{"argv": [*_py(copy), "base.z64", "{build}/made.o2r"], "cwd": "src"}],
        remove=["src"],
        links=["made.o2r"],
    )
    rc.prepare(recipe, paths)
    assert sorted(p.name for p in paths.build.iterdir()) == ["made.o2r"]
    assert (paths.build / "made.o2r").read_bytes() == Z64  # made from the z64 form


def test_remove_deletes_a_single_file_too(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    (paths.build / "torch.hash.yml").write_text("x")
    rc.prepare(_recipe(remove=["torch.hash.yml"]), paths)
    assert not (paths.build / "torch.hash.yml").exists()


def test_a_failing_command_reports_its_exit_code_and_last_lines(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    noisy = "import sys; [print('line %d' % i, file=sys.stderr) for i in range(9)]; sys.exit(3)"
    with pytest.raises(rc.RecipeError) as caught:
        rc.prepare(_recipe(run=[{"argv": _py(noisy)}]), paths)
    assert str(caught.value) == (
        f"{sys.executable} exited 3: line 4\nline 5\nline 6\nline 7\nline 8"
    )


def test_a_missing_tool_or_a_hung_one_is_a_recipe_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    with pytest.raises(rc.RecipeError, match=re.escape(str(tmp_path / "no-such-tool"))):
        rc.prepare(_recipe(run=[{"argv": [str(tmp_path / "no-such-tool")]}]), paths)
    monkeypatch.setattr(rc, "RUN_TIMEOUT", 0.2)
    with pytest.raises(rc.RecipeError, match=r"timed out after 0\.2 seconds"):
        rc.prepare(_recipe(run=[{"argv": _py("import time; time.sleep(5)")}]), paths)


def test_a_file_the_recipe_should_have_made_must_exist(tmp_path: Path) -> None:
    with pytest.raises(rc.RecipeError, match=r"should have made mk64\.o2r, but it isn't there"):
        rc.prepare(_recipe(links=["mk64.o2r"]), _paths(tmp_path))


# ---------------------------------------------------------------- finishing
def test_finish_links_the_data_folder_and_lists_the_port_beside_others(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    ports_file = paths.userdata / rc.PORTS
    ports_file.parent.mkdir(parents=True, exist_ok=True)
    ports_file.write_text(json.dumps({"Mario Kart 64": ["kart"]}))
    paths.data.mkdir(parents=True)
    (paths.data / "made.o2r").symlink_to(tmp_path / "an-old-version")  # replaced, not kept
    rc.finish(_recipe(links=["made.o2r"]), paths)
    assert (paths.data / "made.o2r").readlink() == paths.installed / "made.o2r"
    assert json.loads(ports_file.read_text()) == {
        "Mario Kart 64": ["kart"],
        SK2: [f"{paths.installed}/game", "--data", str(paths.data)],
    }


def test_a_malformed_ports_file_is_a_recipe_error(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    ports_file = paths.userdata / rc.PORTS
    ports_file.parent.mkdir(parents=True, exist_ok=True)
    ports_file.write_text('{"Mario Kart 64": "not a list"}')
    with pytest.raises(rc.RecipeError, match="must map game titles"):
        rc.finish(_recipe(), paths)
    ports_file.write_text("{oops")
    with pytest.raises(rc.RecipeError, match=r"ports\.json"):
        rc.finish(_recipe(), paths)


def test_setup_writes_the_ports_file_launch_and_flip_read() -> None:
    assert Path("/userdata") / rc.PORTS == PORTS_FILE


# ---------------------------------------------------------------- the real manifest
def test_the_committed_manifest_loads_and_its_ports_fit_the_catalogue() -> None:
    components = st.load_manifest(MANIFEST.parent)
    titles = {g.title: g for g in load_catalog()}
    ports = [c for c in components if c.kind == "port"]
    assert ports
    for comp in ports:
        assert comp.recipe is not None, comp.name
        assert titles[comp.recipe.game].ports, comp.name  # a game the catalogue says is ported
        assert comp.recipe.rom_sha1, comp.name  # every port so far needs the owner's ROM


# Programs a recipe may run from the system, each checked present on Batocera 43.1 (the VM).
BATOCERA_COMMANDS = {"tar"}


def test_the_manifest_runs_tools_where_their_component_installs_them() -> None:
    components = st.load_manifest(MANIFEST.parent)
    tools = {c.target for c in components if c.name.startswith("tool:")}
    for comp in components:
        for step in comp.recipe.run if comp.recipe else ():
            if not step.argv[0].startswith("{userdata}/"):
                assert step.argv[0] in BATOCERA_COMMANDS, (comp.name, step.argv[0])
                continue
            program = step.argv[0].removeprefix("{userdata}/")
            assert str(Path(program).parent) in tools, (comp.name, program)
