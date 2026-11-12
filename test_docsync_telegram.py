import contextlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import docsync_purchase as purchase
import docsync_telegram as bridge


class TelegramBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base/'archive'
        self.intake = self.base/'inbox'; self.intake.mkdir()
        self.state = self.base/'state'
        self.source = self.intake/'ho so PO1 - 2026-11-01 - NCC Unknown.zip'
        self.source.write_bytes(b'zip bytes')
        self.config = {'login': 'owner', 'bot': 'bot'}
        self.calls = []
        self.status = 'pending'
        self.responses = {}
        now = time.time() + 30
        clock = patch.object(bridge.time, 'time', return_value=now)
        clock.start(); self.addCleanup(clock.stop)

    def remote(self, config, payload):
        self.calls.append(payload)
        return {'status': self.status, 'group': 'trong nuoc'}

    def run_sync(self):
        with contextlib.redirect_stdout(io.StringIO()):
            bridge.sync(self.root, [self.intake], self.state, self.config, self.remote)

    def test_deduplicated_notification_and_approved_move(self):
        self.run_sync(); self.run_sync()
        self.assertEqual(1, sum(c['op'] == 'submit' for c in self.calls))
        self.assertTrue(self.source.exists())
        self.status = 'approved'; self.run_sync()
        self.assertFalse(self.source.exists())
        self.assertEqual(b'zip bytes', (self.root/'2026/trong nuoc/11'/self.source.name).read_bytes())
        self.assertEqual('completed', json.loads((self.state/'purchase-telegram-state.json').read_text())[str(self.source)]['status'])
        self.run_sync()
        self.assertEqual(1, sum(c['op'] == 'submit' for c in self.calls))

    def test_changed_file_cannot_reuse_approval(self):
        self.run_sync()
        self.source.write_bytes(b'changed bytes')
        self.status = 'approved'; self.run_sync()
        self.assertTrue(self.source.exists())
        self.assertFalse(self.root.exists())
        self.assertEqual(2, sum(c['op'] == 'submit' for c in self.calls))

    def test_hold_survives_new_supplier_history_and_shell_route(self):
        self.run_sync(); self.status = 'held'; self.run_sync()
        history = self.root/'2026/trong nuoc/03/ho so PO2 - 2026-03-01 - NCC Unknown.zip'
        history.parent.mkdir(parents=True); history.write_bytes(b'history')
        with contextlib.redirect_stdout(io.StringIO()):
            purchase.route(self.root, self.state, True, [self.intake])
        self.assertTrue(self.source.exists())
        self.run_sync()
        self.assertTrue(self.source.exists())

    def test_send_failure_retries_same_proposal_id(self):
        def failed(config, payload):
            self.calls.append(payload)
            if payload['op'] == 'submit': raise RuntimeError('offline')
            return {'status': 'missing'}
        with self.assertRaises(RuntimeError):
            bridge.sync(self.root, [self.intake], self.state, self.config, failed)
        old = json.loads((self.state/'purchase-telegram-state.json').read_text())[str(self.source)]['id']
        self.run_sync()
        self.assertEqual(old, self.calls[-1]['id'])

    def test_destination_collision_does_not_send_group_question(self):
        history = self.root/'2026/trong nuoc/03/ho so PO2 - 2026-03-01 - NCC Unknown.zip'
        history.parent.mkdir(parents=True); history.write_bytes(b'history')
        dest = self.root/'2026/trong nuoc/11'/self.source.name
        dest.parent.mkdir(); dest.write_bytes(b'existing')
        self.run_sync()
        self.assertEqual([], self.calls)
        self.assertEqual(b'existing', dest.read_bytes())
        self.assertTrue(self.source.exists())


if __name__ == '__main__':
    unittest.main()
