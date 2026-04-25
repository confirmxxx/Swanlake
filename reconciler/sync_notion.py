"""Notion sync engine.

Designed to be called from inside a Claude Routine (which has Notion API
access via the operator's Notion MCP). The Routine prompt should:
  1. Read canon/notion-template.md from the Swanlake repo
  2. Call sync_master_page(client, page_id, template_text)
  3. Record sync via status.write_sync_timestamp('notion')

The client interface is duck-typed: anything with `update_page(page_id, body)`
works. Real client comes from the Notion MCP at runtime; tests substitute
a MagicMock.
"""
from __future__ import annotations

from typing import Literal, Protocol


SyncResult = Literal['ok', 'rate-limited', 'auth-error', 'error']


class NotionRateLimited(Exception):
    """Raised when Notion API returns 429."""


class NotionAuthError(Exception):
    """Raised when Notion API returns 401/403."""


class NotionClient(Protocol):
    def update_page(self, page_id: str, body: str) -> dict: ...


def sync_master_page(
    client: NotionClient,
    page_id: str,
    template_text: str,
) -> SyncResult:
    """Push template_text into Notion page with given page_id.

    Returns a SyncResult string. Never raises — all failures map to a
    SyncResult so the calling Routine can branch on outcome without
    needing try/except.
    """
    try:
        client.update_page(page_id=page_id, body=template_text)
        return 'ok'
    except NotionRateLimited:
        return 'rate-limited'
    except NotionAuthError:
        return 'auth-error'
    except Exception:
        return 'error'
