import base64
import fcntl
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import authentication as auth


REPO = Path(__file__).resolve().parents[1]
CODEX = 'state/codex/auth.json'
GH = 'state/gh/hosts.yml'
ENVIRONMENTS = f'{auth.ORCA_PROFILE}/orca-environments.json'
ROUTE = 'state/remote-orca/example.route.json'


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='authentication-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def setup_command(self, *arguments):
        for name in ('setup.sh', 'authentication.py'):
            shutil.copyfile(REPO / name, self.root / name)
        return subprocess.run(['bash', str(self.root / 'setup.sh'), *arguments],
                              cwd=self.root, text=True, capture_output=True, timeout=15)

    def setup_until_credentials(self, after_credentials, *arguments):
        # Execute the real setup startup, restore step and EXIT trap. Stop before
        # it touches ~/.bashrc, browser trust, networking, or application daemons.
        source = (REPO / 'setup.sh').read_text().split('stage bash-startup\n', 1)[0]
        self.write('setup.sh', (source + after_credentials).encode())
        shutil.copyfile(REPO / 'authentication.py', self.root / 'authentication.py')
        self.write('remote-orca.py', b'# fixture\n')
        for name in ('gh', 'git', 'ssh', 'curl', 'tar', 'certutil', 'openssl', 'orca', 'orca-desktop'):
            path = self.write('bin/' + name, b'#!/bin/sh\nexit 0\n')
            path.chmod(0o700)
        return subprocess.run(['bash', str(self.root / 'setup.sh'), *arguments],
                              cwd=self.root, text=True, capture_output=True, timeout=15)

    def test_round_trip_authentication_excludes_runtime_and_history(self):
        original = {
            GH: b'github.com:\n  oauth_token: example-github-token\n',
            CODEX: b'{"tokens":{"access_token":"access","refresh_token":"refresh"}}\n',
            auth.TAILSCALE_STATE: b'{"_machinekey":"private-device-identity"}',
            ENVIRONMENTS: b'{"version":1,"environments":[{"deviceToken":"pairing-token"}]}',
            f'{auth.ORCA_PROFILE}/orca-e2ee-keypair.json': b'{"secretKeyB64":"key"}',
            f'{auth.ORCA_PROFILE}/orca-environment-sidecar.json': b'{"version":1,"entries":{}}',
        }
        for name, data in original.items():
            self.write(name, data)
        self.write(ROUTE, b'{"remote_host":"100.64.0.2","remote_port":6768,"local_port":12345,"pid":9876}')
        for name in ('state/codex/history.jsonl', 'bin/tailscale-sandbox-state/localapi.json',
                     f'{auth.ORCA_PROFILE}/.profile-state-access/participants/old.owner',
                     f'{auth.ORCA_PROFILE}/profiles/local-default/profile-state.db'):
            self.write(name, b'not authentication')
        archive, count = auth.save_authentication(self.root)
        self.assertEqual(count, len(original) + 1)
        self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)
        entries = auth.read_archive(archive)
        self.assertEqual(set(entries), {*original, ROUTE})
        self.assertEqual(json.loads(entries[ROUTE]), {'remote_host': '100.64.0.2', 'remote_port': 6768, 'local_port': 12345})
        for name in entries:
            (self.root / name).unlink()
        _, restored = auth.restore_authentication(self.root)
        self.assertEqual(restored, count)
        for name, data in original.items():
            self.assertEqual((self.root / name).read_bytes(), data)
            self.assertEqual(stat.S_IMODE((self.root / name).stat().st_mode), 0o600)

    def test_restore_keeps_newer_tokens_then_save_updates_backup(self):
        path = self.write(CODEX, b'{"refresh_token":"older"}')
        archive, _ = auth.save_authentication(self.root)
        newer = b'{"refresh_token":"newer"}'
        path.write_bytes(newer)
        _, restored = auth.restore_authentication(self.root)
        self.assertEqual(restored, 0)
        self.assertEqual(path.read_bytes(), newer)
        auth.save_authentication(self.root)
        self.assertEqual(auth.read_archive(archive)[CODEX], newer)

    def test_save_retains_missing_credentials_during_partial_setup(self):
        path = self.write(CODEX, b'codex-authorized')
        archive, _ = auth.save_authentication(self.root)
        path.unlink()
        self.write(GH, b'github-authorized')
        auth.save_authentication(self.root)
        self.assertEqual(auth.read_archive(archive), {CODEX: b'codex-authorized', GH: b'github-authorized'})

    def test_invalid_archives_restore_nothing_and_do_not_leak_contents(self):
        valid = base64.b64encode(b'secret-codex-token').decode()
        invalid = [
            {'version': 2, 'files': {CODEX: valid}},
            {'version': 1, 'files': {CODEX: valid, '../escaped': valid}},
            {'version': 1, 'files': {CODEX: valid, GH: 'PRIVATE_INVALID_BASE64!'}},
        ]
        for value in invalid:
            with self.subTest(value=value['version']):
                archive = self.write('state/authentication.json', json.dumps(value).encode())
                result = self.setup_command('auth', 'restore')
                self.assertEqual(result.returncode, 1)
                self.assertNotIn('PRIVATE_INVALID_BASE64', result.stderr)
                self.assertNotIn('secret-codex-token', result.stderr)
                self.assertFalse((self.root / CODEX).exists())
                self.assertEqual(json.loads(archive.read_bytes()), value)

    def test_symlinks_are_not_read_or_replaced(self):
        external = self.write('outside', b'keep external token')
        credential = self.root / CODEX
        credential.parent.mkdir(parents=True)
        credential.symlink_to(external)
        with self.assertRaises(auth.AuthenticationError):
            auth.save_authentication(self.root)
        credential.unlink()
        credential.write_bytes(b'backed-up token')
        auth.save_authentication(self.root)
        credential.unlink()
        credential.symlink_to(external)
        with self.assertRaises(auth.AuthenticationError):
            auth.restore_authentication(self.root)
        self.assertTrue(credential.is_symlink())
        self.assertEqual(external.read_bytes(), b'keep external token')

    def test_symlinked_parent_does_not_receive_credentials(self):
        self.write(CODEX, b'token')
        auth.save_authentication(self.root)
        shutil.rmtree(self.root / 'state/codex')
        outside = self.root / 'outside'
        outside.mkdir()
        (self.root / 'state/codex').symlink_to(outside)
        with self.assertRaises(auth.AuthenticationError):
            auth.restore_authentication(self.root)
        self.assertFalse((outside / 'auth.json').exists())

    def test_tailscale_running_lock_prevents_identity_restore(self):
        path = self.write(auth.TAILSCALE_STATE, b'device identity')
        auth.save_authentication(self.root)
        path.unlink()
        with (path.parent / 'localapi.json.lock').open('a+b') as daemon_lock:
            fcntl.flock(daemon_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(auth.AuthenticationError, 'Stop the tailscaled'):
                auth.restore_authentication(self.root)
            self.assertFalse(path.exists())
        _, restored = auth.restore_authentication(self.root)
        self.assertEqual(restored, 1)

    def test_explicit_tailscale_migration_wins_over_backup(self):
        path = self.write(auth.TAILSCALE_STATE, b'old device identity')
        self.write(CODEX, b'codex login')
        archive, _ = auth.save_authentication(self.root)
        path.unlink()
        (self.root / CODEX).unlink()
        _, restored = auth.restore_authentication(self.root, skip_tailscale=True)
        self.assertEqual(restored, 1)
        self.assertFalse(path.exists())
        self.assertEqual((self.root / CODEX).read_bytes(), b'codex login')
        path.write_bytes(b'explicitly migrated device identity')
        auth.save_authentication(self.root)
        self.assertEqual(auth.read_archive(archive)[auth.TAILSCALE_STATE], b'explicitly migrated device identity')

    def test_failed_save_keeps_previous_backup_and_cleans_temporary(self):
        path = self.write(CODEX, b'original login')
        archive, _ = auth.save_authentication(self.root)
        before = archive.read_bytes()
        path.write_bytes(b'refreshed login')
        with patch.object(auth.os, 'replace', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                auth.save_authentication(self.root)
        self.assertEqual(archive.read_bytes(), before)
        self.assertEqual(list(archive.parent.glob('.authentication-*')), [])

    def test_restore_cannot_clobber_a_concurrent_login(self):
        path = self.write(CODEX, b'backed-up login')
        auth.save_authentication(self.root)
        path.unlink()
        link = os.link

        def concurrent_login(source, target):
            Path(target).write_bytes(b'concurrent fresh login')
            link(source, target)

        with patch.object(auth.os, 'link', side_effect=concurrent_login):
            _, restored = auth.restore_authentication(self.root)
        self.assertEqual(restored, 0)
        self.assertEqual(path.read_bytes(), b'concurrent fresh login')

    def test_single_backup_writer_lock_releases_after_operation(self):
        with auth.authentication_lock(self.root):
            with self.assertRaisesRegex(auth.AuthenticationError, 'in progress'):
                auth.save_authentication(self.root)
        auth.save_authentication(self.root)

    def test_shell_commands_export_and_restore_custom_file_without_outputting_tokens(self):
        path = self.write(GH, b'PRIVATE_GITHUB_TOKEN_FOR_TEST')
        result = self.setup_command('auth', 'save', 'portable-auth.json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('PRIVATE_GITHUB_TOKEN_FOR_TEST', result.stdout + result.stderr)
        path.unlink()
        result = self.setup_command('auth', 'restore', 'portable-auth.json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(path.read_bytes(), b'PRIVATE_GITHUB_TOKEN_FOR_TEST')

    def test_explicit_missing_restore_file_fails(self):
        result = self.setup_command('auth', 'restore', 'does-not-exist.json')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('does not exist', result.stderr)

    def test_remote_pairing_failure_still_saves_token_and_preserves_exit_status(self):
        self.write('remote-orca.py', (
            "from pathlib import Path\n"
            "p = Path(__file__).parent / 'bin/orca.AppImage.config/orca/orca-environments.json'\n"
            "p.parent.mkdir(parents=True)\n"
            "p.write_text('{\"deviceToken\":\"paired-before-check-failed\"}')\n"
            "raise SystemExit(7)\n"
        ).encode())
        result = self.setup_command('remote', 'pair', 'example')
        self.assertEqual(result.returncode, 7, result.stderr)
        entries = auth.read_archive(self.root / 'state/authentication.json')
        self.assertIn(b'paired-before-check-failed', entries[ENVIRONMENTS])
        self.assertNotIn('paired-before-check-failed', result.stdout + result.stderr)

    def test_remote_preview_keeps_json_output_and_does_not_save(self):
        self.write('remote-orca.py', b'print(\'{"servers": []}\')\n')
        result = self.setup_command('remote', 'import', '--no-add', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'servers': []})
        self.assertFalse((self.root / 'state/authentication.json').exists())

    def test_full_setup_restores_before_login_and_saves_on_later_failure(self):
        path = self.write(CODEX, b'backed-up-login')
        archive, _ = auth.save_authentication(self.root)
        path.unlink()
        after = '''python3 - "$ROOT" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1]) / 'state/codex/auth.json'
assert path.read_bytes() == b'backed-up-login'
path.write_bytes(b'completed-new-login')
PY
exit 17
'''
        result = self.setup_until_credentials(after)
        self.assertEqual(result.returncode, 17, result.stdout + result.stderr)
        self.assertEqual(path.read_bytes(), b'completed-new-login')
        self.assertEqual(auth.read_archive(archive)[CODEX], b'completed-new-login')
        self.assertNotIn('completed-new-login', result.stdout + result.stderr)

    def test_full_setup_saves_explicitly_migrated_tailscale_identity(self):
        path = self.write(auth.TAILSCALE_STATE, b'backed-up-old-identity')
        archive, _ = auth.save_authentication(self.root)
        path.unlink()
        previous = self.write('previous/tailscaled.state', b'explicitly-selected-identity').parent
        # Include the actual post-migration reset that enables subsequent saves.
        after = (REPO / 'setup.sh').read_text().split('stage tailscale-daemon\n', 1)[1].split('if ! tailscale_status', 1)[0]
        result = self.setup_until_credentials(after + 'authentication save\n', '--tailscale-state-from', str(previous))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(auth.read_archive(archive)[auth.TAILSCALE_STATE], b'explicitly-selected-identity')


if __name__ == '__main__':
    unittest.main()
