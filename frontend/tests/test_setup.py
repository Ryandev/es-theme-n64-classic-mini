"""AC-S02..S08: the boot setup, against a fake /userdata, fake downloads and no network."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import tarfile
import zipfile
from typing import TYPE_CHECKING, ClassVar

import pytest

from n64mini.console import setup as st
from n64mini.console.showcase import CARD, CARD_IMAGES
from n64mini.console.strip import STRIPS, SYSTEMS_CFG
from n64mini.paths import REPO_ROOT

if TYPE_CHECKING:
    from pathlib import Path


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _zip(files: dict[str, bytes], mode: int = 0o644) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            zf.writestr(info, data)
    return buf.getvalue()


class FakeNet:
    """Serves downloads from a dict by URL; counts requests; can be switched off."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.requests: list[str] = []
        self.online = True

    def __call__(self, url: str, dest: Path) -> None:
        self.requests.append(url)
        if not self.online:
            raise OSError("network is unreachable")
        dest.write_bytes(self.files[url])


PORT = _tar({"Starship/Starship": b"#!/bin/sh\n", "Starship/data.o2r": b"x"})
URL = "https://example.org/starship-v1.tar.gz"


def _bundle(tmp_path: Path, components: list[dict[str, object]]) -> Path:
    bundle = tmp_path / "usb"
    (bundle / "theme").mkdir(parents=True, exist_ok=True)
    (bundle / "theme" / "theme.xml").write_text("<theme/>")
    (bundle / st.MANIFEST).write_text(json.dumps({"components": components}))
    return bundle


def _theme(version: str = "1") -> dict[str, object]:
    return {
        "name": "theme",
        "kind": "files",
        "version": version,
        "source": "theme",
        "target": "themes/n64-classic-mini",
    }


def _port(
    version: str = "1.0", sha: str = _sha(PORT), archive: str = "tar.gz"
) -> dict[str, object]:
    return {
        "name": "port:starship",
        "kind": "port",
        "version": version,
        "url": URL,
        "sha256": sha,
        "archive": archive,
    }


@pytest.fixture
def userdata(tmp_path: Path) -> Path:
    path = tmp_path / "userdata"
    path.mkdir()
    return path


def _run(bundle: Path, userdata: Path, net: FakeNet) -> st.Report:
    return st.run(bundle, userdata, st.Options(fetch=net))


# ---------------------------------------------------------------- AC-S02: idempotent, versioned
def test_a_fresh_console_gets_everything_and_the_next_boot_does_nothing(
    tmp_path: Path, userdata: Path
) -> None:
    bundle = _bundle(tmp_path, [_theme(), _port()])
    net = FakeNet({URL: PORT})
    first = _run(bundle, userdata, net)
    assert first.applied == ["theme", "port:starship"]
    assert first.failed == {}
    assert (userdata / "themes/n64-classic-mini/theme.xml").read_text() == "<theme/>"
    assert (userdata / st.PORTS_DIR / "starship/Starship/Starship").is_file()
    second = _run(bundle, userdata, net)
    assert second.applied == []
    assert second.skipped == ["theme", "port:starship"]
    assert net.requests == [URL]  # nothing downloaded the second time


def test_a_new_version_redoes_only_that_component(tmp_path: Path, userdata: Path) -> None:
    net = FakeNet({URL: PORT})
    _run(_bundle(tmp_path, [_theme("1"), _port()]), userdata, net)
    report = _run(_bundle(tmp_path, [_theme("2"), _port()]), userdata, net)
    assert report.applied == ["theme"]
    assert st.load_state(userdata) == {"theme": "2", "port:starship": "1.0"}


def test_only_and_force_run_one_component(tmp_path: Path, userdata: Path) -> None:
    """AC-S08: one component on demand, even when it's already at its version."""
    bundle = _bundle(tmp_path, [_theme(), _port()])
    net = FakeNet({URL: PORT})
    _run(bundle, userdata, net)
    report = st.run(bundle, userdata, st.Options(only=("port:starship",), force=True, fetch=net))
    assert report.applied == ["port:starship"]
    assert net.requests == [URL]  # redone from the cache, not re-downloaded
    with pytest.raises(st.SetupError, match="not in the manifest: nope"):
        st.run(bundle, userdata, st.Options(only=("nope",), fetch=net))


# ---------------------------------------------------------------- AC-S03: never half-setup
def test_a_failed_component_keeps_the_old_version_and_others_carry_on(
    tmp_path: Path, userdata: Path
) -> None:
    net = FakeNet({URL: PORT})
    _run(_bundle(tmp_path, [_theme("1"), _port()]), userdata, net)
    bad = _bundle(tmp_path, [_theme("2"), _port("2.0", sha="0" * 64)])
    report = st.run(bad, userdata, st.Options(force=True, fetch=net))
    assert report.applied == ["theme"]
    assert "checksum mismatch" in report.failed["port:starship"]
    assert (userdata / st.PORTS_DIR / "starship/Starship/Starship").is_file()  # old one kept
    assert st.load_state(userdata)["port:starship"] == "1.0"
    assert not list((userdata / st.CACHE).glob("*.part"))


def test_a_missing_bundle_folder_fails_that_component(tmp_path: Path, userdata: Path) -> None:
    comp = _theme()
    comp["source"] = "gone"
    report = _run(_bundle(tmp_path, [comp]), userdata, FakeNet({}))
    assert "isn't in the bundle" in report.failed["theme"]
    assert not (userdata / "themes/n64-classic-mini").exists()


