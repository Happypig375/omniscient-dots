import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), REPO / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_long_profile_alias_preserves_data_and_binds_unix_socket(self):
        helper = load('orca-profile')
        profile = self.root / ('nested' * 30) / 'profile'
        profile.mkdir(parents=True)
        (profile / 'saved-state').write_text('fixture')
        # Isolate the private alias directory too.
        with patch.object(helper.os, 'getuid', return_value=os.getuid()), \
                patch.object(helper, 'Path', wraps=Path) as paths:
            paths.side_effect = lambda value: self.root / 'aliases' if value == '/tmp' else Path(value)
            (self.root / 'aliases').mkdir()
            alias = helper.profile_path(profile)
            self.assertEqual(helper.profile_path(profile), alias)
            self.assertEqual((alias / 'saved-state').read_text(), 'fixture')
            self.assertLess(len(os.fsencode(str(alias / 'orcad.sock'))), 108)
            try:
                listener = socket.socket(socket.AF_UNIX)
            except PermissionError:
                self.assertTrue(profile.is_dir())
                self.skipTest('Managed environment forbids AF_UNIX creation; alias checks passed')
            if listener is not None:
                with listener:
                    listener.bind(str(alias / 'orcad.sock'))
                (alias / 'orcad.sock').unlink()
            self.assertTrue(profile.is_dir())

    def test_profile_alias_rejects_unsafe_directory(self):
        helper = load('orca-profile')
        profile = self.root / ('deep' * 30) / 'profile'
        aliases = self.root / 'aliases'
        aliases.mkdir()
        private = aliases / f'od-{os.getuid()}'
        private.mkdir(mode=0o700)
        private.chmod(0o755)
        with patch.object(helper, 'Path', wraps=Path) as paths:
            paths.side_effect = lambda value: aliases if value == '/tmp' else Path(value)
            with self.assertRaisesRegex(RuntimeError, 'Unsafe'):
                helper.profile_path(profile)
        self.assertFalse(profile.exists())

    def test_alias_rejects_symlink_non_directory_and_collisions(self):
        helper = load('orca-profile')
        profile = self.root / ('deep' * 30) / 'profile'
        aliases = self.root / 'aliases'
        aliases.mkdir()
        private = aliases / f'od-{os.getuid()}'
        outside = self.root / 'outside'
        outside.mkdir(mode=0o700)
        with patch.object(helper, 'Path', wraps=Path) as paths:
            paths.side_effect = lambda value: aliases if value == '/tmp' else Path(value)
            private.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, 'Unsafe'):
                helper.profile_path(profile)
            self.assertEqual(list(outside.iterdir()), [])
            private.unlink()
            private.write_text('keep')
            with self.assertRaisesRegex(RuntimeError, 'Unsafe'):
                helper.profile_path(profile)
            self.assertEqual(private.read_text(), 'keep')
            private.unlink()
            private.mkdir(mode=0o700)
            alias = helper.profile_path(profile)
            alias.unlink()
            for kind in ('file', 'directory', 'symlink'):
                with self.subTest(kind=kind):
                    if kind == 'file':
                        alias.write_text('keep')
                    elif kind == 'directory':
                        alias.mkdir()
                    else:
                        alias.symlink_to(outside, target_is_directory=True)
                    with self.assertRaisesRegex(RuntimeError, 'collision'):
                        helper.profile_path(profile)
                    if kind == 'directory':
                        alias.rmdir()
                    else:
                        alias.unlink()
            self.assertFalse(profile.exists())

    def test_alias_threshold_counts_bytes(self):
        helper = load('orca-profile')
        # Character length fits, but UTF-8 exceeds the socket-path budget.
        profile = Path('/') / ('é' * 20)
        self.assertLessEqual(len(str(profile)), 40)
        self.assertGreater(len(os.fsencode(profile)), 40)
        aliases = self.root / 'aliases'
        aliases.mkdir()
        with patch.object(helper, 'Path', wraps=Path) as paths:
            paths.side_effect = lambda value: aliases if value == '/tmp' else Path(value)
            self.assertNotEqual(helper.profile_path(profile), profile)
            short = Path('/' + 'a' * 39)
            self.assertEqual(helper.profile_path(short), short)

    def test_readme_sources_all_profiles_in_cloud_terminal(self):
        step = (REPO / 'README.md').read_text().split('4. After user choice,', 1)[1].split('5. ', 1)[0]
        self.assertIn('on a new terminal on your cloud computer', step)
        for profile in ('worker', 'supervisor', 'both'):
            self.assertIn(f'`source ./setup.sh --profile {profile}`', step)

    def test_source_current_child_and_fresh_shell_resolution(self):
        checkout = self.root / 'checkout'
        checkout.mkdir()
        shutil.copy(REPO / 'setup.sh', checkout / 'setup.sh')
        (checkout / 'bin').mkdir()
        cli = checkout / 'bin/codex'
        cli.write_text('#!/bin/bash\nprintf "local fixture\\n"\n')
        cli.chmod(0o700)
        home = self.root / 'home'
        home.mkdir()
        (home / '.bashrc').write_text(f'source "{checkout}/setup.sh" --env-only\n')
        env = {**os.environ, 'HOME': str(home), 'PATH': '/usr/bin:/bin'}
        script = f'''hash -p /bin/false codex
source "{checkout}/setup.sh" --env-only
type -t codex
command -v codex
command codex
bash --noprofile --norc -c 'command -v codex'
'''
        result = subprocess.run(['bash', '--noprofile', '--norc', '-ic', script], env=env,
                                capture_output=True, stdin=subprocess.DEVNULL, start_new_session=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('function\ncodex\nlocal fixture\n' + str(cli), result.stdout)
        fresh = subprocess.run(['bash', '--noprofile', '-ic', 'type -t codex; command codex'],
                               env=env, capture_output=True, stdin=subprocess.DEVNULL, start_new_session=True, text=True, timeout=10)
        self.assertEqual(fresh.returncode, 0, fresh.stderr)
        self.assertIn('function\nlocal fixture', fresh.stdout)
        child = subprocess.run(['bash', '--noprofile', '--norc', '-c',
                                f'bash "{checkout}/setup.sh" --env-only; command -v codex || true'],
                               env=env, capture_output=True, stdin=subprocess.DEVNULL, start_new_session=True, text=True, timeout=10)
        self.assertNotIn(str(cli), child.stdout)

    def test_bridge_relaunch_after_move_and_endpoint_mismatch(self):
        remote = load('remote-orca')
        checkout = self.root / 'old'
        checkout.mkdir()
        shutil.copy(REPO / 'remote-orca.py', checkout / 'remote-orca.py')
        state = checkout / 'state/remote-orca'
        state.mkdir(parents=True)
        route = state / 'fixture.route.json'
        route.write_text(json.dumps({'remote_host': 'fixture.invalid', 'remote_port': 6768}))
        processes = []
        popen = subprocess.Popen

        def spawn(*args, **kwargs):
            process = popen(*args, **kwargs)
            processes.append(process)
            return process

        self.addCleanup(lambda: [self.stop_process(process) for process in processes])
        with patch.object(remote, 'log'), patch.object(remote.subprocess, 'Popen', side_effect=spawn):
            running = remote.start_bridge(route)
            pid = running['pid']
            moved = self.root / 'new'
            checkout.rename(moved)
            relocated = moved / 'state/remote-orca/fixture.route.json'
            self.assertEqual(remote.start_bridge(relocated)['pid'], pid)
            changed = {**running, 'remote_port': 9999}
            self.assertFalse(remote.bridge_running(relocated, changed))
            self.assertFalse(remote.bridge_running(relocated, {**running, 'bridge_identity': {'start': 'wrong'}}))
            with socket.socket() as unknown:
                unknown.bind(('127.0.0.1', 0))
                unknown.listen()
                unrelated = {**running, 'local_port': unknown.getsockname()[1], 'pid': os.getpid()}
                relocated.write_text(json.dumps(unrelated))
                self.assertFalse(remote.bridge_running(relocated, unrelated))
                with self.assertRaises(RuntimeError):
                    remote.start_bridge(relocated)
                self.assertEqual(unknown.getsockname()[1], unrelated['local_port'])

    def test_legacy_bridge_keeps_listener_and_resumes_without_pairing(self):
        remote = load('remote-orca')
        state = self.root / 'state/remote-orca'
        state.mkdir(parents=True)
        route_path = state / 'legacy.route.json'
        processes = []
        popen = subprocess.Popen

        def spawn(*args, **kwargs):
            process = popen(*args, **kwargs)
            processes.append(process)
            return process

        self.addCleanup(lambda: [self.stop_process(process) for process in processes])
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            route = {'remote_host': '100.64.0.2', 'remote_port': 6768,
                     'local_port': listener.getsockname()[1], 'pid': os.getpid()}
            route_path.write_text(json.dumps(route))
            with patch.object(remote.os, 'kill') as kill, \
                    patch.object(remote.subprocess, 'Popen', side_effect=spawn):
                with self.assertRaises(RuntimeError):
                    remote.start_bridge(route_path)
                kill.assert_not_called()
            self.assertEqual(json.loads(route_path.read_text()), route)
            self.assertEqual(listener.getsockname()[1], route['local_port'])
        # Once the old listener exits, the same route/port can serve the saved grant.
        with patch.object(remote, 'STATE', state), patch.object(remote, 'log'), \
                patch.object(remote, 'prompt_access_link') as prompt, \
                patch.object(remote.subprocess, 'Popen', side_effect=spawn):
            remote.resume()
            resumed = json.loads(route_path.read_text())
            self.assertTrue(remote.bridge_running(route_path, resumed))
            self.assertEqual(resumed['local_port'], route['local_port'])
            prompt.assert_not_called()

    @staticmethod
    def stop_process(process):
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
