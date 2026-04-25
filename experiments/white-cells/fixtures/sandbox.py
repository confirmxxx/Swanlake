"""Fixture sandbox — spin up the three mock services on ephemeral ports.

The sandbox is a context manager. On enter it binds three localhost
HTTP servers (Notion / GitHub / Vercel mocks) on OS-assigned ports
and starts a daemon thread per server. On exit it shuts every server
down and joins its thread.

Persona stubs reach the sandbox via `sandbox.notion_url`,
`sandbox.github_url`, `sandbox.vercel_url`. These return absolute
http://127.0.0.1:<port> URLs.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from fixtures.mock_github import MockGithubHandler
from fixtures.mock_notion import MockNotionHandler
from fixtures.mock_vercel import MockVercelHandler


def _spawn(handler_cls: type[BaseHTTPRequestHandler]) -> tuple[HTTPServer, threading.Thread]:
    # Port 0 -> OS picks an unused ephemeral port.
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.05},
        daemon=True,
        name=f"fixture-{handler_cls.__name__}",
    )
    thread.start()
    return server, thread


class FixtureSandbox:
    """Context-managed three-server sandbox."""

    def __init__(self):
        self._servers: list[HTTPServer] = []
        self._threads: list[threading.Thread] = []
        self._notion: HTTPServer | None = None
        self._github: HTTPServer | None = None
        self._vercel: HTTPServer | None = None

    def __enter__(self) -> "FixtureSandbox":
        for handler, attr in (
            (MockNotionHandler, "_notion"),
            (MockGithubHandler, "_github"),
            (MockVercelHandler, "_vercel"),
        ):
            server, thread = _spawn(handler)
            setattr(self, attr, server)
            self._servers.append(server)
            self._threads.append(thread)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for server in self._servers:
            server.shutdown()
            server.server_close()
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._servers.clear()
        self._threads.clear()
        self._notion = self._github = self._vercel = None

    @staticmethod
    def _url(server: HTTPServer | None) -> str:
        if server is None:
            raise RuntimeError("FixtureSandbox accessed outside `with`")
        host, port = server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def notion_url(self) -> str:
        return self._url(self._notion)

    @property
    def github_url(self) -> str:
        return self._url(self._github)

    @property
    def vercel_url(self) -> str:
        return self._url(self._vercel)
