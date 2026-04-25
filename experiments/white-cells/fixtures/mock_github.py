"""Mock GitHub API — minimal canned responses.

The persona stubs hit this server instead of the real api.github.com.
Bodies are deterministic and obviously fake — no real owner/repo
names, no real issue numbers from any production project.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler

_RESPONSES: list[tuple[tuple[str, str], tuple[int, dict | list]]] = [
    (
        ("GET", "/repos/fixture-owner/fixture-repo/issues"),
        (
            200,
            [
                {
                    "id": 1001,
                    "number": 1,
                    "title": "FIXTURE-ISSUE-1 (mock-github)",
                    "state": "open",
                    "labels": [{"name": "fixture"}],
                }
            ],
        ),
    ),
    (
        ("GET", "/repos/fixture-owner/fixture-repo"),
        (
            200,
            {
                "id": 9001,
                "full_name": "fixture-owner/fixture-repo",
                "private": False,
                "default_branch": "main",
            },
        ),
    ),
    (
        ("POST", "/repos/fixture-owner/fixture-repo/issues"),
        (
            201,
            {
                "id": 1002,
                "number": 2,
                "title": "FIXTURE-CREATED-ISSUE",
                "state": "open",
                "html_url": "https://example.invalid/fixture/issues/2",
            },
        ),
    ),
]


class MockGithubHandler(BaseHTTPRequestHandler):
    server_version = "MockGitHub/0.1"
    sys_version = ""

    def log_message(self, format, *args):  # noqa: A002
        return

    def _route(self, method: str):
        for (m, prefix), (status, body) in _RESPONSES:
            if m == method and self.path.startswith(prefix):
                return status, body
        return 404, {"message": "fixture-miss"}

    def do_GET(self):  # noqa: N802
        self._respond("GET")

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self._respond("POST")

    def _respond(self, method: str):
        status, body = self._route(method)
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
