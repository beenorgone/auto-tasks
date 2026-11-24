"""Settings stay consistent between the app, watcher paths and shell organizer."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from docsync_paths import save_workspace_paths, workspace_config, workspace_paths
from docsync_purchase import intake_paths, purchase_root


class FolderSettingsTests(unittest.TestCase):
    def test_shell_and_purchase_paths_share_settings_and_allow_empty_scan_slots(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            work = home / "WIP - hồ sơ 'mới'"
            accounting = home / 'Kho $(echo forbidden)'
            source = home / 'Thư mục quét'
            for directory in (work, accounting, source):
                directory.mkdir()
            with patch.dict(os.environ, {'HOME': temporary, 'XDG_CONFIG_HOME': str(home / '.config')}, clear=True):
                selected = {
                    'DOCSYNC_ROOT': str(work),
                    'IVAR_DATA': str(accounting),
                    'DOCSYNC_DOCUMENTS_SCAN': str(source),
                    'DOCSYNC_DOWNLOADS_SCAN': '',
                }
                save_workspace_paths(selected)
                self.assertEqual(selected, workspace_paths())
                self.assertEqual(0o600, workspace_config().stat().st_mode & 0o777)
                self.assertIn(source, intake_paths())
                self.assertNotIn(Path.cwd(), intake_paths())
                self.assertEqual(accounting / 'ho so mua vao', purchase_root())
                config_script = Path(__file__).with_name('docsync_paths.py')
                shell = '''config=$(/usr/bin/python3 "$1") || exit 1
eval "$config"
printf '%s\n' "$DOCSYNC_ROOT" "$IVAR_DATA" "$DOCSYNC_DOCUMENTS_SCAN" "$DOCSYNC_DOWNLOADS_SCAN"'''
                output = subprocess.check_output(['/bin/sh', '-c', shell, 'sh', config_script], text=True)
                self.assertEqual(list(selected.values()), output.splitlines())
                with self.assertRaisesRegex(ValueError, 'thư mục đích'):
                    save_workspace_paths({**selected, 'DOCSYNC_DOCUMENTS_SCAN': str(accounting)})
                self.assertEqual(selected, workspace_paths())
