from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]


class HelpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='setup-help-test-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / 'checkout with spaces'
        self.root.mkdir()
        for name in ('setup.sh', 'persistence.py', 'remote-orca.py', 'orca-window.py'):
            shutil.copyfile(REPO / name, self.root / name)

    def setup_command(self, *arguments):
        return subprocess.run(['bash', str(self.root / 'setup.sh'), *arguments],
                              cwd=self.directory, text=True, capture_output=True, stdin=subprocess.DEVNULL, start_new_session=True, timeout=10)

    def assert_read_only_help(self, arguments):
        before = {path.relative_to(self.root): path.read_bytes()
                  for path in self.root.rglob('*') if path.is_file()}
        result = self.setup_command(*arguments)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout)
        after = {path.relative_to(self.root): path.read_bytes()
                 for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(after, before)
        self.assertFalse((self.root / 'state/remote-orca').exists())

    def test_top_level_help_supports_both_flags_without_running_setup(self):
        for flag in ('-h', '--help'):
            with self.subTest(flag=flag):
                self.assert_read_only_help((flag,))

    def test_checkpoint_help_does_not_restore_or_save_files(self):
        for arguments in (('checkpoint', '--help'), ('checkpoint', 'save', '--help'),
                          ('checkpoint', 'restore', '-h')):
            with self.subTest(arguments=arguments):
                self.assert_read_only_help(arguments)
                self.assertFalse((self.root / 'state').exists())

    def test_remote_help_leaves_missing_and_existing_checkpoints_untouched(self):
        for existing in (False, True):
            if existing:
                checkpoint = self.root / 'state/.omniscient-dots-checkpoint'
                checkpoint.parent.mkdir()
                # Help must not even try to decode or refresh an existing file.
                checkpoint.write_bytes(b'checkpoint that help must not read')
            for arguments in (('remote', '--help'), ('remote', 'import', '--help'),
                              ('remote', 'pair', '-h'), ('remote', 'resume', '--help'),
                              ('remote', 'share', '-h')):
                with self.subTest(existing=existing, arguments=arguments):
                    self.assert_read_only_help(arguments)

    def test_desktop_help_does_not_connect_to_the_display_or_change_windows(self):
        for arguments in (('desktop', '--help'), ('desktop', 'fit', '--help'),
                          ('desktop', 'diagnose', '-h')):
            with self.subTest(arguments=arguments):
                self.assert_read_only_help(arguments)
                self.assertFalse((self.root / 'state').exists())

    def test_invalid_remote_syntax_does_not_save_a_checkpoint(self):
        for arguments in (('remote',), ('remote', 'pair'), ('remote', 'unknown'),
                          ('remote', 'import', '--port', 'not-a-port')):
            with self.subTest(arguments=arguments):
                result = self.setup_command(*arguments)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertFalse((self.root / 'state').exists())

    def test_setup_requires_a_profile_without_a_terminal(self):
        for arguments in ((), ('--profile',), ('--profile', 'unknown'),
                          ('--profile', 'worker', 'extra')):
            with self.subTest(arguments=arguments):
                result = self.setup_command(*arguments)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertFalse((self.root / 'state').exists())

    def test_removed_tailscale_import_options_do_not_change_state(self):
        previous = self.directory / 'previous tailnet state'
        previous.mkdir()
        identity = previous / 'tailscaled.state'
        identity.write_bytes(b'keep-this-device-identity')
        for arguments in (('--tailscale-state-from', str(previous)),
                          ('checkpoint', 'restore', '--skip-tailscale'),
                          ('checkpoint', 'save', '--skip-tailscale')):
            with self.subTest(arguments=arguments):
                result = self.setup_command(*arguments)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertFalse((self.root / 'state').exists())
                self.assertFalse((self.root / 'bin').exists())
                self.assertFalse(previous.is_symlink())
                self.assertEqual(identity.read_bytes(), b'keep-this-device-identity')


if __name__ == '__main__':
    unittest.main()
