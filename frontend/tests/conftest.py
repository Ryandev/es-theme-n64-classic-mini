from __future__ import annotations

import contextlib
import os
import signal
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

import pytest

from n64mini.paths import THEME_FILE

N64_THEME = THEME_FILE

type ThemeWriter = Callable[[Mapping[str, str | bytes]], Path]


def wrap(body: str, *, version: str = "7") -> str:
    """Add the ``<theme>`` / ``<formatVersion>`` boilerplate around ``body``."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<theme>\n  <formatVersion>{version}</formatVersion>\n{body}\n</theme>\n"
    )


@pytest.fixture
def write_theme(tmp_path: Path) -> ThemeWriter:
    """Write ``files`` (relative path -> text or bytes) and return theme.xml's path."""

    def _write(files: Mapping[str, str | bytes]) -> Path:
        for name, content in files.items():
            target = tmp_path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding="utf-8")
        return tmp_path / "theme.xml"

    return _write


def processes_under(root: Path) -> dict[int, str]:
    """Live processes whose command line mentions ``root`` (pytest's temp folder)."""
    found: dict[int, str] = {}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            cmd = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue  # it exited while we looked
        if str(root) in cmd:
            found[int(proc.name)] = cmd.strip()
    return found


@pytest.fixture(scope="session", autouse=True)
def _no_leftover_processes(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Fail the run if a test leaves a process behind (a fake hotkeygen once looped for 19 h).

    Anything still running from pytest's temp folder a few seconds after the last test is killed
    and reported, so a leak fails the gate instead of heating the owner's laptop overnight.
    """
    root = tmp_path_factory.getbasetemp()
    yield
    deadline = time.monotonic() + 3.0
    left = processes_under(root)
    while left and time.monotonic() < deadline:
        time.sleep(0.1)
        left = processes_under(root)
    for pid in left:
        # the whole process group, so a child it started (`sleep`) goes too
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        with contextlib.suppress(ProcessLookupError):  # it exited on its own meanwhile
            os.kill(pid, signal.SIGKILL)
    assert not left, f"tests left processes running: {left}"