def test_a_leftover_from_an_interrupted_run_is_cleared(tmp_path: Path, userdata: Path) -> None:
    leftover = userdata / "themes/n64-classic-mini.new"
    leftover.mkdir(parents=True)
    (leftover / "junk").write_text("x")
    _run(_bundle(tmp_path, [_theme()]), userdata, FakeNet({}))
    assert not leftover.exists()
    assert not (userdata / "themes/n64-classic-mini/junk").exists()


# ---------------------------------------------------------------- AC-S04/S05: downloads
def test_without_a_network_the_port_waits_and_the_rest_completes(
    tmp_path: Path, userdata: Path
) -> None:
    net = FakeNet({URL: PORT})
    net.online = False
    notes: list[str] = []
    bundle = _bundle(tmp_path, [_theme(), _port()])
    report = st.run(bundle, userdata, st.Options(fetch=net, notify=notes.append))
    assert report.applied == ["theme"]
    assert "network is unreachable" in report.failed["port:starship"]
    assert notes[-1] == "port:starship failed; will retry next boot"
    net.online = True
    assert _run(bundle, userdata, net).applied == ["port:starship"]  # the next boot


def test_a_mirror_serves_downloads_by_checksum(tmp_path: Path, userdata: Path) -> None:
    mirror = "https://mirror.local/cache/"
    net = FakeNet({f"https://mirror.local/cache/{_sha(PORT)}": PORT})
    report = st.run(_bundle(tmp_path, [_port()]), userdata, st.Options(fetch=net, mirror=mirror))
    assert report.applied == ["port:starship"]


def test_a_zip_port_keeps_its_executable_bit(tmp_path: Path, userdata: Path) -> None:
    data = _zip({"Kart/SpaghettiKart": b"elf"}, mode=0o755)
    net = FakeNet({URL: data})
    _run(_bundle(tmp_path, [_port(sha=_sha(data), archive="zip")]), userdata, net)
    binary = userdata / st.PORTS_DIR / "starship/Kart/SpaghettiKart"
    assert binary.stat().st_mode & 0o111


@pytest.mark.parametrize(
    ("data", "archive"),
    [
        (_zip({"../escape": b"x"}), "zip"),
        (_tar({"../escape": b"x"}), "tar.gz"),
    ],
)
def test_an_archive_cannot_write_outside_its_folder(
    tmp_path: Path, userdata: Path, data: bytes, archive: str
) -> None:
    net = FakeNet({URL: data})
    report = _run(_bundle(tmp_path, [_port(sha=_sha(data), archive=archive)]), userdata, net)
    assert "port:starship" in report.failed
    assert not (userdata / st.PORTS_DIR / "escape").exists()
    assert not (userdata / st.PORTS_DIR / "starship").exists()


# ---------------------------------------------------------------- the manifest and state
@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"name": "Bad Name"}, "bad component name"),
        ({"kind": "magic"}, "unknown kind"),
        ({"target": "/etc"}, "relative path"),
        ({"source": "../../etc"}, "relative path"),
    ],
)
def test_a_malformed_files_component_is_refused(
    tmp_path: Path, change: dict[str, object], match: str
) -> None:
    with pytest.raises(st.SetupError, match=match):
        st.load_manifest(_bundle(tmp_path, [{**_theme(), **change}]))


def test_a_port_needs_a_checksum_and_a_known_archive(tmp_path: Path) -> None:
    for change in ({"sha256": "abc"}, {"archive": "rar"}):
        with pytest.raises(st.SetupError, match="needs a sha256"):
            st.load_manifest(_bundle(tmp_path, [{**_port(), **change}]))


def test_a_manifest_naming_a_component_twice_or_missing_is_refused(tmp_path: Path) -> None:
    with pytest.raises(st.SetupError, match="twice"):
        st.load_manifest(_bundle(tmp_path, [_theme(), _theme()]))
    with pytest.raises(st.SetupError, match="manifest"):
        st.load_manifest(tmp_path / "nothing-here")


def test_a_malformed_state_file_is_refused(userdata: Path) -> None:
    (userdata / st.STATE).parent.mkdir(parents=True)
    (userdata / st.STATE).write_text('{"theme": 1}')
    with pytest.raises(st.SetupError, match="must map component names"):
        st.load_state(userdata)


# ---------------------------------------------------------------- the CLI
def test_cli_reports_and_exits_nonzero_on_a_failure(
    tmp_path: Path, userdata: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = _bundle(tmp_path, [_theme()])
    assert st.main([str(bundle), "--userdata", str(userdata)]) == 0
    assert capsys.readouterr().out == "applied  theme\n"
    assert st.main([str(bundle), "--userdata", str(userdata)]) == 0
    assert capsys.readouterr().out == "skipped  theme\n"
    bad = _bundle(tmp_path / "b", [{**_theme("2"), "source": "gone"}])
    assert st.main([str(bad), "--userdata", str(userdata)]) == 1
    assert "FAILED   theme" in capsys.readouterr().err
    assert st.main([str(tmp_path / "none"), "--userdata", str(userdata)]) == 1


# ---------------------------------------------------------------- downloading over HTTPS
class _Response:
    def __init__(self, status: int, body: bytes = b"", location: str | None = None) -> None:
        self.status, self._body, self._location = status, io.BytesIO(body), location

    def getheader(self, name: str) -> str | None:
        return self._location if name == "Location" else None

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)


class _Https:
    """Stands in for http.client.HTTPSConnection: a scripted series of responses."""

    script: ClassVar[list[_Response]] = []
    seen: ClassVar[list[tuple[str, str]]] = []

    def __init__(self, host: str, port: int | None, timeout: float) -> None:
        assert timeout == 60
        assert port is None
        self.host = host

    def request(self, method: str, path: str, headers: dict[str, str]) -> None:
        assert method == "GET"
        assert headers == {"User-Agent": "n64mini-setup"}
        _Https.seen.append((self.host, path))

    def getresponse(self) -> _Response:
        return _Https.script.pop(0)

    def close(self) -> None:
        pass


