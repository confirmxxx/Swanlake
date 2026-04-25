"""Mock Vercel API — minimal canned responses.

The persona stubs hit this server instead of the real api.vercel.com.
Bodies are deterministic and obviously fake — no real project IDs,
no real deployment URLs.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler

_RESPONSES: list[tuple[tuple[str, str], tuple[int, dict]]] = [
    (
        ("GET", "/v9/projects/fixture-project-id"),
        (
            200,
            {
                "id": "fixture-project-id",
                "name": "white-cells-fixture-project",
                "framework": None,
                "createdAt": 0,
            },
        ),
    ),
    (
        ("GET", "/v6/deployments"),
        (
            200,
            {
                "deployments": [
                    {
                        "uid": "fixture-deployment-uid-0000",
                        "name": "white-cells-fixture-project",
                        "url": "fixture.example.invalid",
                        "state": "READY",
                    }
                ]
            },
        ),
    ),
]


class MockVercelHandler(BaseHTTPRequestHandler):
    server_version = "MockVercel/0.1"
    sys_version = ""

    def log_message(self, format, *args):  # noqa: A002
        return

    def _route(self, method: str):
        for (m, prefix), (status, body) in _RESPONSES:
            if m == method and self.path.startswith(prefix):
                return status, body
        return 404, {"error": {"code": "not_found", "message": "fixture-miss"}}

    def do_GET(self):  # noqa: N802
        self._respond("GET")

    def _respond(self, method: str):
        status, body = self._route(method)
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
