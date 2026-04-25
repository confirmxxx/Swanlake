"""Mock Notion API — minimal canned responses.

The persona stubs hit this server instead of the real notion.com.
Bodies are deterministic and obviously fake — no real-shaped canary
literals, no real production page IDs, no real workspace names.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler

# Canned response table. Keys are (method, path-prefix) tuples; the value
# is a (status, body-dict) pair. The server matches the first key whose
# prefix matches the request path.
_RESPONSES: list[tuple[tuple[str, str], tuple[int, dict]]] = [
    (
        ("GET", "/v1/users/me"),
        (
            200,
            {
                "object": "user",
                "id": "fake-notion-user-0000-0000-0000-000000000000",
                "type": "bot",
                "bot": {"workspace_name": "white-cells-fixture"},
            },
        ),
    ),
    (
        ("POST", "/v1/search"),
        (
            200,
            {
                "object": "list",
                "results": [
                    {
                        "object": "page",
                        "id": "fake-page-id-1111-1111-1111-111111111111",
                        "title": "FIXTURE-PAGE-1 (mock-notion)",
                    }
                ],
                "has_more": False,
            },
        ),
    ),
    (
        ("GET", "/v1/pages/"),
        (
            200,
            {
                "object": "page",
                "id": "fake-page-id-2222-2222-2222-222222222222",
                "properties": {"title": {"title": [{"plain_text": "FIXTURE-PAGE-2"}]}},
            },
        ),
    ),
]


class MockNotionHandler(BaseHTTPRequestHandler):
    server_version = "MockNotion/0.1"
    sys_version = ""

    # Silence default logging — the test runner does not need request lines.
    def log_message(self, format, *args):  # noqa: A002
        return

    def _route(self, method: str):
        for (m, prefix), (status, body) in _RESPONSES:
            if m == method and self.path.startswith(prefix):
                return status, body
        return 404, {"object": "error", "code": "not_found", "message": "fixture-miss"}

    def do_GET(self):  # noqa: N802
        self._respond("GET")

    def do_POST(self):  # noqa: N802
        # Drain request body to keep clients happy; we do not inspect it.
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
