"""``n64mini-setup``: bring the console to the bundle's versions, on every boot (AC-S02..S08).

The bundle (on a USB stick, DEC-S1) holds ``n64mini.json``, a manifest of components, each
with a version:

- ``files``: a folder in the bundle copied to a place under ``/userdata`` (theme, Python);
- ``file``: one file in the bundle copied to a path under ``/userdata``, for config files that
  live in folders other things share (``es_systems_n64.cfg``, an event script, the service);
- ``es-settings``: the engine's settings merged into ``es_settings.cfg`` (``es_settings.py``);
- ``port``: a native port, downloaded from a pinned URL, rejected unless its SHA-256 matches,
  and unpacked into a private folder, with any ``extras`` (more pinned archives) beside it. A port
  with a ``game`` then follows its recipe (``recipe.py``): the owner's ROM, preparation commands,
  and a listing in ``ports.json``. A port whose ROM isn't there yet *waits*: it's retried on the
  next boot without being reported as a failure.

A ``files`` or ``file`` component may give its version as ``"content"``: its version is then a
hash of what the bundle holds, so it's redone exactly when that changes (AC-S07).

A component already at its version is skipped, so a normal boot does nothing. A component is
built beside the working one (``<target>.new``) and swapped in only when complete, so a failure
leaves the previous version in place; it's reported, the other components carry on, and the
next boot tries again. Downloads are cached by checksum and can come from a mirror, so a re-run
doesn't re-download.

Runs on the console, so it uses only the standard library.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import shutil
import sys
import tarfile
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Final, Literal, cast

from n64mini.console import es_settings as es
from n64mini.console import recipe as rc
from n64mini.console.engine import Engine
from n64mini.console.flip import FlipError, init
from n64mini.console.showcase import CARD, ROM_SUFFIXES, ShowcaseError, switch
from n64mini.console.strip import StripError, redraw

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

MANIFEST: Final = "n64mini.json"
STATE: Final = "system/n64mini/state.json"  # under /userdata: component -> installed version
CACHE: Final = "system/n64mini/cache"  # downloads, by SHA-256
PORTS_DIR: Final = "system/n64mini/ports"  # never roms/ports (DEC-6)
ROMS: Final = "roms/n64"
THEME: Final = "themes/n64-classic-mini"
BADGES_FROM: Final = f"{THEME}/art"  # the theme component's flip badges
ES_CONFIG: Final = "system/configs/emulationstation"  # the engine's user folder
ES_SETTINGS: Final = f"{ES_CONFIG}/es_settings.cfg"
ES_DIR: Final = Path("/usr/share/emulationstation")  # Batocera's es_systems.cfg (SystemData.cpp)
SHOWCASE_STARTED: Final = "system/n64mini/showcase-started"  # DEC-7: done once per console
ENGINE_WAIT: Final = 120  # seconds: at boot the engine starts alongside the services
_NAME_RE: Final = re.compile(r"[a-z0-9][a-z0-9:_.-]*")
_SHA_RE: Final = re.compile(r"[0-9a-f]{64}")
_FORMATS: Final = ("tar.gz", "zip")
CONTENT: Final = "content"  # a version that is the hash of the bundled source
_REDIRECTS: Final = frozenset({301, 302, 303, 307, 308})

type Kind = Literal["files", "file", "es-settings", "port"]
# Which components a run covers: those the engine reads only at start-up ("stopped", applied while
# it's suspended: it writes its whole settings back as it quits, ViewController::saveState), the
# rest ("live", with progress on screen), or both (no engine running at all).
type Phase = Literal["all", "live", "stopped"]
PHASES: Final = ("all", "live", "stopped")
type Fetch = Callable[[str, Path], None]
type Notify = Callable[[str], None]


class SetupError(Exception):
    """A component (or the manifest) can't be set up; nothing of it was changed."""


@dataclass(frozen=True, slots=True)
class Download:
    """A pinned archive: where from, its checksum, its format and where it unpacks."""

    url: str
    sha256: str
    archive: str
    into: str = ""  # a folder inside the port's; "" is the port's own
    strip: bool = False  # the archive holds one top folder: unpack what's inside it