def test_download_follows_github_style_redirects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("http.client.HTTPSConnection", _Https)
    monkeypatch.setattr(_Https, "seen", [])
    monkeypatch.setattr(
        _Https,
        "script",
        [
            _Response(302, location="https://objects.example.org/blob?sig=1"),
            _Response(200, b"port"),
        ],
    )
    st.download("https://github.com/o/r/releases/download/v1/port.tar.gz", tmp_path / "out")
    assert (tmp_path / "out").read_bytes() == b"port"
    assert _Https.seen == [
        ("github.com", "/o/r/releases/download/v1/port.tar.gz"),
        ("objects.example.org", "/blob?sig=1"),
    ]


@pytest.mark.parametrize(
    ("url", "script", "match"),
    [
        ("http://example.org/x", [], "only https"),
        ("https://example.org/x", [_Response(404)], "HTTP 404"),
        (
            "https://example.org/x",
            [_Response(302, location="https://example.org/x")] * 6,
            "redirects",
        ),
    ],
)
def test_download_refuses_plain_http_errors_and_loops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    script: list[_Response],
    match: str,
) -> None:
    monkeypatch.setattr("http.client.HTTPSConnection", _Https)
    monkeypatch.setattr(_Https, "script", list(script))
    with pytest.raises(st.SetupError, match=match):
        st.download(url, tmp_path / "out")


def test_a_zip_with_folders_and_plain_files_unpacks_as_is(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("Kart/assets/"), b"")
        plain = zipfile.ZipInfo("Kart/assets/readme.txt")
        plain.external_attr = 0o644 << 16
        zf.writestr(plain, b"hi")
    archive = tmp_path / "port.zip"
    archive.write_bytes(buf.getvalue())
    st.unpack(archive, "zip", tmp_path / "out")
    assert (tmp_path / "out/Kart/assets").is_dir()
    readme = tmp_path / "out/Kart/assets/readme.txt"
    assert readme.read_text() == "hi"
    assert not readme.stat().st_mode & 0o111  # not made executable


def test_a_stale_old_copy_from_an_interrupted_swap_is_cleared(
    tmp_path: Path, userdata: Path
) -> None:
    stale = userdata / "themes/n64-classic-mini.old"
    stale.mkdir(parents=True)
    _run(_bundle(tmp_path, [_theme()]), userdata, FakeNet({}))
    assert not stale.exists()
    assert (userdata / "themes/n64-classic-mini/theme.xml").is_file()


# ---------------------------------------------------------------- AC-S04: a port with a recipe
ROM = b"\x80\x37\x12\x40" + b"kart" * 64  # a tiny "ROM" in z64 byte order
KART = _tar({"spaghetti.appimage": b"#!/bin/sh\n"})
SOURCE = _tar({"SpaghettiKart-1.0.0/meta/mods.toml": b"[mod]\n"})
SOURCE_URL = "https://example.org/source.tar.gz"
# Stands in for Torch: reads the ROM in its folder and writes the .o2r where -d points.
TORCH = (
    "import pathlib,sys; d=pathlib.Path(sys.argv[1]);"
    " assert pathlib.Path('meta/mods.toml').exists();"
    " (d/'mk64.o2r').write_bytes(b'o2r:' + pathlib.Path('baserom.us.z64').read_bytes())"
)


def _kart(**fields: object) -> dict[str, object]:
    return {
        "name": "port:spaghettikart",
        "kind": "port",
        "version": "1.0.0",
        "url": URL,
        "sha256": _sha(KART),
        "archive": "tar.gz",
        "extras": [
            {
                "url": SOURCE_URL,
                "sha256": _sha(SOURCE),
                "archive": "tar.gz",
                "into": "source",
                "strip": True,
            }
        ],
        "game": "Mario Kart 64",
        "rom": "source/baserom.us.z64",
        "rom_sha1": [hashlib.sha1(ROM, usedforsecurity=False).hexdigest()],
        "run": [{"argv": [sys.executable, "-c", TORCH, "{build}"], "cwd": "source"}],
        "remove": ["source"],
        "links": ["mk64.o2r"],
        "command": ["env", "SHIP_HOME={data}", "{dir}/spaghetti.appimage"],
    } | fields


BUNDLE_CFG = REPO_ROOT / "bundle/console/es_systems_n64.cfg"


def _console(userdata: Path, *, rom: bool = True) -> Path:
    """The ROM folder and the installed theme's flip badges, as on the console."""
    roms = userdata / st.ROMS
    roms.mkdir(parents=True)
    if rom:
        (roms / "Mario Kart 64 (U) [!].z64").write_bytes(ROM)
    art = userdata / st.BADGES_FROM
    art.mkdir(parents=True)
    for name in ("flip-emulator.png", "flip-port.png"):
        (art / name).write_bytes(b"png")
    cfg = userdata / SYSTEMS_CFG  # installed by setup's es-systems-n64 (the strip reads it)
    cfg.parent.mkdir(parents=True)
    shutil.copy(BUNDLE_CFG, cfg)
    return roms


def test_setup_draws_the_strip_once_the_list_is_dressed(tmp_path: Path, userdata: Path) -> None:
    """AC-T20: after the badges, each game's strip under the cards is drawn for the new list."""
    _console(userdata)
    net = FakeNet({URL: KART, SOURCE_URL: SOURCE})
    report = _run(_bundle(tmp_path, [_kart()]), userdata, net)
    assert report.failed == {}
    strips = sorted(p.name for p in (userdata / STRIPS).iterdir())
    assert strips == ["Mario Kart 64 (U) [!].z64.png"]


