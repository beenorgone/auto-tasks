"""Keep classification history tied to recorded events for the local day."""

import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import docsync_history as history
import docsync_topics as topics


class HistoryTests(unittest.TestCase):
    def pending_file(self, temporary):
        root = Path(temporary) / 'wip'
        root.mkdir()
        state = Path(temporary) / 'state'
        (state / 'logs').mkdir(parents=True)
        file = root / 'tài liệu.pdf'
        file.write_bytes(b'original')
        day = dt.date.today()
        (state / 'logs' / f'{day}.log').write_text(
            f'{day} 00:00:00 [INFO] REVIEW: {file} [needs_review\t0\t\treview_only]\n', encoding='utf-8')
        key = b'fixture-key'
        payload = {'path': file.name, 'token': history.file_token(key, str(file), file.stat()), 'topic': 'qc'}
        return root, state, file, key, payload

    def test_web_hold_and_move_update_history_and_reuse_telegram_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, state, source, key, payload = self.pending_file(temporary)
            self.assertIn('classify-file', history.render_today(root, '__ROWS__', state=state, key=key))
            self.assertEqual('', history.decide(root, state, key, {**payload, 'topic': topics.HOLD})['destination'])
            report = topics.read_records(state)[source.name]
            self.assertTrue(topics.is_held_file(root, state, source, report['sha256']))
            self.assertIn('1 giữ lại', history.render_today(root, '__SUMMARY__', state=state))
            self.assertEqual('QC/tài liệu.pdf', history.decide(root, state, key, payload)['destination'])
            self.assertFalse(source.exists())
            self.assertEqual(b'original', (root / 'QC' / source.name).read_bytes())
            markup = history.render_today(root, '__ROWS__', state=state, key=key)
            self.assertIn('QC/tài liệu.pdf', markup)
            self.assertNotIn('classify-file', markup)
            self.assertTrue(topics.read_records(state)[source.name]['notified'])

    def test_changed_files_collisions_and_automatic_reports_do_not_move(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, state, source, key, payload = self.pending_file(temporary)
            with patch.object(history, 'automatic_state', return_value=True):
                with self.assertRaisesRegex(ValueError, 'EIM tự động'):
                    history.decide(root, state, key, payload)
            destination = root / 'QC' / source.name
            destination.parent.mkdir()
            destination.write_bytes(b'existing')
            with self.assertRaises(FileExistsError):
                history.decide(root, state, key, payload)
            source.write_bytes(b'changed version')
            with self.assertRaisesRegex(ValueError, 'đã thay đổi'):
                history.decide(root, state, key, payload)
            self.assertEqual(b'changed version', source.read_bytes())
            self.assertEqual(b'existing', destination.read_bytes())

    def test_today_uses_latest_event_and_escapes_file_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'logs').mkdir()
            day = dt.date(2026, 11, 22)
            source = f'{root}/item<script>.pdf'
            destination = f'{root}/Giao vận/GHTK/item<script>.pdf'
            (root / 'logs/2026-11-22.log').write_text(
                f'2026-11-22 09:02:00 [INFO] REVIEW: {source} [ghtk_reports\t0\t\treview_only]\n'
                f'2026-11-22 09:03:00 [INFO] Managed route: {source} -> {destination} [category=ghtk_reports]\n'
                f'2026-11-22 09:04:00 [INFO] PLAN managed-route: {source} -> /preview [category=ghtk_reports]\n'
                '2026-11-22 09:05:00 [INFO] REVIEW: /incoming/uncertain.pdf [needs_review\t0\t\treview_only]\n'
                '2026-11-21 23:59:00 [INFO] REVIEW: /incoming/yesterday.pdf [needs_review]\n', encoding='utf-8')
            at = dt.datetime.combine(day, dt.time(9, 10)).astimezone().isoformat()
            purchase = {'source':'/incoming/purchase.zip','destination':'/archive/purchase.zip','time':at}
            with (root / 'purchase-months.jsonl').open('w', encoding='utf-8') as stream:
                for status in ('planned', 'completed'):
                    stream.write(json.dumps(dict(purchase, status=status)) + '\n')
            markup = history.render_today(root, '__DAY__ __SUMMARY__ __ROWS__', day=day, state=root)
            self.assertIn('22/11/2026', markup)
            self.assertIn('2 đã chuyển · 1 chờ duyệt', markup)
            self.assertIn('Giao vận/GHTK/item&lt;script&gt;.pdf', markup)
            self.assertNotIn('<script>', markup)
            self.assertNotIn('yesterday.pdf', markup)
            self.assertNotIn('/preview', markup)


if __name__ == '__main__':
    unittest.main()