@dataclass(frozen=True, slots=True)
class Component:
    """One thing the setup installs, at one version."""

    name: str
    kind: Kind
    version: str
    target: str  # under /userdata
    source: str = ""  # files: folder in the bundle; file: one file
    executable: tuple[str, ...] = ()  # files: globs in the folder; file: ("*",) or ()
    url: str = ""  # port: where to download
    sha256: str = ""  # port: the download's checksum
    archive: str = ""  # port: "tar.gz" or "zip"
    extras: tuple[Download, ...] = ()  # port: more archives, unpacked into its folder
    recipe: rc.Recipe | None = None  # port: how it's prepared and listed
    settings: tuple[tuple[str, es.Value], ...] = ()  # es-settings: name, value
    only_system: str = ""  # es-settings: the one system left visible
    restart_engine: bool = False  # the engine reads it only at start-up: set up while it's stopped

    @property
    def download(self) -> Download:
        """The port's own archive."""
        return Download(self.url, self.sha256, self.archive)


@dataclass(slots=True)
class Report:
    """What a run did."""

    applied: list[str] = field(default_factory=list[str])
    skipped: list[str] = field(default_factory=list[str])
    failed: dict[str, str] = field(default_factory=dict[str, str])
    waiting: dict[str, str] = field(default_factory=dict[str, str])  # for the owner's ROM


def _safe_relative(value: str, what: str) -> str:
    try:
        return rc.relative(value, what)
    except rc.RecipeError as exc:
        raise SetupError(str(exc)) from exc


def _executable(name: str, kind: str, value: object) -> tuple[str, ...]:
    """Which installed files are executable; the stick's own modes can't be trusted (FAT)."""
    if value is None:
        return ()
    if kind == "file":
        if not isinstance(value, bool):
            raise SetupError(f"{name}: a file's executable is true or false")
        return ("*",) if value else ()
    items = cast("list[object]", value) if isinstance(value, list) else None
    if items is None or not all(isinstance(g, str) and g for g in items):
        raise SetupError(f"{name}: executable is a list of paths in the folder (globs)")
    return tuple(_safe_relative(str(g), f"{name}: executable") for g in items)


def _download(raw: dict[str, object], what: str) -> Download:
    sha, archive = str(raw["sha256"]).lower(), str(raw["archive"])
    if not _SHA_RE.fullmatch(sha) or archive not in _FORMATS:
        raise SetupError(f"{what}: needs a sha256 and an archive of {', '.join(_FORMATS)}")
    into = str(raw.get("into", ""))
    return Download(
        str(raw["url"]),
        sha,
        archive,
        _safe_relative(into, f"{what}: into") if into else "",
        strip=raw.get("strip") is True,
    )


def _port(name: str, version: str, raw: dict[str, object]) -> Component:
    main = _download(raw, name)
    extras = cast("list[dict[str, object]]", raw.get("extras", []))
    try:
        recipe = rc.parse(raw) if "game" in raw else None
    except rc.RecipeError as exc:
        raise SetupError(f"{name}: {exc}") from exc
    return Component(
        name,
        "port",
        version,
        f"{PORTS_DIR}/{name.removeprefix('port:')}",
        url=main.url,
        sha256=main.sha256,
        archive=main.archive,
        extras=tuple(_download(e, f"{name} extra") for e in extras),
        recipe=recipe,
    )


def _component(raw: dict[str, object]) -> Component:
    name, kind, version = str(raw["name"]), str(raw["kind"]), str(raw["version"])
    if not _NAME_RE.fullmatch(name) or not version:
        raise SetupError(f"bad component name or version: {name!r} {version!r}")
    restart = raw.get("restart_engine", False)
    if not isinstance(restart, bool):
        raise SetupError(f"{name}: restart_engine is true or false")
    if kind in ("files", "file"):
        return Component(
            name,
            cast("Kind", kind),
            version,
            _safe_relative(str(raw["target"]), f"{name}: target"),
            source=_safe_relative(str(raw["source"]), f"{name}: source"),
            executable=_executable(name, kind, raw.get("executable")),
            restart_engine=restart,
        )
    if kind == "es-settings":
        return _es_settings(name, version, raw, restart=restart)
    if kind == "port":
        return _port(name, version, raw)
    raise SetupError(f"{name}: unknown kind {kind!r}")


def _es_settings(name: str, version: str, raw: dict[str, object], *, restart: bool) -> Component:
    values = raw.get("settings")
    settings = cast("dict[str, object]", values) if isinstance(values, dict) else None
    if not settings or not all(isinstance(v, (bool, int, str)) for v in settings.values()):
        raise SetupError(f"{name}: settings maps names to true/false, numbers or text")
    if version != CONTENT:
        raise SetupError(f"{name}: its version is the settings' own ({CONTENT!r})")
    return Component(
        name,
        "es-settings",
        version,
        ES_SETTINGS,
        settings=tuple((k, cast("es.Value", v)) for k, v in settings.items()),
        only_system=str(raw["only_system"]),
        restart_engine=restart,
    )


