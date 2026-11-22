"""Keep classification history tied to recorded events for the local day."""

import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

import docsync_history as history


class HistoryTests(unittest.TestCase):
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
