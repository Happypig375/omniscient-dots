import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('orca_locks', REPO / 'orca-locks.py')
orca_locks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orca_locks)


class OrcaLockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='orca-lock-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.proc = self.root / 'proc'
        self.proc.mkdir()
        self.profile = self.root / 'profile'
        self.profile.mkdir()

    def write(self, name, data=b'preserved'):
        path = self.profile / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def process(self, executable, *arguments, command=None, pid=123):
        process = self.proc / str(pid)
        process.mkdir()
        (process / 'exe').symlink_to(executable)
        (process / 'comm').write_text(command or Path(executable).name)
        (process / 'cmdline').write_bytes(b'\0'.join(arg.encode() for arg in (executable, *arguments)) + b'\0')
        return process

    def test_stale_locks_are_removed_while_saved_state_is_preserved(self):
        locks = [
            self.write('orcad.lock'),
            self.write('Local Storage/leveldb/LOCK'),
            self.write('Partitions/orca-browser/Session Storage/LOCK'),
            self.write('.profile-state-access/participants/old/old.owner'),
            self.write('.profile-state-access/candidates/old/old.owner'),
            self.write('.profile-state-access/maintenance/old.owner'),
            self.write('profile-state.lock/owner'),
        ]
        saved = [self.write(name) for name in (
            'orca-environments.json', 'profiles/local-default/profile-state.db',
            'profiles/local-default/profile-state.db-wal', 'profiles/local-default/profile-state.db-shm',
        )]
        external = self.root / 'external'
        external.write_bytes(b'keep external target')
        for name in orca_locks.SINGLETON_FILES:
            path = self.profile / name
            path.symlink_to(external)
            locks.append(path)

        self.assertGreater(orca_locks.clear_stale_locks(self.profile, proc=self.proc), 0)
        for path in locks:
            self.assertFalse(path.exists(), path)
            self.assertFalse(path.is_symlink(), path)
        for path in saved:
            self.assertEqual(path.read_bytes(), b'preserved')
        self.assertEqual(external.read_bytes(), b'keep external target')
        self.assertEqual(orca_locks.clear_stale_locks(self.profile, proc=self.proc), 0)

    def test_any_running_orca_keeps_locks_including_other_profiles(self):
        for executable, arguments in (
            ('/another-profile/orca', ('--user-data-dir=/other/profile',)),
            ('/usr/bin/node', ('/other/orcad-artifacts/linux-x64/current/server.js',)),
            ('/usr/bin/node', ('/another/orca/out/main/daemon-entry.js',)),
            ('/usr/bin/node', ('/other/cli/orcad.js',)),
            ('/tmp/orca (deleted)', ()),
        ):
            with self.subTest(executable=executable, arguments=arguments):
                process = self.process(executable, *arguments)
                lock = self.write('orcad.lock')
                self.assertEqual(orca_locks.clear_stale_locks(self.profile, proc=self.proc), 0)
                self.assertTrue(lock.exists())
                for path in process.iterdir():
                    path.unlink()
                process.rmdir()

    def test_unrelated_and_exited_processes_allow_cleanup(self):
        self.process('/usr/bin/python3', '/some/other/script.py')
        exited = self.process('/tmp/orca', pid=456)
        (exited / 'cmdline').write_bytes(b'')
        lock = self.write('orcad.lock')
        self.assertEqual(orca_locks.clear_stale_locks(self.profile, proc=self.proc), 1)
        self.assertFalse(lock.exists())

    def test_launcher_shell_does_not_block_its_own_cleanup(self):
        self.process('/usr/bin/bash', '/other/bin/orca', command='orca')
        self.process('/usr/bin/bash', '/other/bin/orca-desktop', command='orca-desktop', pid=456)
        lock = self.write('orcad.lock')
        self.assertEqual(orca_locks.clear_stale_locks(self.profile, proc=self.proc), 1)
        self.assertFalse(lock.exists())

    def test_unreadable_process_keeps_locks(self):
        self.process('/usr/bin/node', '/some/script.js')
        lock = self.write('orcad.lock')
        with patch.object(orca_locks.os, 'readlink', side_effect=PermissionError):
            self.assertEqual(orca_locks.clear_stale_locks(self.profile, proc=self.proc), 0)
        self.assertTrue(lock.exists())

    def test_cleanup_does_not_follow_directories_outside_the_profile(self):
        outside = self.root / 'outside'
        outside.mkdir()
        external_lock = outside / 'LOCK'
        external_lock.write_bytes(b'external lock')
        (self.profile / 'linked-data').symlink_to(outside, target_is_directory=True)
        (self.profile / '.profile-state-access').symlink_to(outside, target_is_directory=True)
        self.assertEqual(orca_locks.clear_stale_locks(self.profile, proc=self.proc), 1)
        self.assertEqual(external_lock.read_bytes(), b'external lock')
        self.assertTrue((self.profile / 'linked-data').is_symlink())


if __name__ == '__main__':
    unittest.main()
