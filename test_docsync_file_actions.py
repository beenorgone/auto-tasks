"""Cover versioned bulk actions, recoverable trash, and history receipts."""

from pathlib import Path
import tempfile
import unittest

from docsync_daily import file_token
from docsync_file_actions import dispatch
from docsync_history import render_today


class BulkActionsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'work'
        self.root.mkdir()
        self.state = Path(temporary.name) / 'state'
        self.key = b'fixture'

    def item(self, filename, new_name=None):
        path = self.root / filename
        return {'path': filename, 'token': file_token(self.key, filename, path.stat()), 'name': new_name}

    def run_action(self, action, *items, **fields):
        return dispatch(self.root, self.state, self.key, {'action': action, 'files': list(items), **fields})

    def test_rename_and_trash_are_visible_and_recoverable(self):
        for filename in ('first.txt', 'second.txt'):
            (self.root / filename).write_text(filename)
        result = self.run_action('rename', self.item('first.txt', 'first-new.txt'), self.item('second.txt', 'second-new.txt'))
        self.assertEqual(len(result['results']), 2)
        removed = self.run_action('trash', self.item('first-new.txt'), self.item('second-new.txt'))['results']
        self.assertEqual(len(removed), 2)
        self.assertTrue(all(not (self.root / item['path']).exists() for item in removed))
        self.assertEqual(['first.txt', 'second.txt'], [(self.root / item['destination']).read_text() for item in removed])
        markup = render_today(self.root, '__SUMMARY__ __ROWS__', state=self.state, key=self.key)
        self.assertIn('2 đã đổi tên', markup)
        self.assertIn('2 đã đưa vào thùng rác', markup)

    def test_preflight_blocks_entire_batch_on_stale_selection_or_collision(self):
        for filename in ('one.txt', 'two.txt', 'collision.txt'):
            (self.root / filename).write_text(filename)
        with self.assertRaises(FileExistsError):
            self.run_action('rename', self.item('one.txt', 'new.txt'), self.item('two.txt', 'collision.txt'))
        self.assertFalse((self.root / 'new.txt').exists())
        old = self.item('two.txt')
        (self.root / 'two.txt').write_text('updated file')
        with self.assertRaises(ValueError):
            self.run_action('trash', self.item('one.txt'), old)
        self.assertTrue((self.root / 'one.txt').is_file())

    def test_bulk_classify_uses_one_topic_destination(self):
        for filename in ('one.pdf', 'two.pdf'):
            (self.root / filename).write_text(filename)
        result = self.run_action('classify', self.item('one.pdf'), self.item('two.pdf'), topic='qc')
        self.assertEqual(['QC/one.pdf', 'QC/two.pdf'], [item['destination'] for item in result['results']])
