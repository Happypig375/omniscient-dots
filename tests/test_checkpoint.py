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
import persistence as checkpoint


REPO = Path(__file__).resolve().parents[1]
CODEX = 'state/codex/auth.json'
GH = 'state/gh/hosts.yml'
ENVIRONMENTS = f'{checkpoint.ORCA_PROFILE}/orca-environments.json'
ROUTE = 'state/remote-orca/example.route.json'


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='checkpoint-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def setup_command(self, *arguments):
        for name in ('setup.sh', 'persistence.py'):
            shutil.copyfile(REPO / name, self.root / name)
        return subprocess.run(['bash', str(self.root / 'setup.sh'), *arguments],
                              cwd=self.root, text=True, capture_output=True, stdin=subprocess.DEVNULL, start_new_session=True, timeout=15)

    def setup_until_credentials(self, after_credentials, *arguments, remote_script=b'# fixture\n'):
        # Execute the real setup startup, restore step and EXIT trap. Stop before
        # it touches ~/.bashrc, browser trust, networking, or application daemons.
        source = (REPO / 'setup.sh').read_text().split('stage bash-startup\n', 1)[0]
        self.write('setup.sh', (source + after_credentials).encode())
        for name in ('persistence.py', 'orca-locks.py', 'orca-window.py'):
            shutil.copyfile(REPO / name, self.root / name)
        self.write('remote-orca.py', remote_script)
        for name in ('gh', 'git', 'ssh', 'curl', 'tar', 'certutil', 'openssl', 'orca', 'orca-desktop'):
            path = self.write('bin/' + name, b'#!/bin/sh\nexit 0\n')
            path.chmod(0o700)
        return subprocess.run(['bash', str(self.root / 'setup.sh'), '--profile', 'both', *arguments],
                              cwd=self.root, text=True, capture_output=True, stdin=subprocess.DEVNULL, start_new_session=True, timeout=15)

    def setup_through_remote_connections(self):
        source = (REPO / 'setup.sh').read_text()
        connections = source.split('stage remote-connections\n', 1)[1].split('stage orca-settings\n', 1)[0]
        return self.setup_until_credentials('stage remote-connections\n' + connections, remote_script=(
            "from pathlib import Path\n"
            "import sys\n"
            "assert sys.argv[1:] == ['import']\n"
            "Path(__file__).with_name('discovery-ran').touch()\n"
        ).encode())

    def test_round_trip_checkpoint_excludes_runtime_and_history(self):
        original = {
            GH: b'github.com:\n  oauth_token: example-github-token\n',
            CODEX: b'{"tokens":{"access_token":"access","refresh_token":"refresh"}}\n',
            checkpoint.TAILSCALE_STATE: b'{"_machinekey":"private-device-identity"}',
            ENVIRONMENTS: b'{"version":1,"environments":[{"deviceToken":"pairing-token"}]}',
            f'{checkpoint.ORCA_PROFILE}/orca-e2ee-keypair.json': b'{"secretKeyB64":"key"}',
            f'{checkpoint.ORCA_PROFILE}/orca-environment-sidecar.json': b'{"version":1,"entries":{}}',
        }
        for name, data in original.items():
            self.write(name, data)
        self.write(ROUTE, b'{"remote_host":"100.64.0.2","remote_port":6768,"local_port":12345,"pid":9876}')
        for name in ('state/codex/history.jsonl', 'bin/tailscale-sandbox-state/localapi.json',
                     f'{checkpoint.ORCA_PROFILE}/.profile-state-access/participants/old.owner',
                     f'{checkpoint.ORCA_PROFILE}/profiles/local-default/profile-state.db'):
            self.write(name, b'not part of the checkpoint')
        saved_checkpoint, count = checkpoint.save_checkpoint(self.root)
        self.assertEqual(saved_checkpoint, self.root / 'state/.omniscient-dots-checkpoint')
        self.assertEqual(count, len(original) + 1)
        self.assertEqual(stat.S_IMODE(saved_checkpoint.stat().st_mode), 0o600)
        packed = saved_checkpoint.read_bytes()
        for label in (b'checkpoint', b'token', b'github', b'refresh', b'files', b'state/codex'):
            self.assertNotIn(label, packed)
        entries = checkpoint.read_checkpoint(saved_checkpoint)
        self.assertEqual(set(entries), {*original, ROUTE})
        self.assertEqual(json.loads(entries[ROUTE]), {'remote_host': '100.64.0.2', 'remote_port': 6768, 'local_port': 12345})
        for name in entries:
            (self.root / name).unlink()
        _, restored = checkpoint.restore_checkpoint(self.root)
        self.assertEqual(restored, count)
        for name, data in original.items():
            self.assertEqual((self.root / name).read_bytes(), data)
            self.assertEqual(stat.S_IMODE((self.root / name).stat().st_mode), 0o600)

    def test_checkpoint_relocation_roundtrip_preserves_grant_and_port(self):
        grant = b'{"environments":[{"deviceToken":"fixture-grant"}]}'
        self.write(ENVIRONMENTS, grant)
        route = {'remote_host': '100.64.0.2', 'remote_port': 6768,
                 'local_port': 23456, 'pid': 9876,
                 'bridge_identity': {'command': str(self.root / 'remote-orca.py')},
                 'bridge_endpoint': ['100.64.0.2', 6768, 23456]}
        self.write(ROUTE, json.dumps(route).encode())
        saved, _ = checkpoint.save_checkpoint(self.root)
        relocated = self.root / 'different checkout'
        relocated.mkdir()
        portable = relocated / checkpoint.DEFAULT_CHECKPOINT
        portable.parent.mkdir()
        shutil.copyfile(saved, portable)
        checkpoint.restore_checkpoint(relocated)
        expected = {key: route[key] for key in ('remote_host', 'remote_port', 'local_port')}
        self.assertEqual(json.loads((relocated / ROUTE).read_bytes()), expected)
        self.assertEqual((relocated / ENVIRONMENTS).read_bytes(), grant)
        checkpoint.save_checkpoint(relocated)
        self.assertEqual(checkpoint.read_checkpoint(portable), checkpoint.read_checkpoint(saved))

    def test_restore_keeps_newer_tokens_then_save_updates_checkpoint(self):
        path = self.write(CODEX, b'{"refresh_token":"older"}')
        saved_checkpoint, _ = checkpoint.save_checkpoint(self.root)
        newer = b'{"refresh_token":"newer"}'
        path.write_bytes(newer)
        _, restored = checkpoint.restore_checkpoint(self.root)
        self.assertEqual(restored, 0)
        self.assertEqual(path.read_bytes(), newer)
        checkpoint.save_checkpoint(self.root)
        self.assertEqual(checkpoint.read_checkpoint(saved_checkpoint)[CODEX], newer)

    def test_save_retains_missing_credentials_during_partial_setup(self):
        path = self.write(CODEX, b'codex-authorized')
        saved_checkpoint, _ = checkpoint.save_checkpoint(self.root)
        path.unlink()
        self.write(GH, b'github-authorized')
        checkpoint.save_checkpoint(self.root)
        self.assertEqual(checkpoint.read_checkpoint(saved_checkpoint), {CODEX: b'codex-authorized', GH: b'github-authorized'})

    @unittest.skipUnless(shutil.which('file'), 'file utility is unavailable')
    def test_checkpoint_file_type_is_generic_data(self):
        self.write(CODEX, b'PRIVATE_CURRENT_LOGIN')
        saved_checkpoint, _ = checkpoint.save_checkpoint(self.root)
        result = subprocess.run(['file', '--brief', str(saved_checkpoint)],
                                text=True, capture_output=True, stdin=subprocess.DEVNULL, start_new_session=True, check=True, timeout=5)
        self.assertEqual(result.stdout.strip(), 'data')

    def test_invalid_entries_restore_nothing_and_do_not_leak_contents(self):
        valid = b'PRIVATE_CURRENT_LOGIN'
        invalid = [
            {CODEX: valid, '../escaped': valid},
            {CODEX: valid, GH: b''},
            {CODEX: valid, ROUTE: b'PRIVATE_INVALID_ROUTE'},
        ]
        for index, files in enumerate(invalid):
            with self.subTest(index=index):
                saved_checkpoint = self.root / checkpoint.DEFAULT_CHECKPOINT
                checkpoint.write_checkpoint(saved_checkpoint, files)
                before = saved_checkpoint.read_bytes()
                result = self.setup_command('checkpoint', 'restore')
                self.assertEqual(result.returncode, 1)
                self.assertNotIn('PRIVATE_', result.stdout + result.stderr)
                self.assertFalse((self.root / CODEX).exists())
                self.assertEqual(saved_checkpoint.read_bytes(), before)

    def test_current_checkpoint_rejects_corruption_without_restoring_or_leaking(self):
        path = self.write(CODEX, b'PRIVATE_CURRENT_LOGIN')
        saved_checkpoint, _ = checkpoint.save_checkpoint(self.root)
        packed = saved_checkpoint.read_bytes()
        path.unlink()
        changed_payload = bytearray(packed)
        changed_payload[-1] ^= 1
        changed_digest = bytearray(packed)
        changed_digest[16] ^= 1
        changed_padding = bytearray(packed)
        changed_padding[0] ^= 1
        invalid = [packed[:31], packed[:-1], bytes(changed_payload), bytes(changed_digest),
                   bytes(changed_padding), packed + b'extra']
        for index, data in enumerate(invalid):
            with self.subTest(index=index):
                saved_checkpoint.write_bytes(data)
                result = self.setup_command('checkpoint', 'restore')
                self.assertEqual(result.returncode, 1)
                self.assertNotIn('PRIVATE_CURRENT_LOGIN', result.stdout + result.stderr)
                self.assertFalse(path.exists())
                self.assertEqual(saved_checkpoint.read_bytes(), data)

    def test_symlinks_are_not_read_or_replaced(self):
        external = self.write('outside', b'keep external token')
        credential = self.root / CODEX
        credential.parent.mkdir(parents=True)
        credential.symlink_to(external)
        with self.assertRaises(checkpoint.CheckpointError):
            checkpoint.save_checkpoint(self.root)
        credential.unlink()
        credential.write_bytes(b'backed-up token')
        checkpoint.save_checkpoint(self.root)
        credential.unlink()
        credential.symlink_to(external)
        with self.assertRaises(checkpoint.CheckpointError):
            checkpoint.restore_checkpoint(self.root)
        self.assertTrue(credential.is_symlink())
        self.assertEqual(external.read_bytes(), b'keep external token')

    def test_symlinked_parent_does_not_receive_credentials(self):
        self.write(CODEX, b'token')
        checkpoint.save_checkpoint(self.root)
        shutil.rmtree(self.root / 'state/codex')
        outside = self.root / 'outside'
        outside.mkdir()
        (self.root / 'state/codex').symlink_to(outside)
        with self.assertRaises(checkpoint.CheckpointError):
            checkpoint.restore_checkpoint(self.root)
        self.assertFalse((outside / 'auth.json').exists())

    def test_tailscale_running_lock_prevents_identity_restore(self):
        path = self.write(checkpoint.TAILSCALE_STATE, b'device identity')
        checkpoint.save_checkpoint(self.root)
        path.unlink()
        with (path.parent / 'localapi.json.lock').open('a+b') as daemon_lock:
            fcntl.flock(daemon_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(checkpoint.CheckpointError, 'Stop the tailscaled'):
                checkpoint.restore_checkpoint(self.root)
            self.assertFalse(path.exists())
        _, restored = checkpoint.restore_checkpoint(self.root)
        self.assertEqual(restored, 1)

    def test_failed_save_keeps_previous_checkpoint_and_cleans_temporary(self):
        path = self.write(CODEX, b'original login')
        saved_checkpoint, _ = checkpoint.save_checkpoint(self.root)
        before = saved_checkpoint.read_bytes()
        path.write_bytes(b'refreshed login')
        with patch.object(checkpoint.os, 'replace', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                checkpoint.save_checkpoint(self.root)
        self.assertEqual(saved_checkpoint.read_bytes(), before)
        self.assertEqual(list(saved_checkpoint.parent.glob('.omniscient-dots-checkpoint-*')), [])

    def test_restore_cannot_clobber_a_concurrent_login(self):
        path = self.write(CODEX, b'backed-up login')
        checkpoint.save_checkpoint(self.root)
        path.unlink()
        link = os.link

        def concurrent_login(source, target):
            Path(target).write_bytes(b'concurrent fresh login')
            link(source, target)

        with patch.object(checkpoint.os, 'link', side_effect=concurrent_login):
            _, restored = checkpoint.restore_checkpoint(self.root)
        self.assertEqual(restored, 0)
        self.assertEqual(path.read_bytes(), b'concurrent fresh login')

    def test_single_checkpoint_writer_lock_releases_after_operation(self):
        with checkpoint.checkpoint_lock(self.root):
            with self.assertRaisesRegex(checkpoint.CheckpointError, 'in progress'):
                checkpoint.save_checkpoint(self.root)
        checkpoint.save_checkpoint(self.root)

    def test_shell_commands_export_and_restore_custom_file_without_outputting_tokens(self):
        path = self.write(GH, b'PRIVATE_GITHUB_TOKEN_FOR_TEST')
        result = self.setup_command('checkpoint', 'save', 'portable.checkpoint')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Checkpoint saved to ', result.stdout)
        self.assertNotIn('PRIVATE_GITHUB_TOKEN_FOR_TEST', result.stdout + result.stderr)
        path.unlink()
        result = self.setup_command('checkpoint', 'restore', 'portable.checkpoint')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Checkpoint restored from ', result.stdout)
        self.assertEqual(path.read_bytes(), b'PRIVATE_GITHUB_TOKEN_FOR_TEST')

    def test_explicit_missing_restore_file_fails(self):
        result = self.setup_command('checkpoint', 'restore', 'does-not-exist.json')
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
        entries = checkpoint.read_checkpoint(self.root / checkpoint.DEFAULT_CHECKPOINT)
        self.assertIn(b'paired-before-check-failed', entries[ENVIRONMENTS])
        self.assertNotIn('paired-before-check-failed', result.stdout + result.stderr)

    def test_remote_preview_keeps_json_output_and_does_not_save(self):
        self.write('remote-orca.py', b'print(\'{"servers": []}\')\n')
        result = self.setup_command('remote', 'import', '--no-add', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'servers': []})
        self.assertFalse((self.root / checkpoint.DEFAULT_CHECKPOINT).exists())

    def test_setup_from_checkpoint_restores_connections_without_scanning_for_additions(self):
        profile = self.write(ENVIRONMENTS, b'{"deviceToken":"saved-pairing"}')
        route = self.write(ROUTE, b'{"remote_host":"100.64.0.2","remote_port":6768}')
        checkpoint.save_checkpoint(self.root)
        profile.unlink()
        route.unlink()
        result = self.setup_through_remote_connections()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(profile.read_bytes(), b'{"deviceToken":"saved-pairing"}')
        self.assertEqual(json.loads(route.read_bytes()), {'remote_host': '100.64.0.2', 'remote_port': 6768})
        self.assertFalse((self.root / 'discovery-ran').exists())
        self.assertIn('./setup.sh remote import', result.stdout)

    def test_setup_without_checkpoint_still_scans_after_creating_its_first_checkpoint(self):
        result = self.setup_through_remote_connections()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / checkpoint.DEFAULT_CHECKPOINT).exists())
        self.assertTrue((self.root / 'discovery-ran').exists())

    def test_explicit_remote_import_updates_existing_checkpoint(self):
        self.write(ENVIRONMENTS, b'{"deviceToken":"saved-pairing"}')
        saved_checkpoint, _ = checkpoint.save_checkpoint(self.root)
        self.write('remote-orca.py', (
            "from pathlib import Path\n"
            "import sys\n"
            "assert sys.argv[1:] == ['import']\n"
            f"(Path(__file__).parent / {ENVIRONMENTS!r}).write_bytes(b'new-pairing')\n"
        ).encode())
        result = self.setup_command('remote', 'import')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(checkpoint.read_checkpoint(saved_checkpoint)[ENVIRONMENTS], b'new-pairing')

    def test_full_setup_restores_before_login_and_saves_on_later_failure(self):
        path = self.write(CODEX, b'backed-up-login')
        tailnet_state = self.write(checkpoint.TAILSCALE_STATE, b'backed-up-device-state')
        saved_checkpoint, _ = checkpoint.save_checkpoint(self.root)
        path.unlink()
        tailnet_state.unlink()
        after = '''python3 - "$ROOT" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1]) / 'state/codex/auth.json'
assert path.read_bytes() == b'backed-up-login'
path.write_bytes(b'completed-new-login')
tailnet_state = Path(sys.argv[1]) / 'bin/tailscale-sandbox-state/tailscaled.state'
assert tailnet_state.read_bytes() == b'backed-up-device-state'
tailnet_state.write_bytes(b'updated-device-state')
PY
exit 17
'''
        result = self.setup_until_credentials(after)
        self.assertEqual(result.returncode, 17, result.stdout + result.stderr)
        self.assertEqual(path.read_bytes(), b'completed-new-login')
        self.assertEqual(checkpoint.read_checkpoint(saved_checkpoint)[CODEX], b'completed-new-login')
        self.assertEqual(tailnet_state.read_bytes(), b'updated-device-state')
        self.assertEqual(checkpoint.read_checkpoint(saved_checkpoint)[checkpoint.TAILSCALE_STATE], b'updated-device-state')
        self.assertNotIn('completed-new-login', result.stdout + result.stderr)
        self.assertNotIn('updated-device-state', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
