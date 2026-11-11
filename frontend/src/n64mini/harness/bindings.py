"""The part of the engine's binding language the theme uses, for the preview.

A bound property (``{game:rom}`` and the like) is either substituted (text) or evaluated as an
expression (``BindingManager.cpp``): string literals, ``+``, ``==``/``!=``, the ``c ? a : b``
ternary, and the methods ``exists(path)`` and ``empty(value)`` (``THEMES_BINDINGS.md``). Only
that subset is modelled; anything else raises ``RenderError`` rather than being guessed.

Absolute device paths (``/userdata/...``) resolve under a sandbox folder standing in for the
console's disk, so ``exists()`` and bound image paths read real files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from n64mini.harness.values import RenderError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

type Value = str | bool

_TOKEN_RE: Final = re.compile(
    r'\s*(?:(?P<str>"[^"]*")|(?P<bind>\{game:\w+\})|(?P<op>==|!=|[+?:()])|(?P<name>[A-Za-z_]\w*))'
)
BINDING_RE: Final = re.compile(r"\{game:(\w+)\}")


def player_count(players: str) -> str:
    """``{game:playerCount}``: the highest number in "1-4" or "2+" (``FileData::getProperty``)."""
    if (split := players.rfind("+")) != -1:
        return players[:split]
    if (split := players.rfind("-")) != -1:
        return players[split + 1 :]
    return str(min(9, max(1, _to_integer(players))))


def _to_integer(text: str) -> int:
    """``StringUtil::toInteger``: optional sign, then digits up to a '.' or a space."""
    body = text.lstrip(" ")
    sign = -1 if body.startswith("-") else 1
    body = body.removeprefix("-").removeprefix("+")
    digits = re.match(r"[^. \r\n]*", body)
    run = digits.group(0) if digits else ""
    if run and not run.isdigit():
        raise RenderError(f"players={text!r} isn't a number the preview models")
    return sign * int(run) if run else 0


@dataclass(frozen=True, slots=True)
class Device:
    """The console's disk, as a sandbox folder: ``/userdata/x`` is ``root/userdata/x``."""

    root: Path

    def path(self, device_path: str) -> Path:
        """The sandbox file for an absolute device path."""
        if not device_path.startswith("/"):
            raise RenderError(f"{device_path!r} isn't an absolute device path")
        return self.root / device_path.lstrip("/")


class _Parser:
    """Recursive descent over: ternary > comparison > concatenation > atom."""

    def __init__(self, text: str, field: Callable[[str], str], device: Device) -> None:
        self.text = text
        self.field = field
        self.device = device
        self.tokens: list[tuple[str, str]] = []
        pos = 0
        while pos < len(text.rstrip()):
            m = _TOKEN_RE.match(text, pos)
            if m is None or m.end() == pos:
                raise RenderError(f"binding {text!r} isn't modelled (at {text[pos:]!r})")
            kind = m.lastgroup or ""
            self.tokens.append((kind, m.group(kind)))
            pos = m.end()
        self.i = 0

    def _peek(self) -> str:
        return self.tokens[self.i][1] if self.i < len(self.tokens) else ""

    def _take(self, expected: str | None = None) -> tuple[str, str]:
        if self.i >= len(self.tokens) or (expected and self.tokens[self.i][1] != expected):
            raise RenderError(f"binding {self.text!r} isn't modelled (expected {expected!r})")
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def parse(self) -> Value:
        value = self._ternary()
        if self.i != len(self.tokens):
            raise RenderError(f"binding {self.text!r} isn't modelled (trailing {self._peek()!r})")
        return value

    def _ternary(self) -> Value:
        cond = self._comparison()
        if self._peek() != "?":
            return cond
        self._take("?")
        yes = self._ternary()
        self._take(":")
        no = self._ternary()
        if not isinstance(cond, bool):
            raise RenderError(f"binding {self.text!r}: a condition must be true or false")
        return yes if cond else no

    def _comparison(self) -> Value:
        left = self._concat()
        if self._peek() not in {"==", "!="}:
            return left
        op = self._take()[1]
        right = self._concat()
        return (left == right) == (op == "==")

    def _concat(self) -> Value:
        value = self._atom()
        while self._peek() == "+":
            self._take("+")
            right = self._atom()
            if isinstance(value, bool) or isinstance(right, bool):
                raise RenderError(f"binding {self.text!r}: '+' joins text only")
            value += right
        return value

    def _atom(self) -> Value:
        kind, tok = self._take()
        if kind == "str":
            return tok[1:-1]
        if kind == "bind":
            m = BINDING_RE.fullmatch(tok)
            return self.field(m.group(1) if m else tok)
        if kind == "name" and tok in {"exists", "empty"}:
            self._take("(")
            arg = self._ternary()
            self._take(")")
            if isinstance(arg, bool):
                raise RenderError(f"binding {self.text!r}: {tok}() takes text")
            return self.device.path(arg).exists() if tok == "exists" else arg == ""
        raise RenderError(f"binding {self.text!r} isn't modelled ({tok!r})")


def evaluate(text: str, field: Callable[[str], str], device: Device) -> Value:
    """Evaluate a bound expression; ``field`` gives a ``{game:<field>}`` value."""
    return _Parser(text, field, device).parse()


def substitute(text: str, field: Callable[[str], str]) -> str:
    """Bound text: each ``{game:<field>}`` replaced by its value, nothing evaluated."""
    return BINDING_RE.sub(lambda m: field(m.group(1)), text)