def load_manifest(bundle: Path) -> list[Component]:
    """The bundle's components. Raises SetupError if the manifest is malformed."""
    try:
        raw = json.loads((bundle / MANIFEST).read_text(encoding="utf-8"))
        entries = cast("list[dict[str, object]]", raw["components"])
        components = [_component(e) for e in entries]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SetupError(f"manifest {bundle / MANIFEST}: {exc}") from exc
    names = [c.name for c in components]
    if len(set(names)) != len(names):
        raise SetupError("manifest names a component twice")
    return components


def load_state(userdata: Path) -> dict[str, str]:
    """Installed versions, by component; empty on a fresh console."""
    path = userdata / STATE
    if not path.exists():
        return {}
    data: object = json.loads(path.read_text(encoding="utf-8"))
    state = cast("dict[str, object]", data) if isinstance(data, dict) else None
    if state is None or not all(isinstance(v, str) for v in state.values()):
        raise SetupError(f"{path} must map component names to versions")
    return {k: str(v) for k, v in state.items()}


def _save_state(userdata: Path, state: dict[str, str]) -> None:
    path = userdata / STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def download(url: str, dest: Path, redirects: int = 5) -> None:
    """Fetch ``url`` to ``dest`` over HTTPS, following redirects (GitHub releases redirect)."""
    for _ in range(redirects + 1):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise SetupError(f"only https downloads: {url!r}")
        conn = http.client.HTTPSConnection(parts.hostname, parts.port, timeout=60)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        conn.request("GET", path or "/", headers={"User-Agent": "n64mini-setup"})
        response = conn.getresponse()
        if response.status in _REDIRECTS and (location := response.getheader("Location")):
            url = urllib.parse.urljoin(url, location)
            conn.close()
            continue
        if response.status != HTTPStatus.OK:
            raise SetupError(f"{url}: HTTP {response.status}")
        with dest.open("wb") as out:
            shutil.copyfileobj(response, out)
        return
    raise SetupError(f"{url}: more than {redirects} redirects")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_verified(item: Download, userdata: Path, fetch: Fetch, mirror: str | None = None) -> Path:
    """The download, from the cache if it's there, else fetched and checked."""
    cache = userdata / CACHE / item.sha256
    if cache.exists() and _sha256(cache) == item.sha256:
        return cache
    cache.parent.mkdir(parents=True, exist_ok=True)
    partial = cache.with_name(cache.name + ".part")
    url = f"{mirror.rstrip('/')}/{item.sha256}" if mirror else item.url
    try:
        fetch(url, partial)
    except (OSError, SetupError) as exc:
        raise SetupError(f"download failed ({url}): {exc}") from exc
    actual = _sha256(partial)
    if actual != item.sha256:
        partial.unlink()
        raise SetupError(f"checksum mismatch for {url}: got {actual}")
    partial.replace(cache)
    return cache


def unpack(archive: Path, kind: str, dest: Path) -> None:
    """Unpack into ``dest``, refusing any member that would land outside it."""
    dest.mkdir(parents=True)
    if kind == "tar.gz":
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(dest, filter="data")  # rejects absolute paths, "..", odd links
        return
    with zipfile.ZipFile(archive) as zf:
        root = dest.resolve()
        for info in zf.infolist():
            out = (dest / info.filename).resolve()
            if not out.is_relative_to(root) or PurePosixPath(info.filename).is_absolute():
                raise SetupError(f"archive member escapes its folder: {info.filename!r}")
            if info.is_dir():
                out.mkdir(parents=True, exist_ok=True)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            if info.external_attr >> 16 & 0o111:  # keep the executable bit (the port's binary)
                out.chmod(0o755)


def _unpack_download(item: Download, archive: Path, dest: Path) -> None:
    if not item.strip:
        unpack(archive, item.archive, dest)
        return
    tmp = dest.with_name(dest.name + ".unpack")
    unpack(archive, item.archive, tmp)
    tops = list(tmp.iterdir())
    if len(tops) != 1 or not tops[0].is_dir():
        raise SetupError(f"{item.url}: expected one top folder to strip, found {len(tops)}")
    tops[0].replace(dest)
    tmp.rmdir()


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _swap_in(new: Path, target: Path) -> None:
    if not new.is_dir():  # one file: a rename replaces it in a single step
        new.replace(target)
        return
    old = target.with_name(target.name + ".old")
    _remove(old)
    if target.exists():
        target.replace(old)
    new.replace(target)
    _remove(old)


