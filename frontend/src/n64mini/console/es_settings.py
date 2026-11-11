"""The engine's settings for the console (DEC-6): our theme, N64 only, straight into its game list.

The engine keeps its settings in ``es_settings.cfg`` (``Settings.cpp``): one element per setting,
``<bool|int|float|string name=".." value=".." />`` under ``<config>``, as pugixml writes it. It
reads the file once at start-up, and writes back its whole in-memory set when a setting changes
in its menus. So setup merges our settings into the file (keeping anything else the owner has
set), then restarts the engine so it reads them.

"N64 only" means hiding every other system (``HiddenSystems``, ``;``-separated): stock Batocera
ships demo games for about fourteen. The list is every system the console's ``es_systems*.cfg``
files name, other than ours, so it follows the Batocera version actually installed.

Runs on the console, so it uses only the standard library.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

type Value = bool | int | str

HIDDEN: Final = "HiddenSystems"
BATOCERA_CONF: Final = "system/batocera.conf"
_ENTRY_RE: Final = re.compile(r'<(bool|int|float|string) name="([^"]*)" value="([^"]*)"\s*/>')
_SYSTEM_NAME_RE: Final = re.compile(r"<system>\s*.*?<name>\s*([^<\s]+)\s*</name>", re.DOTALL)
_ESCAPES: Final = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;"))


class SettingsError(Exception):
    """The settings file or the wanted settings can't be used."""


def _escape(text: str) -> str:
    for raw, escaped in _ESCAPES:
        text = text.replace(raw, escaped)
    return text


def _unescape(text: str) -> str:
    for raw, escaped in reversed(_ESCAPES):
        text = text.replace(escaped, raw)
    return text


def _typed(value: Value) -> tuple[str, str]:
    if isinstance(value, bool):  # before int: a bool is an int
        return "bool", "true" if value else "false"
    if isinstance(value, int):
        return "int", str(value)
    return "string", value


def read(path: Path) -> dict[str, tuple[str, str]]:
    """The file's settings, by name: (type, raw value). Empty if there's no file yet.

    Raises SettingsError if the file has content this reader doesn't understand, rather than
    dropping it when the file is written back.
    """
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    entries = {
        _unescape(m.group(2)): (m.group(1), _unescape(m.group(3))) for m in _ENTRY_RE.finditer(text)
    }
    leftover = _ENTRY_RE.sub("", text)
    leftover = re.sub(r"<\?xml[^>]*\?>|</?config>|<config\s*/>", "", leftover)
    if leftover.strip():
        raise SettingsError(f"{path}: unexpected content {leftover.strip()[:60]!r}")
    return entries


def render(entries: Mapping[str, tuple[str, str]]) -> str:
    """The settings file's text, in pugixml's layout."""
    lines = ['<?xml version="1.0"?>', "<config>"]
    lines += [
        f'\t<{kind} name="{_escape(name)}" value="{_escape(value)}" />'
        for name, (kind, value) in entries.items()
    ]
    return "\n".join([*lines, "</config>", ""])


def system_names(config_dirs: Iterable[Path]) -> list[str]:
    """Every system named in the ``es_systems*.cfg`` files of these folders (sorted, unique)."""
    names: set[str] = set()
    for folder in config_dirs:
        if folder.is_dir():
            for path in folder.glob("es_systems*.cfg"):
                names.update(_SYSTEM_NAME_RE.findall(path.read_text(encoding="utf-8")))
    return sorted(names)


def wanted(
    settings: Mapping[str, Value], only_system: str, config_dirs: Iterable[Path]
) -> dict[str, tuple[str, str]]:
    """Our settings, typed, with every system but ``only_system`` hidden."""
    if HIDDEN in settings:
        raise SettingsError(f"{HIDDEN} is computed from the console's systems; don't set it")
    names = system_names(config_dirs)
    if only_system not in names:
        raise SettingsError(f"no system named {only_system!r} on this console")
    entries = {name: _typed(value) for name, value in settings.items()}
    entries[HIDDEN] = ("string", ";".join(n for n in names if n != only_system))
    return entries


def version(entries: Mapping[str, tuple[str, str]]) -> str:
    """A version naming exactly these settings."""
    data = json.dumps(sorted(entries.items())).encode()
    return f"content:{hashlib.sha256(data).hexdigest()[:16]}"


def merge(path: Path, entries: Mapping[str, tuple[str, str]], dest: Path) -> None:
    """Write ``path``'s settings with ``entries`` set over them to ``dest``."""
    merged = read(path)
    merged.update(entries)
    dest.write_text(render(merged), encoding="utf-8")


def merge_conf(path: Path, entries: Mapping[str, str], dest: Path) -> None:
    """Write ``path`` (``batocera.conf``, ``key=value`` lines) with ``entries`` set over them."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for key, value in entries.items():
        pat = re.compile(rf"^\s*[#;]?\s*{re.escape(key)}\s*=")
        replaced = False
        for i, line in enumerate(lines):
            if pat.match(line):
                lines[i] = f"{key}={value}"
                replaced = True
        if not replaced:
            lines.append(f"{key}={value}")
    dest.write_text("\n".join([*lines, ""]), encoding="utf-8")