def test_a_strip_that_cannot_be_drawn_is_reported(tmp_path: Path, userdata: Path) -> None:
    _console(userdata)
    (userdata / SYSTEMS_CFG).unlink()
    net = FakeNet({URL: KART, SOURCE_URL: SOURCE})
    report = _run(_bundle(tmp_path, [_kart()]), userdata, net)
    assert report.applied == ["port:spaghettikart"]  # the port itself is done
    assert list(report.failed) == ["strip"]
    assert "can't read the extensions" in report.failed["strip"]


def test_a_port_is_prepared_from_the_owners_rom_listed_and_badged(
    tmp_path: Path, userdata: Path
) -> None:
    roms = _console(userdata)
    net = FakeNet({URL: KART, SOURCE_URL: SOURCE})
    report = _run(_bundle(tmp_path, [_kart()]), userdata, net)
    assert report.applied == ["port:spaghettikart"]
    installed = userdata / st.PORTS_DIR / "spaghettikart"
    data = userdata / "saves/n64mini/spaghettikart"
    assert sorted(p.name for p in installed.iterdir()) == ["mk64.o2r", "spaghetti.appimage"]
    assert (installed / "mk64.o2r").read_bytes() == b"o2r:" + ROM
    assert (data / "mk64.o2r").resolve() == installed / "mk64.o2r"
    assert json.loads((userdata / "system/n64mini/ports.json").read_text()) == {
        "Mario Kart 64": ["env", f"SHIP_HOME={data}", f"{installed}/spaghetti.appimage"]
    }
    gamelist = (roms / "gamelist.xml").read_text()
    assert "<cartridge>./media/n64mini/flip-emulator.png</cartridge>" in gamelist


def test_a_port_without_the_owners_rom_waits_quietly_then_installs(
    tmp_path: Path, userdata: Path
) -> None:
    roms = _console(userdata, rom=False)
    notes: list[str] = []
    net = FakeNet({URL: KART, SOURCE_URL: SOURCE})
    bundle = _bundle(tmp_path, [_kart()])
    report = st.run(bundle, userdata, st.Options(fetch=net, notify=notes.append))
    assert report.applied == []
    assert report.failed == {}
    assert report.waiting["port:spaghettikart"].startswith("no ROM of Mario Kart 64 in ")
    assert notes == ["Setting up port:spaghettikart…"]  # no "failed" message on screen
    assert not (userdata / st.PORTS_DIR / "spaghettikart.new").exists()
    assert not (userdata / "system/n64mini/ports.json").exists()
    (roms / "Mario Kart 64 (U) [!].z64").write_bytes(ROM)  # the owner adds it
    assert _run(bundle, userdata, net).applied == ["port:spaghettikart"]


def test_stripping_needs_exactly_one_top_folder(tmp_path: Path, userdata: Path) -> None:
    _console(userdata)
    two = _tar({"a/meta/mods.toml": b"", "b/x": b""})
    net = FakeNet({URL: KART, SOURCE_URL: two})
    extra = {"url": SOURCE_URL, "sha256": _sha(two), "archive": "tar.gz", "strip": True}
    report = _run(_bundle(tmp_path, [_kart(extras=[extra])]), userdata, net)
    assert report.failed["port:spaghettikart"] == (
        f"{SOURCE_URL}: expected one top folder to strip, found 2"
    )


def test_an_extra_with_a_bad_checksum_fails_the_port(tmp_path: Path, userdata: Path) -> None:
    _console(userdata)
    net = FakeNet({URL: KART, SOURCE_URL: SOURCE + b"tampered"})
    report = _run(_bundle(tmp_path, [_kart()]), userdata, net)
    assert report.failed["port:spaghettikart"].startswith(f"checksum mismatch for {SOURCE_URL}")
    assert not (userdata / st.PORTS_DIR / "spaghettikart").exists()


def test_a_failing_recipe_step_keeps_the_old_version(tmp_path: Path, userdata: Path) -> None:
    _console(userdata)
    net = FakeNet({URL: KART, SOURCE_URL: SOURCE})
    _run(_bundle(tmp_path, [_kart()]), userdata, net)
    broken = _kart(version="1.0.1", run=[{"argv": [sys.executable, "-c", "raise SystemExit(4)"]}])
    report = _run(_bundle(tmp_path / "2", [broken]), userdata, net)
    assert report.failed["port:spaghettikart"].startswith(
        f"port:spaghettikart: {sys.executable} exited 4"
    )
    assert (userdata / st.PORTS_DIR / "spaghettikart" / "mk64.o2r").exists()
    assert st.load_state(userdata)["port:spaghettikart"] == "1.0.0"


def test_a_port_that_cannot_be_listed_says_so(tmp_path: Path, userdata: Path) -> None:
    _console(userdata)
    (userdata / "system/n64mini").mkdir(parents=True)
    (userdata / "system/n64mini/ports.json").write_text("[]")
    net = FakeNet({URL: KART, SOURCE_URL: SOURCE})
    report = _run(_bundle(tmp_path, [_kart()]), userdata, net)
    assert report.failed["port:spaghettikart"].startswith(
        "port:spaghettikart: installed, but not listed: "
    )


def test_missing_badges_are_reported_without_undoing_the_port(
    tmp_path: Path, userdata: Path
) -> None:
    _console(userdata)
    (userdata / st.BADGES_FROM / "flip-port.png").unlink()
    net = FakeNet({URL: KART, SOURCE_URL: SOURCE})
    report = _run(_bundle(tmp_path, [_kart()]), userdata, net)
    assert report.applied == ["port:spaghettikart"]
    missing = userdata / st.BADGES_FROM / "flip-port.png"
    assert report.failed == {"badges": f"badge not found: {missing}"}