def content_version(source: Path, executable: Sequence[str] = ()) -> str:
    """A version naming what ``source`` (a file or a folder) holds: names and bytes.

    Modes don't count, since a FAT stick has none; the manifest's ``executable`` does.
    """
    if not source.exists():
        raise SetupError(f"{source} isn't in the bundle")
    digest = hashlib.sha256("\0".join(executable).encode())
    files = sorted(p for p in source.rglob("*") if p.is_file()) if source.is_dir() else [source]
    for path in files:
        name = path.relative_to(source).as_posix() if source.is_dir() else ""
        digest.update(f"\0{name}\0{path.stat().st_size}\0".encode())
        digest.update(path.read_bytes())
    return f"{CONTENT}:{digest.hexdigest()[:16]}"


def _set_modes(installed: Path, executable: Sequence[str]) -> None:
    """Give the copy plain modes, then make exactly the listed files executable."""
    if installed.is_file():
        installed.chmod(0o755 if executable else 0o644)
        return
    for path in installed.rglob("*"):
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() else 0o644)
    for pattern in executable:
        matches = [p for p in installed.glob(pattern) if p.is_file()]
        if not matches:
            raise SetupError(f"executable {pattern!r} matches no file")
        for path in matches:
            path.chmod(0o755)


def _wanted_settings(comp: Component, userdata: Path, es_dir: Path) -> dict[str, tuple[str, str]]:
    try:
        return es.wanted(dict(comp.settings), comp.only_system, (userdata / ES_CONFIG, es_dir))
    except (es.SettingsError, OSError) as exc:
        raise SetupError(f"{comp.name}: {exc}") from exc


def version_of(comp: Component, bundle: Path, userdata: Path, es_dir: Path = ES_DIR) -> str:
    """The version to install: the manifest's, or for ``"content"`` a hash of what it installs."""
    if comp.version != CONTENT:
        return comp.version
    if comp.kind == "port":
        raise SetupError(f"{comp.name}: a port's version must be its release, not {CONTENT!r}")
    if comp.kind == "es-settings":
        return es.version(_wanted_settings(comp, userdata, es_dir))
    return content_version(bundle / comp.source, comp.executable)


def quiet(_message: str) -> None:
    """No progress messages: the default when nothing shows them."""


@dataclass(frozen=True, slots=True)
class Options:
    """How a run goes: which components, whether to redo them, and where downloads come from."""

    only: Sequence[str] = ()
    force: bool = False
    fetch: Fetch = download
    mirror: str | None = None
    notify: Notify = quiet
    es_dir: Path = ES_DIR  # the engine's own folder, with Batocera's es_systems.cfg
    phase: Phase = "all"


def paths_for(comp: Component, userdata: Path) -> rc.Paths:
    """Where a port's recipe works: its build, installed and data folders, and the ROMs."""
    installed = userdata / comp.target
    return rc.Paths(
        userdata=userdata,
        build=installed.with_name(installed.name + ".new"),
        installed=installed,
        data=userdata / rc.DATA_DIR / installed.name,
        roms=userdata / ROMS,
    )


def _build(comp: Component, bundle: Path, new: Path, userdata: Path, opts: Options) -> None:
    if comp.kind == "es-settings":
        try:
            es.merge(userdata / comp.target, _wanted_settings(comp, userdata, opts.es_dir), new)
        except es.SettingsError as exc:
            raise SetupError(f"{comp.name}: {exc}") from exc
        _set_modes(new, ())
        conf = userdata / es.BATOCERA_CONF
        conf_new = conf.with_name(conf.name + ".new")
        conf.parent.mkdir(parents=True, exist_ok=True)
        es.merge_conf(
            conf,
            {
                f"{comp.only_system}.bezel": "none",
                "splash.screen.enabled": "1",
                "splash.screen.subtitle": "0",
                "es.customsargs": "--no-splash",
                "kodi.enabled": "0",
                "kodi.atstartup": "0",
                "kodi.xbutton": "0",
            },
            conf_new,
        )
        _set_modes(conf_new, ())
        _swap_in(conf_new, conf)
        return
    if comp.kind != "port":
        source = bundle / comp.source
        if comp.kind == "files" and source.is_dir():
            shutil.copytree(source, new, symlinks=True, copy_function=shutil.copyfile)
        elif comp.kind == "file" and source.is_file():
            shutil.copyfile(source, new)
        else:
            raise SetupError(f"{comp.name}: {source} isn't in the bundle")
        _set_modes(new, comp.executable)
        return
    for item in (comp.download, *comp.extras):
        archive = fetch_verified(item, userdata, opts.fetch, opts.mirror)
        _unpack_download(item, archive, new / item.into if item.into else new)
    if comp.recipe:
        rc.prepare(comp.recipe, paths_for(comp, userdata))


