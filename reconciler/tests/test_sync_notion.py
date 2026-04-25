"""Tests for the Notion sync engine — Notion API mocked."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reconciler import sync_notion


class NotionSyncTest(unittest.TestCase):
    def test_writes_template_to_master_page(self):
        mock_client = MagicMock()
        mock_client.update_page.return_value = {'ok': True}
        result = sync_notion.sync_master_page(
            mock_client,
            page_id='abc123',
            template_text='# new content\n',
        )
        self.assertEqual(result, 'ok')
        mock_client.update_page.assert_called_once()

    def test_handles_rate_limit(self):
        mock_client = MagicMock()
        mock_client.update_page.side_effect = sync_notion.NotionRateLimited('retry after 60s')
        result = sync_notion.sync_master_page(
            mock_client, page_id='abc123', template_text='# x\n',
        )
        self.assertEqual(result, 'rate-limited')

    def test_handles_auth_failure(self):
        mock_client = MagicMock()
        mock_client.update_page.side_effect = sync_notion.NotionAuthError('401')
        result = sync_notion.sync_master_page(
            mock_client, page_id='abc123', template_text='# x\n',
        )
        self.assertEqual(result, 'auth-error')

    def test_handles_unexpected_exception(self):
        mock_client = MagicMock()
        mock_client.update_page.side_effect = RuntimeError('network broken')
        result = sync_notion.sync_master_page(
            mock_client, page_id='abc123', template_text='# x\n',
        )
        self.assertEqual(result, 'error')

    def test_passes_page_id_and_body_to_client(self):
        """Verify the client gets called with the right arguments."""
        mock_client = MagicMock()
        mock_client.update_page.return_value = {'ok': True}
        sync_notion.sync_master_page(
            mock_client, page_id='page-xyz', template_text='# template\n',
        )
        mock_client.update_page.assert_called_once_with(
            page_id='page-xyz', body='# template\n',
        )


if __name__ == '__main__':
    unittest.main()
