"""The running engine's HTTP API, on the console itself (``HttpServerThread.cpp``).

Shared by the flip, the Showcase/Full card, the launcher and setup. Runs on the console, so it
uses only the standard library.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import time
from http import HTTPStatus
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

SYSTEM: Final = "n64"
_ENGINE_HTTP: Final = ("127.0.0.1", 1234)


class EngineError(Exception):
    """The engine answered a request with an error."""


def game_id(path: str) -> str:
    """The engine's id for a game: the MD5 of its path (``HttpApi::getFileDataId``)."""
    return hashlib.md5(path.encode(), usedforsecurity=False).hexdigest()


class Engine:
    """The running engine's HTTP API, on the console itself."""

    def __init__(self, host: str = _ENGINE_HTTP[0], port: int = _ENGINE_HTTP[1]) -> None:
        """Talk to the engine at ``host:port``."""
        self.host, self.port = host, port

    def _request(
        self, method: str, url: str, body: bytes | None = None, content_type: str = "text/plain"
    ) -> int:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        try:
            headers = {"Content-Type": content_type} if body is not None else {}
            conn.request(method, url, body=body, headers=headers)
            response = conn.getresponse()
            status, detail = response.status, response.read()
        finally:
            conn.close()
        if status not in (HTTPStatus.OK, HTTPStatus.CREATED):
            text = detail.decode("utf-8", errors="replace").strip()
            raise EngineError(f"{method} {url}: {status} {text}")
        return status

    def game_running(self) -> bool:
        """Whether a game is running (``GET /runningGame``: 200 with its details, 201 if none)."""
        return self._request("GET", "/runningGame") == HTTPStatus.OK

    def notify(self, message: str) -> None:
        """Show ``message`` as the engine's on-screen notification."""
        self._request("POST", "/notify", message.encode())

    def reload_games(self) -> None:
        """Have the engine re-read its systems and game lists."""
        self._request("GET", "/reloadgames")

    def wait_until_up(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> bool:
        """Wait for the engine's HTTP server (at boot it starts in parallel with services)."""
        deadline = clock() + seconds
        while True:
            try:
                self.game_running()
            except (OSError, EngineError):
                if clock() >= deadline:
                    return False
                time.sleep(1)
            else:
                return True

    def set_media(self, path: str, field: str, png: bytes) -> None:
        """Upload ``png`` as the game's ``field`` image (a path field); the engine redraws it.

        A JSON metadata update can't do this: the engine skips path fields there
        (``HttpApi::ImportFromJson``), so the image goes to the media endpoint.
        """
        url = f"/systems/{SYSTEM}/games/{game_id(path)}/media/{field}"
        self._request("POST", url, png, "image/png")

    def set_badge(self, path: str, png: bytes) -> None:
        """Upload ``png`` as the game's ``cartridge`` image: the flip badge (AC-T15)."""
        self.set_media(path, "cartridge", png)

    def set_metadata(self, path: str, fields: Mapping[str, str]) -> None:
        """Set the game's text fields in the engine's memory (``HttpApi::ImportFromJson``).

        The engine answers 404 when nothing changed, as for an unknown game.
        """
        url = f"/systems/{SYSTEM}/games/{game_id(path)}"
        self._request("POST", url, json.dumps(dict(fields)).encode(), "application/json")