def apply(comp: Component, bundle: Path, userdata: Path, opts: Options | None = None) -> None:
    """Build the component beside its target and swap it in; on failure, change nothing."""
    target = userdata / comp.target
    new = target.with_name(target.name + ".new")
    _remove(new)  # left over from an interrupted run
    new.parent.mkdir(parents=True, exist_ok=True)
    try:
        _build(comp, bundle, new, userdata, opts or Options())
    except rc.RomMissingError:
        _remove(new)
        raise
    except (OSError, tarfile.TarError, zipfile.BadZipFile, rc.RecipeError) as exc:
        _remove(new)
        raise SetupError(f"{comp.name}: {exc}") from exc
    except SetupError:
        _remove(new)
        raise
    _swap_in(new, target)
    if comp.recipe:
        try:
            rc.finish(comp.recipe, paths_for(comp, userdata))
        except (OSError, rc.RecipeError) as exc:
            raise SetupError(f"{comp.name}: installed, but not listed: {exc}") from exc


def _selected(bundle: Path, opts: Options) -> tuple[list[Component], list[Component]]:
    """The manifest's components, and those this run covers (``only`` and the phase)."""
    components = load_manifest(bundle)
    unknown = set(opts.only) - {c.name for c in components}
    if unknown:
        raise SetupError(f"not in the manifest: {', '.join(sorted(unknown))}")
    wanted = {"all": (True, False), "live": (False,), "stopped": (True,)}[opts.phase]
    chosen = [
        c
        for c in components
        if (not opts.only or c.name in opts.only)
        and (opts.phase == "all" or c.restart_engine in wanted)
    ]
    return components, chosen


def pending(bundle: Path, userdata: Path, options: Options | None = None) -> list[str]:
    """The components a run would set up (not at their version), without setting anything up.

    One whose version can't be worked out counts too: the run would report it.
    """
    opts = options or Options()
    state = load_state(userdata)
    due: list[str] = []
    for comp in _selected(bundle, opts)[1]:
        try:
            version = version_of(comp, bundle, userdata, opts.es_dir)
        except SetupError:
            due.append(comp.name)
            continue
        if opts.force or state.get(comp.name) != version:
            due.append(comp.name)
    return due


def run(bundle: Path, userdata: Path, options: Options | None = None) -> Report:
    """Bring every component this run covers (``options.only``, the phase) to its version."""
    opts = options or Options()
    components, chosen = _selected(bundle, opts)
    state = load_state(userdata)
    report = Report()
    for comp in chosen:
        try:
            version = version_of(comp, bundle, userdata, opts.es_dir)
        except SetupError as exc:
            report.failed[comp.name] = str(exc)
            continue
        if not opts.force and state.get(comp.name) == version:
            report.skipped.append(comp.name)
            continue
        opts.notify(f"Setting up {comp.name}…")
        try:
            apply(comp, bundle, userdata, opts)
        except rc.RomMissingError as exc:
            report.waiting[comp.name] = str(exc)
            continue
        except SetupError as exc:
            report.failed[comp.name] = str(exc)
            opts.notify(f"{comp.name} failed; will retry next boot")
            continue
        state[comp.name] = version
        _save_state(userdata, state)  # after each one, so a crash keeps what's done
        report.applied.append(comp.name)
    changed = [c for c in components if c.name in report.applied]
    dressing = [c for c in changed if c.recipe or c.target == THEME or c.target.startswith(ROMS)]
    if dressing and _theme_installed(
        components, bundle, userdata, opts
    ):  # a port, the badges' art, or the library card: the gamelist is dressed again
        _refresh_badges(userdata, report)
    return report


def _theme_installed(
    components: Sequence[Component], bundle: Path, userdata: Path, opts: Options
) -> bool:
    """Whether the badges' art is in place: the bundle's theme is at its version.

    A port can be set up before the theme (the theme waits for the engine to stop); the theme's
    own setup then badges the games, so a refresh before that would only fail.
    """
    state = load_state(userdata)
    themes = [c for c in components if c.target == THEME]
    return all(state.get(t.name) == version_of(t, bundle, userdata, opts.es_dir) for t in themes)


