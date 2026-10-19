from pathlib import Path
import importlib.util
import json
import os
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('reuse', Path(__file__).resolve().parents[1] / 'tailscale-reuse.py')
reuse = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reuse)


class MigrationTests(unittest.TestCase):
    def test_flags_support_both_spellings(self):
        self.assertEqual(reuse.option(['--state=/a'], 'state'), '/a')
        self.assertEqual(reuse.option(['-socket', '/b'], 'socket'), '/b')

    def test_handover_copies_identity_and_preserves_both_old_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / 'new'
            local = root / 'bin/tailscale-sandbox-state'
            local.mkdir(parents=True)
            (root / 'state').mkdir()
            (local / 'tailscaled.state').write_text('old-local')
            original = base / 'original'
            original.mkdir()
            identity = original / 'tailscaled.state'
            identity.write_text('migrated-device')
            (original / 'localapi.json').write_text('stale-endpoint')
            pid = os.getpid()
            started = Path(f'/proc/{pid}/stat').read_text().rpartition(')')[2].split()[19]
            endpoint = original / 'localapi.json'
            with patch.object(reuse, 'discover', return_value=[(pid, started, endpoint, identity)]), \
                    patch.object(reuse.subprocess, 'run') as run, \
                    patch.object(reuse.os, 'pidfd_open', return_value=100), \
                    patch.object(reuse.signal, 'pidfd_send_signal') as stop, \
                    patch('select.select', return_value=([100], [], [])), \
                    patch.object(reuse.os, 'close'):
                run.return_value.stdout = json.dumps({'BackendState': 'Running'})
                self.assertTrue(reuse.reuse(root))
                stop.assert_called_once()
            self.assertEqual((local / 'tailscaled.state').read_text(), 'migrated-device')
            self.assertFalse((local / 'localapi.json').exists())
            self.assertEqual(identity.read_text(), 'migrated-device')
            backups = list((root / 'state').glob('tailscale-state-before-migration-*/tailscale-sandbox-state/tailscaled.state'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), 'old-local')
