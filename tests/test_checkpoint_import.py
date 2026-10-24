import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import persistence

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('checkpoint_import', REPO / 'checkpoint-import.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class ImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'checkout'
        self.root.mkdir()
        self.source = self.base / 'opaque'
        persistence.write_checkpoint(self.source, {'state/codex/auth.json': b'SYNTHETIC_PRIVATE_FIXTURE'})
        self.dest = self.root / persistence.DEFAULT_CHECKPOINT

    def test_exact_private_placement_and_idempotence_without_restoration(self):
        with patch.object(persistence, 'restore_checkpoint', side_effect=AssertionError('restore forbidden')):
            self.assertEqual(helper.import_checkpoint(self.source, self.root), 'imported')
            self.assertEqual(self.dest.read_bytes(), self.source.read_bytes())
            self.dest.chmod(0o644)
            self.assertEqual(helper.import_checkpoint(self.source, self.root), 'unchanged')
        self.assertEqual(self.dest.stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.root / 'state/codex').exists())
        self.assertEqual(list(self.dest.parent.glob('*import-*')), [])

    def test_different_existing_destination_preserved(self):
        self.dest.parent.mkdir()
        self.dest.write_bytes(b'keep existing opaque state')
        with self.assertRaises(persistence.CheckpointError):
            helper.import_checkpoint(self.source, self.root)
        self.assertEqual(self.dest.read_bytes(), b'keep existing opaque state')

    def test_missing_empty_corrupt_and_nonregular_source(self):
        for kind in ('missing', 'empty', 'corrupt', 'directory', 'fifo'):
            with self.subTest(kind=kind):
                path = self.base / kind
                if kind in ('empty', 'corrupt'):
                    path.write_bytes(b'' if kind == 'empty' else b'invalid')
                elif kind == 'directory':
                    path.mkdir()
                elif kind == 'fifo':
                    os.mkfifo(path)
                with self.assertRaises((OSError, persistence.CheckpointError)):
                    helper.import_checkpoint(path, self.root)
                self.assertFalse(self.dest.exists())

    def test_symlinks_and_hardlinked_destination_rejected(self):
        linked = self.base / 'linked'
        linked.symlink_to(self.source)
        with self.assertRaises(persistence.CheckpointError):
            helper.import_checkpoint(linked, self.root)
        (self.root / 'state').symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(persistence.CheckpointError):
            helper.import_checkpoint(self.source, self.root)
        (self.root / 'state').unlink()
        self.dest.parent.mkdir()
        self.dest.symlink_to(self.source)
        with self.assertRaises(persistence.CheckpointError):
            helper.import_checkpoint(self.source, self.root)
        self.dest.unlink()
        os.link(self.source, self.dest)
        with self.assertRaises(persistence.CheckpointError):
            helper.import_checkpoint(self.source, self.root)

    def test_concurrent_destination_is_not_clobbered(self):
        link = os.link
        def race(source, destination):
            Path(destination).write_bytes(b'concurrent state')
            link(source, destination)
        with patch.object(helper.os, 'link', side_effect=race):
            with self.assertRaises(persistence.CheckpointError):
                helper.import_checkpoint(self.source, self.root)
        self.assertEqual(self.dest.read_bytes(), b'concurrent state')

    def test_oversized_rejected_and_cli_output_secret_free(self):
        with patch.object(helper, 'MAX_BYTES', 1):
            with self.assertRaises(persistence.CheckpointError):
                helper.import_checkpoint(self.source, self.root)
        for valid in (True, False):
            if not valid:
                self.source.write_bytes(b'SYNTHETIC_PRIVATE_FIXTURE')
            result = subprocess.run([sys.executable, str(REPO / 'checkpoint-import.py'), str(self.source),
                                     '--root', str(self.root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0 if valid else 1)
            self.assertNotIn('SYNTHETIC_PRIVATE_FIXTURE', result.stdout + result.stderr)

    def test_decoded_validation_limit_rejects_compressed_expansion(self):
        # Exercise the optional bound without allocating a large fixture.
        with self.assertRaises(persistence.CheckpointError):
            persistence.read_checkpoint(self.source, max_decoded_bytes=8)
        self.assertFalse(self.dest.exists())

    def test_unsupported_entry_is_not_imported(self):
        persistence.write_checkpoint(self.source, {'../outside': b'fixture'})
        with self.assertRaises(persistence.CheckpointError):
            helper.import_checkpoint(self.source, self.root)
        self.assertFalse(self.dest.exists())

    def test_write_error_cleans_staging_file(self):
        with patch.object(helper.os, 'link', side_effect=OSError('fixture failure')):
            with self.assertRaises(OSError):
                helper.import_checkpoint(self.source, self.root)
        self.assertFalse(self.dest.exists())
        self.assertEqual(list(self.dest.parent.glob('*import-*')), [])
