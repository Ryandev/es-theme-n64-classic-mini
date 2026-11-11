"""Theme property values as the preview reads them, and the error it raises instead of guessing."""

from __future__ import annotations

from typing import Final

type RGBA = tuple[int, int, int, int]

_RECT_ARITIES: Final = frozenset({1, 2, 4})


class RenderError(ValueError):
    """The theme cannot be rendered faithfully.

    Raised instead of guessing. A preview that silently drops an element, or
    substitutes a default for garbage, still produces a picture and a score, so
    the defect would read as green. An *absent* property is different: the
    engine has a real default for it, and the renderer uses that.
    """


def _floats(value: str, what: str) -> list[float]:
    try:
        return [float(p) for p in value.split()]
    except ValueError:
        raise RenderError(f"not a number {what}: {value!r}") from None


def parse_pair(value: str | None, default: tuple[float, float]) -> tuple[float, float]:
    """Parse an ES ``'x y'`` pair; a single value applies to both axes.

    ``default`` is used only when the property is absent (the engine's default).
    """
    if not value:
        return default
    floats = _floats(value, "pair")
    if len(floats) == 1:
        return (floats[0], floats[0])
    if len(floats) == 2:
        return (floats[0], floats[1])
    raise RenderError(f"expected 1 or 2 numbers, got {len(floats)}: {value!r}")


def parse_rect(
    value: str | None, default: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    """Parse a ``NORMALIZED_RECT`` the way ``Vector4f::parseString`` does.

    One value fills all four, two are ``x y`` repeated, four are taken as given. The
    engine turns any other count into zeros; that's never what a theme meant, so it raises.
    """
    if not value:
        return default
    f = _floats(value, "rect")
    if len(f) not in _RECT_ARITIES:
        raise RenderError(f"expected 1, 2 or 4 numbers, got {len(f)}: {value!r}")
    if len(f) == 1:
        return (f[0], f[0], f[0], f[0])
    if len(f) == 2:
        return (f[0], f[1], f[0], f[1])
    return (f[0], f[1], f[2], f[3])


def parse_hex_color(value: str | None, default: RGBA = (255, 255, 255, 255)) -> RGBA:
    """Parse ``RRGGBB`` / ``RRGGBBAA`` (optional ``#``) into RGBA; ``default`` if absent."""
    s = (value or "").strip().lstrip("#")
    if not s:
        return default
    if len(s) not in {6, 8} or not all(c in "0123456789abcdefABCDEF" for c in s):
        raise RenderError(f"not a hex colour: {value!r}")
    alpha = int(s[6:8], 16) if len(s) == 8 else 255
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), alpha)