def test_a_malformed_recipe_or_extra_is_refused(tmp_path: Path) -> None:
    with pytest.raises(st.SetupError, match="port:spaghettikart: rom and rom_sha1 go together"):
        st.load_manifest(_bundle(tmp_path, [_kart(rom="")]))
    bad_extra = [{"url": SOURCE_URL, "sha256": "nope", "archive": "tar.gz"}]
    with pytest.raises(st.SetupError, match="port:spaghettikart extra: needs a sha256"):
        st.load_manifest(_bundle(tmp_path / "2", [_kart(extras=bad_extra)]))
    escaping = [{"url": SOURCE_URL, "sha256": _sha(SOURCE), "archive": "tar.gz", "into": "../x"}]
    with pytest.raises(st.SetupError, match="into must be a relative path"):
        st.load_manifest(_bundle(tmp_path / "3", [_kart(extras=escaping)]))


def test_cli_lists_ports_waiting_for_a_rom(
    tmp_path: Path, userdata: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _console(userdata, rom=False)
    bundle = _bundle(tmp_path, [_kart(url="https://example.org/unused")])
    cache = userdata / st.CACHE
    cache.mkdir(parents=True)
    (cache / _sha(KART)).write_bytes(KART)  # cached: the CLI's real downloader isn't called
    (cache / _sha(SOURCE)).write_bytes(SOURCE)
    assert st.main([str(bundle), "--userdata", str(userdata)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("waiting  port:spaghettikart: no ROM of Mario Kart 64")


# ---------------------------------------------------------------- files, modes, content versions
def _cfg(**fields: object) -> dict[str, object]:
    return {
        "name": "es-systems-n64",
        "kind": "file",
        "version": "content",
        "source": "console/es_systems_n64.cfg",
        "target": "system/configs/emulationstation/es_systems_n64.cfg",
    } | fields


def _console_files(bundle: Path, text: str = "<systemList/>") -> None:
    (bundle / "console").mkdir(exist_ok=True)
    (bundle / "console" / "es_systems_n64.cfg").write_text(text)


def test_a_file_component_replaces_one_file_beside_others(tmp_path: Path, userdata: Path) -> None:
    bundle = _bundle(tmp_path, [_cfg()])
    _console_files(bundle)
    folder = userdata / "system/configs/emulationstation"
    folder.mkdir(parents=True)
    (folder / "es_settings.cfg").write_text("theirs")  # a file beside ours: never touched
    (folder / "es_systems_n64.cfg").write_text("old")
    assert _run(bundle, userdata, FakeNet({})).applied == ["es-systems-n64"]
    assert (folder / "es_systems_n64.cfg").read_text() == "<systemList/>"
    assert sorted(p.name for p in folder.iterdir()) == ["es_settings.cfg", "es_systems_n64.cfg"]


def test_a_leftover_file_from_an_interrupted_run_is_cleared(tmp_path: Path, userdata: Path) -> None:
    bundle = _bundle(tmp_path, [_cfg()])
    _console_files(bundle)
    folder = userdata / "system/configs/emulationstation"
    folder.mkdir(parents=True)
    (folder / "es_systems_n64.cfg.new").write_text("half-written")
    assert _run(bundle, userdata, FakeNet({})).applied == ["es-systems-n64"]
    assert sorted(p.name for p in folder.iterdir()) == ["es_systems_n64.cfg"]


def test_a_content_version_follows_the_bytes_not_the_modes(tmp_path: Path, userdata: Path) -> None:
    bundle = _bundle(tmp_path, [_cfg()])
    _console_files(bundle)
    source = bundle / "console" / "es_systems_n64.cfg"
    first = st.content_version(source)
    source.chmod(0o755)  # a FAT stick shows every file as executable
    assert st.content_version(source) == first
    assert st.content_version(source, ("*",)) != first  # the manifest's own declaration counts
    assert _run(bundle, userdata, FakeNet({})).applied == ["es-systems-n64"]
    assert _run(bundle, userdata, FakeNet({})).skipped == ["es-systems-n64"]
    source.write_text("<systemList><!-- v2 --></systemList>")
    assert _run(bundle, userdata, FakeNet({})).applied == ["es-systems-n64"]
    assert st.load_state(userdata)["es-systems-n64"] == st.content_version(source)


def test_a_folders_content_version_covers_names_and_bytes(tmp_path: Path) -> None:
    folder = tmp_path / "theme"
    folder.mkdir()
    (folder / "a.xml").write_text("x")
    first = st.content_version(folder)
    (folder / "a.xml").rename(folder / "b.xml")
    renamed = st.content_version(folder)
    (folder / "b.xml").write_text("y")
    assert len({first, renamed, st.content_version(folder)}) == 3
    assert first.startswith("content:")


def test_installed_modes_come_from_the_manifest_not_the_stick(
    tmp_path: Path, userdata: Path
) -> None:
    tool: dict[str, object] = {
        "name": "python",
        "kind": "files",
        "version": "content",
        "source": "py",
        "target": "system/n64mini/python",
        "executable": ["data/*.sh"],
    }
    script = _cfg(name="service", source="console/svc", target="system/services/n64mini")
    bundle = _bundle(tmp_path, [tool, script | {"executable": True}, _cfg()])
    _console_files(bundle)
    (bundle / "console" / "svc").write_text("#!/bin/bash\n")
    (bundle / "py" / "data").mkdir(parents=True)
    for name in ("run.sh", "games.json", "flip.py"):
        (bundle / "py" / "data" / name).write_text("x")
        (bundle / "py" / "data" / name).chmod(0o700)  # as a FAT stick shows them
    (bundle / "console" / "es_systems_n64.cfg").chmod(0o700)
    (bundle / "py" / "data" / "latest.sh").symlink_to("run.sh")  # links are copied, not chmodded
    _run(bundle, userdata, FakeNet({}))
    installed = userdata / "system/n64mini/python/data"
    assert str((installed / "latest.sh").readlink()) == "run.sh"

    def mode(path: Path) -> int:
        return path.stat().st_mode & 0o777

    assert mode(installed / "run.sh") == 0o755
    assert mode(installed / "games.json") == 0o644
    assert mode(installed / "flip.py") == 0o644
    assert mode(installed) == 0o755
    assert mode(userdata / "system/services/n64mini") == 0o755
    assert mode(userdata / "system/configs/emulationstation/es_systems_n64.cfg") == 0o644


def test_an_executable_glob_that_matches_nothing_fails_the_component(
    tmp_path: Path, userdata: Path
) -> None:
    tool = {**_theme(), "executable": ["torch"]}
    report = _run(_bundle(tmp_path, [tool]), userdata, FakeNet({}))
    assert report.failed == {"theme": "executable 'torch' matches no file"}
    assert not (userdata / "themes/n64-classic-mini").exists()


@pytest.mark.parametrize(
    ("component", "message"),
    [
        (_cfg(executable="yes"), "a file's executable is true or false"),
        ({**_theme(), "executable": "torch"}, "executable is a list of paths"),
        ({**_theme(), "executable": ["../x"]}, "executable must be a relative path"),
    ],
)
def test_a_malformed_executable_list_is_refused(
    tmp_path: Path, component: dict[str, object], message: str
) -> None:
    with pytest.raises(st.SetupError, match=message):
        st.load_manifest(_bundle(tmp_path, [component]))


def test_a_content_version_needs_its_source_and_a_port_cannot_use_one(
    tmp_path: Path, userdata: Path
) -> None:
    bundle = _bundle(tmp_path, [_cfg(), _port(version="content")])
    report = _run(bundle, userdata, FakeNet({URL: PORT}))
    assert report.failed == {
        "es-systems-n64": f"{bundle / 'console/es_systems_n64.cfg'} isn't in the bundle",
        "port:starship": "port:starship: a port's version must be its release, not 'content'",
    }


def test_a_file_component_whose_source_is_a_folder_fails(tmp_path: Path, userdata: Path) -> None:
    bundle = _bundle(tmp_path, [_cfg(source="theme", version="1")])
    assert _run(bundle, userdata, FakeNet({})).failed == {
        "es-systems-n64": f"es-systems-n64: {bundle / 'theme'} isn't in the bundle"
    }


def test_a_new_theme_refreshes_the_badges_and_no_roms_means_nothing_to_badge(
    tmp_path: Path, userdata: Path
) -> None:
    bundle = _bundle(tmp_path, [_theme(version="content")])
    (bundle / "theme" / "art").mkdir()
    for name in ("flip-emulator.png", "flip-port.png"):
        (bundle / "theme" / "art" / name).write_bytes(b"png")
    assert _run(bundle, userdata, FakeNet({})).failed == {}  # no ROM folder: nothing to badge
    roms = userdata / st.ROMS
    roms.mkdir(parents=True)
    assert _run(bundle, userdata, FakeNet({})).failed == {}  # an empty one: the same
    (roms / "Mario Kart 64 (U) [!].z64").write_bytes(ROM)
    (bundle / "theme" / "theme.xml").write_text("<theme><!-- v2 --></theme>")
    assert _run(bundle, userdata, FakeNet({})).applied == ["theme"]
    assert (roms / "media/n64mini/flip-port.png").exists()  # the badges were copied in


# ---------------------------------------------------------------- the boot service's --engine
class _FakeEngine:
    def __init__(self) -> None:
        self.notes: list[str] = []
        self.reloads = 0

    def notify(self, message: str) -> None:
        self.notes.append(message)

    def reload_games(self) -> None:
        self.reloads += 1


def test_engine_mode_shows_progress_then_reloads_the_game_lists(
    tmp_path: Path, userdata: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _FakeEngine()
    monkeypatch.setattr(st, "engine_for_boot", lambda: engine)
    bundle = _bundle(tmp_path, [_theme()])
    assert st.main([str(bundle), "--userdata", str(userdata), "--engine"]) == 0
    assert engine.notes == ["Setting up theme…", "N64 Mini is up to date"]
    assert engine.reloads == 1
    assert st.main([str(bundle), "--userdata", str(userdata), "--engine"]) == 0
    assert engine.reloads == 1  # nothing changed: no reload, no message
    assert engine.notes == ["Setting up theme…", "N64 Mini is up to date"]


def test_engine_mode_carries_on_when_the_engine_never_answers(
    tmp_path: Path,
    userdata: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _Down:
        def wait_until_up(self, seconds: float) -> bool:
            assert seconds == st.ENGINE_WAIT
            return False

    monkeypatch.setattr(st, "Engine", _Down)
    bundle = _bundle(tmp_path, [_theme()])
    assert st.main([str(bundle), "--userdata", str(userdata), "--engine"]) == 0
    out = capsys.readouterr().out
    assert out == (
        f"setup: the engine didn't answer in {st.ENGINE_WAIT} s; no progress on screen\n"
        "applied  theme\n"
    )


def test_engine_for_boot_returns_the_engine_once_it_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Up:
        def wait_until_up(self, seconds: float) -> bool:
            return seconds == st.ENGINE_WAIT

    monkeypatch.setattr(st, "Engine", _Up)
    assert isinstance(st.engine_for_boot(), _Up)


# ---------------------------------------------------------------- DEC-6: the engine's settings
def _settings(**fields: object) -> dict[str, object]:
    return {
        "name": "settings",
        "kind": "es-settings",
        "version": "content",
        "settings": {"ThemeSet": "n64-classic-mini", "StartupOnGameList": True},
        "only_system": "n64",
        "restart_engine": True,
    } | fields


def _engine_dir(tmp_path: Path, systems: str = "n64 snes nes") -> Path:
    stock = tmp_path / "share-emulationstation"
    stock.mkdir(exist_ok=True)
    body = "".join(f"<system><name>{s}</name></system>" for s in systems.split())
    (stock / "es_systems.cfg").write_text(f"<systemList>{body}</systemList>")
    return stock


def test_settings_merge_into_the_engines_file_and_hide_other_systems(
    tmp_path: Path, userdata: Path
) -> None:
    stock = _engine_dir(tmp_path)
    cfg = userdata / st.ES_SETTINGS
    cfg.parent.mkdir(parents=True)
    cfg.write_text('<config>\n\t<string name="Language" value="fr_FR" />\n</config>\n')
    report = st.run(_bundle(tmp_path, [_settings()]), userdata, st.Options(es_dir=stock))
    assert report.applied == ["settings"]
    assert cfg.read_text() == (
        '<?xml version="1.0"?>\n<config>\n'
        '\t<string name="Language" value="fr_FR" />\n'
        '\t<string name="ThemeSet" value="n64-classic-mini" />\n'
        '\t<bool name="StartupOnGameList" value="true" />\n'
        '\t<string name="HiddenSystems" value="nes;snes" />\n'
        "</config>\n"
    )
    assert (userdata / "system/batocera.conf").read_text() == (
        "n64.bezel=none\n"
        "splash.screen.enabled=1\n"
        "splash.screen.subtitle=0\n"
        "es.customsargs=--no-splash\n"
        "kodi.enabled=0\n"
        "kodi.atstartup=0\n"
        "kodi.xbutton=0\n"
    )


def test_a_new_system_on_the_console_redoes_the_settings(tmp_path: Path, userdata: Path) -> None:
    """A Batocera update that adds a system gets it hidden on the next run."""
    bundle = _bundle(tmp_path, [_settings()])
    opts = st.Options(es_dir=_engine_dir(tmp_path))
    assert st.run(bundle, userdata, opts).applied == ["settings"]
    second = st.run(bundle, userdata, opts)
    assert second.skipped == ["settings"]
    _engine_dir(tmp_path, "n64 snes nes psx")
    assert st.run(bundle, userdata, opts).applied == ["settings"]
    assert "nes;psx;snes" in (userdata / st.ES_SETTINGS).read_text()


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"settings": {}}, "settings maps names to true/false, numbers or text"),
        ({"settings": {"A": [1]}}, "settings maps names to true/false, numbers or text"),
        ({"version": "1"}, "its version is the settings' own"),
        ({"restart_engine": "yes"}, "restart_engine is true or false"),
    ],
)
def test_a_malformed_settings_component_is_refused(
    tmp_path: Path, fields: dict[str, object], message: str
) -> None:
    with pytest.raises(st.SetupError, match=message):
        st.load_manifest(_bundle(tmp_path, [_settings(**fields)]))


def test_settings_for_a_system_the_console_lacks_fail(tmp_path: Path, userdata: Path) -> None:
    opts = st.Options(es_dir=_engine_dir(tmp_path, "snes"))
    report = st.run(_bundle(tmp_path, [_settings()]), userdata, opts)
    assert report.failed == {"settings": "settings: no system named 'n64' on this console"}


def test_an_unreadable_settings_file_is_left_alone(tmp_path: Path, userdata: Path) -> None:
    cfg = userdata / st.ES_SETTINGS
    cfg.parent.mkdir(parents=True)
    cfg.write_text("<config><list/></config>")
    opts = st.Options(es_dir=_engine_dir(tmp_path))
    report = st.run(_bundle(tmp_path, [_settings()]), userdata, opts)
    assert report.failed["settings"].startswith("settings: ")
    assert "unexpected content" in report.failed["settings"]
    assert cfg.read_text() == "<config><list/></config>"


# ---------------------------------------------------------------- phases: live, then engine stopped
def _phased(tmp_path: Path) -> Path:
    """A bundle with one start-up-only component (settings) and one live one (a file)."""
    bundle = _bundle(tmp_path, [_settings(), _cfg(name="script", target="system/x.sh")])
    _console_files(bundle)
    return bundle


def test_each_phase_covers_its_own_components(tmp_path: Path, userdata: Path) -> None:
    """The engine writes its settings back as it quits (ViewController::saveState), so the
    components it reads only at start-up are set up apart, while it's stopped."""
    bundle, stock = _phased(tmp_path), _engine_dir(tmp_path)
    live = st.run(bundle, userdata, st.Options(es_dir=stock, phase="live"))
    assert live.applied == ["script"]
    stopped = st.run(bundle, userdata, st.Options(es_dir=stock, phase="stopped"))
    assert stopped.applied == ["settings"]
    assert st.run(bundle, userdata, st.Options(es_dir=stock)).skipped == ["settings", "script"]


def test_pending_lists_what_a_phase_would_change_without_changing_it(
    tmp_path: Path, userdata: Path
) -> None:
    bundle, stock = _phased(tmp_path), _engine_dir(tmp_path)
    assert st.pending(bundle, userdata, st.Options(es_dir=stock)) == ["settings", "script"]
    assert st.pending(bundle, userdata, st.Options(es_dir=stock, phase="stopped")) == ["settings"]
    assert not (userdata / st.ES_SETTINGS).exists()
    st.run(bundle, userdata, st.Options(es_dir=stock))
    assert st.pending(bundle, userdata, st.Options(es_dir=stock)) == []
    assert st.pending(bundle, userdata, st.Options(es_dir=stock, force=True)) == [
        "settings",
        "script",
    ]
    # one whose version can't be worked out is pending too: the run would report it
    assert st.pending(bundle, userdata, st.Options(es_dir=tmp_path / "none")) == ["settings"]


def test_cli_pending_prints_one_name_per_line(
    tmp_path: Path, userdata: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle, stock = _phased(tmp_path), str(_engine_dir(tmp_path))
    args = [str(bundle), "--userdata", str(userdata), "--es-dir", stock, "--pending"]
    assert st.main([*args, "--phase", "stopped"]) == 0
    assert capsys.readouterr().out == "settings\n"
    assert st.main(args) == 0
    assert capsys.readouterr().out == "settings\nscript\n"
    assert not (userdata / st.STATE).exists()


def test_the_live_phase_says_a_restart_follows_when_start_up_settings_wait(
    tmp_path: Path, userdata: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _FakeEngine()
    monkeypatch.setattr(st, "engine_for_boot", lambda: engine)
    bundle, stock = _phased(tmp_path), str(_engine_dir(tmp_path))
    args = [str(bundle), "--userdata", str(userdata), "--es-dir", stock]
    assert st.main([*args, "--phase", "live", "--engine"]) == 0
    assert engine.notes[-1] == "N64 Mini: restarting to finish setting up"
    assert engine.reloads == 0  # update.sh suspends the engine next; a reload would be lost
    assert st.main([*args, "--phase", "stopped"]) == 0  # (run by update.sh, engine stopped)
    (bundle / "console" / "es_systems_n64.cfg").write_text("<systemList><!-- v2 --></systemList>")
    assert st.main([*args, "--phase", "live", "--engine"]) == 0
    assert engine.notes[-1] == "N64 Mini is up to date"
    assert engine.reloads == 1


def test_a_port_set_up_before_the_theme_waits_for_it_to_badge(
    tmp_path: Path, userdata: Path
) -> None:
    """Found on the VM: the live phase set up the ports while the theme (and its badge art)
    was still waiting for the engine to stop, and the badge refresh failed."""
    roms = userdata / st.ROMS
    roms.mkdir(parents=True)
    (roms / "Mario Kart 64 (U) [!].z64").write_bytes(ROM)
    (userdata / SYSTEMS_CFG).parent.mkdir(parents=True)
    shutil.copy(BUNDLE_CFG, userdata / SYSTEMS_CFG)
    bundle = _bundle(tmp_path, [{**_theme(version="content"), "restart_engine": True}, _kart()])
    (bundle / "theme" / "art").mkdir()
    for name in ("flip-emulator.png", "flip-port.png"):
        (bundle / "theme" / "art" / name).write_bytes(b"png")
    net = FakeNet({URL: KART, SOURCE_URL: SOURCE})
    live = st.run(bundle, userdata, st.Options(fetch=net, phase="live"))
    assert live.applied == ["port:spaghettikart"]
    assert live.failed == {}  # not "badge not found": the theme isn't installed yet
    stopped = st.run(bundle, userdata, st.Options(fetch=net, phase="stopped"))
    assert stopped.applied == ["theme"]
    assert stopped.failed == {}
    assert (
        "<cartridge>./media/n64mini/flip-emulator.png</cartridge>"
        in (roms / "gamelist.xml").read_text()
    )


def test_installing_the_library_card_dresses_the_gamelist(tmp_path: Path, userdata: Path) -> None:
    """A component installed into the ROM folder (the AC-T14 card) re-runs the badge refresh."""
    roms = _console(userdata)
    card = _cfg(name="library-card", target=f"{st.ROMS}/{CARD}")
    bundle = _bundle(tmp_path, [card])
    _console_files(bundle)
    for name in CARD_IMAGES.values():
        (userdata / st.BADGES_FROM / name).write_bytes(b"png")
    assert _run(bundle, userdata, FakeNet({})).applied == ["library-card"]
    # DEC-7: the first dressing starts the console in the Showcase, so the card offers Full
    assert "<name>Full library</name>" in (roms / "gamelist.xml").read_text()
    assert (userdata / st.SHOWCASE_STARTED).exists()


def test_after_the_first_start_the_owners_choice_stands(tmp_path: Path, userdata: Path) -> None:
    roms = _console(userdata)
    (userdata / st.SHOWCASE_STARTED).parent.mkdir(parents=True, exist_ok=True)
    (userdata / st.SHOWCASE_STARTED).write_text("done")  # started before; the owner chose Full
    bundle = _bundle(tmp_path, [_cfg(name="library-card", target=f"{st.ROMS}/{CARD}")])
    _console_files(bundle)
    for name in CARD_IMAGES.values():
        (userdata / st.BADGES_FROM / name).write_bytes(b"png")
    _run(bundle, userdata, FakeNet({}))
    assert "<name>The Showcase</name>" in (roms / "gamelist.xml").read_text()  # still Full


def test_with_no_roms_yet_the_showcase_start_waits(tmp_path: Path, userdata: Path) -> None:
    roms = _console(userdata, rom=False)
    (roms / CARD).write_text("")
    st.run(_bundle(tmp_path, [_theme()]), userdata, st.Options())
    assert not (userdata / st.SHOWCASE_STARTED).exists()


def test_a_showcase_that_cannot_start_is_reported(tmp_path: Path, userdata: Path) -> None:
    roms = _console(userdata, rom=False)
    (roms / "Unknown Game (U) [!].z64").write_bytes(ROM)  # a ROM, but no Showcase game
    (roms / CARD).write_text("")
    report = st.run(_bundle(tmp_path, [_theme(version="content")]), userdata, st.Options())
    assert report.failed["showcase"].startswith("none of the Showcase's games is in")
    assert not (userdata / st.SHOWCASE_STARTED).exists()  # tried again next time