def _refresh_badges(userdata: Path, report: Report) -> None:
    """A port came or went: re-badge the flippable games (AC-T15) and redraw the strip.

    A console with no N64 ROMs yet has nothing to badge, so that isn't a failure. The first time
    there's a list to dress, it also starts the console in the Showcase (DEC-7).
    """
    roms = userdata / ROMS
    if not roms.is_dir() or not any(p.suffix.lower() in ROM_SUFFIXES for p in roms.iterdir()):
        return
    _start_in_showcase(userdata, report)
    try:
        init(userdata / ROMS, userdata / BADGES_FROM, userdata / rc.PORTS)
    except (FlipError, ShowcaseError, OSError) as exc:
        report.failed["badges"] = str(exc)
    try:  # the list or its art may have changed: the strip under the cards follows (AC-T20)
        redraw(userdata)
    except (StripError, OSError) as exc:
        report.failed["strip"] = str(exc)


def engine_for_boot() -> Engine | None:
    """The engine, once its HTTP server answers; None if it never does (setup runs anyway)."""
    engine = Engine()
    if engine.wait_until_up(ENGINE_WAIT):
        return engine
    print(f"setup: the engine didn't answer in {ENGINE_WAIT} s; no progress on screen")
    return None


def _start_in_showcase(userdata: Path, report: Report) -> None:
    """Once per console, with the card installed: switch to the Showcase (DEC-7).

    The marker keeps it to once, so the owner's own choice with the card stands after that.
    """
    roms, marker = userdata / ROMS, userdata / SHOWCASE_STARTED
    if marker.exists() or not (roms / CARD).exists():
        return
    try:
        switch("showcase", roms / "gamelist.xml", roms)
    except ShowcaseError as exc:
        report.failed["showcase"] = str(exc)
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("The console started in the Showcase (DEC-7); the card decides now.\n")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: exit 0 if everything is set up, 1 if anything failed."""
    parser = argparse.ArgumentParser(description="Bring the console to the bundle's versions.")
    parser.add_argument("bundle", type=Path, help="the bundle folder (e.g. on the USB stick)")
    parser.add_argument("--userdata", type=Path, default=Path("/userdata"))
    parser.add_argument("--only", action="append", default=[], help="just this component")
    parser.add_argument("--force", action="store_true", help="redo even if at the version")
    parser.add_argument("--mirror", help="fetch downloads from here, by checksum")
    parser.add_argument(
        "--es-dir", type=Path, default=ES_DIR, help="the engine's own folder (es_systems.cfg)"
    )
    parser.add_argument("--phase", choices=PHASES, default="all", help="which components")
    parser.add_argument(
        "--pending", action="store_true", help="list what would be set up; change nothing"
    )
    parser.add_argument(
        "--engine",
        action="store_true",
        help="show progress on screen and reload the game lists after (the boot service)",
    )
    args = parser.parse_args(argv)
    engine = engine_for_boot() if args.engine and not args.pending else None
    options = Options(
        only=args.only,
        force=args.force,
        mirror=args.mirror,
        notify=engine.notify if engine else quiet,
        es_dir=args.es_dir,
        phase=args.phase,
    )
    try:
        if args.pending:
            print("\n".join(pending(args.bundle, args.userdata, options)))
            return 0
        report = run(args.bundle, args.userdata, options)
        if engine and report.applied:
            _finish_live(engine, args.bundle, args.userdata, options)
    except SetupError as exc:
        print(f"setup: {exc}", file=sys.stderr)
        return 1
    _print(report)
    return 1 if report.failed else 0


def _finish_live(engine: Engine, bundle: Path, userdata: Path, options: Options) -> None:
    """After the live phase: show the engine the change, or say a restart follows."""
    stopped = Options(only=options.only, es_dir=options.es_dir, phase="stopped")
    if options.phase == "live" and pending(bundle, userdata, stopped):
        engine.notify("N64 Mini: restarting to finish setting up")  # update.sh suspends it next
        return
    engine.notify("N64 Mini is up to date")
    engine.reload_games()


def _print(report: Report) -> None:
    for name in report.applied:
        print(f"applied  {name}")
    for name in report.skipped:
        print(f"skipped  {name}")
    for name, why in report.waiting.items():
        print(f"waiting  {name}: {why}")
    for name, why in report.failed.items():
        print(f"FAILED   {name}: {why}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
