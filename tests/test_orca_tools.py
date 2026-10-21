import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('tools', REPO / 'orca-tools.py')
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)


class OrcaToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {'HOME': str(self.root / 'home'), 'CODEX_HOME': str(self.root / 'state/codex'), 'PATH': '/usr/bin:/bin'}
        self.calls = []

    def installer(self, *args, env):
        self.calls.append(args)
        self.assertEqual(args, ('npx', '--yes', 'skills@1.7.2', 'add', tools.SOURCE,
                               '--skill', 'orca-cli', 'orchestration', '--global', '--agent', 'codex', '--agent', 'universal', '-y'))
        stage = Path(env['HOME'])
        self.assertNotEqual(stage, Path(self.env['HOME']))
        for name in tools.SKILLS:
            source = stage / '.agents/skills' / name
            source.mkdir(parents=True)
            (source / 'SKILL.md').write_text('official fixture ORCA_CLI_COMMAND ' + name)
        return ''

    def test_regenerate_verify_and_reuse_preserving_other_files(self):
        unrelated = Path(self.env['CODEX_HOME']) / 'skills/custom/SKILL.md'
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text('user')
        with patch.object(tools, 'run', side_effect=self.installer):
            tools.install_skills(self.root, self.env)
            installed = Path(self.env['CODEX_HOME']) / 'skills/orca-cli/SKILL.md'
            before = installed.stat().st_mtime_ns
            tools.install_skills(self.root, self.env)
            self.assertEqual(installed.stat().st_mtime_ns, before)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(unrelated.read_text(), 'user')

    def test_user_conflict_preflight_changes_nothing(self):
        dest = Path(self.env['HOME']) / '.agents/skills/orchestration'
        dest.mkdir(parents=True)
        (dest / 'SKILL.md').write_text('user')
        with patch.object(tools, 'run', side_effect=self.installer):
            with self.assertRaisesRegex(RuntimeError, 'Preserving existing'):
                tools.install_skills(self.root, self.env)
        self.assertEqual((dest / 'SKILL.md').read_text(), 'user')
        self.assertFalse((Path(self.env['CODEX_HOME']) / 'skills/orca-cli').exists())

    def test_modified_managed_skill_preserved(self):
        with patch.object(tools, 'run', side_effect=self.installer):
            tools.install_skills(self.root, self.env)
            dest = Path(self.env['CODEX_HOME']) / 'skills/orca-cli/SKILL.md'
            dest.write_text('user modification')
            with self.assertRaisesRegex(RuntimeError, 'Preserving existing'):
                tools.install_skills(self.root, self.env)
            self.assertEqual(dest.read_text(), 'user modification')

    def test_runtime_reuses_compatible_toolchain(self):
        with patch.object(tools, 'compatible', return_value=True), patch.object(tools, 'run') as run:
            self.assertEqual(tools.runtime(self.root, self.env), self.env)
            run.assert_not_called()

    def test_runtime_checksum_failure_does_not_install(self):
        def download(*args, **kwargs):
            Path(args[args.index('-o') + 1]).write_bytes(b'bad archive')
            return ''
        with patch.object(tools, 'compatible', return_value=False), patch.object(tools, 'run', side_effect=download):
            with self.assertRaisesRegex(RuntimeError, 'SHA-256'):
                tools.runtime(self.root, self.env)
        self.assertFalse((self.root / 'state/node' / f'node-v{tools.NODE}-linux-x64').exists())

    def test_existing_fallback_verified_without_download(self):
        target = self.root / 'state/node' / f'node-v{tools.NODE}-linux-x64'
        target.mkdir(parents=True)
        versions = {'node': 'v' + tools.NODE, 'npm': '11.19.1', 'npx': '11.19.1'}
        with patch.object(tools, 'compatible', return_value=True), patch.object(
                tools, 'run', side_effect=lambda *args, **kwargs: versions[args[0]]):
            env = tools.runtime(self.root, self.env)
        self.assertTrue(env['PATH'].startswith(str(target / 'bin')))

    def test_installer_failure_does_not_publish(self):
        import subprocess
        with patch.object(tools, 'run', side_effect=subprocess.CalledProcessError(1, 'npx')):
            with self.assertRaises(subprocess.CalledProcessError):
                tools.install_skills(self.root, self.env)
        self.assertFalse((Path(self.env['CODEX_HOME']) / 'skills').exists())

    def test_symlink_destination_preserved(self):
        outside = self.root / 'outside'
        outside.mkdir()
        dest = Path(self.env['CODEX_HOME']) / 'skills'
        dest.parent.mkdir(parents=True)
        dest.symlink_to(outside, target_is_directory=True)
        with patch.object(tools, 'run', side_effect=self.installer):
            with self.assertRaisesRegex(RuntimeError, 'symlinked user'):
                tools.install_skills(self.root, self.env)
        self.assertEqual(list(outside.iterdir()), [])

    def test_checkpoint_excludes_install_artifacts(self):
        import persistence
        for name in ('state/node/runtime', 'state/cache/npm/token', 'state/codex/skills/orca-cli/SKILL.md', 'state/orca-tools/skills.json'):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('fixture')
        filename, count = persistence.save_checkpoint(self.root)
        self.assertEqual(count, 0)
        self.assertEqual(persistence.read_checkpoint(filename), {})

    def test_stage_only_after_supervisor_guard_and_download(self):
        source = (REPO / 'setup.sh').read_text()
        self.assertLess(source.index('if (( ! SETUP_SUPERVISOR ))'), source.index('stage orca-download'))
        self.assertLess(source.index('stage orca-download'), source.index('stage orca-cli-and-skills'))
        self.assertIn('export ORCA_CLI_COMMAND="$ORCA_DOTS_DIR/bin/orca"', source)

    def test_fresh_runtime_install_executes_selected_binaries(self):
        import hashlib
        import subprocess
        import tarfile
        old = self.root / 'old'
        old.mkdir()
        for name in ('node', 'npm', 'npx'):
            executable = old / name
            executable.write_text('#!/bin/sh\necho v18.0.0\n')
            executable.chmod(0o755)
        package = self.root / 'package' / f'node-v{tools.NODE}-linux-x64'
        (package / 'bin').mkdir(parents=True)
        for name, version in (('node', 'v' + tools.NODE), ('npm', '11.19.1'), ('npx', '11.19.1')):
            executable = package / 'bin' / name
            executable.write_text('#!/bin/sh\necho ' + version + '\n')
            executable.chmod(0o755)
        archive = self.root / 'fixture.tar.xz'
        with tarfile.open(archive, 'w:xz') as tar:
            tar.add(package, arcname=package.name)
        original = tools.run
        def download(*args, **kwargs):
            if args[0] == 'curl':
                Path(args[args.index('-o') + 1]).write_bytes(archive.read_bytes())
                return ''
            return original(*args, **kwargs)
        env = {**self.env, 'PATH': str(old) + ':' + self.env['PATH']}
        with patch.object(tools, 'SHA256', hashlib.sha256(archive.read_bytes()).hexdigest()), patch.object(tools, 'run', side_effect=download):
            selected = tools.runtime(self.root, env)
        self.assertEqual(original('node', '--version', env=selected), 'v' + tools.NODE)
        # Exercise the real sourced wrapper with a harmless child standing in
        # for full setup; it installs the runtime after the initial env load.
        checkout = self.root / 'checkout'
        checkout.mkdir()
        source = (REPO / 'setup.sh').read_text()
        boundary = source.index('set -Eeuo pipefail')
        child = '''mkdir -p "$ORCA_DOTS_DIR/state/node"
cp -a "$TEST_NODE_PACKAGE" "$ORCA_DOTS_DIR/state/node/"
'''
        (checkout / 'setup.sh').write_text(source[:boundary] + child)
        script = '''hash node
source "$TEST_CHECKOUT/setup.sh" --profile supervisor
node --version
npm --version
npx --version
'''
        result = subprocess.check_output(['/bin/bash', '-c', script], text=True, env={
            **os.environ, **env, 'TEST_NODE_PACKAGE': str(package), 'TEST_CHECKOUT': str(checkout)})
        self.assertEqual(result.splitlines(), ['v' + tools.NODE, '11.19.1', '11.19.1'])
        # An existing fallback must beat an old shadowing node and an existing
        # PATH entry for the fallback must be moved to the front.
        target_bin = checkout / 'state/node' / package.name / 'bin'
        result = subprocess.check_output(['/bin/bash', '-c',
            'source "$TEST_CHECKOUT/setup.sh" --env-only; node --version'], text=True,
            env={**os.environ, **env, 'PATH': str(old) + ':' + str(target_bin) + ':/usr/bin:/bin',
                 'TEST_CHECKOUT': str(checkout)})
        self.assertEqual(result.strip(), 'v' + tools.NODE)

    def test_pinned_installer_minimum_node_version(self):
        for version, expected in (('v18.0.0', False), ('v22.19.0', False),
                                  ('v22.20.0', True), ('v24.22.1', True)):
            with self.subTest(version=version), patch.object(
                    tools, 'run', side_effect=lambda *args, **kwargs:
                    version if args[0] == 'node' else '11.19.1'):
                self.assertEqual(tools.compatible(self.env), expected)

    def test_missing_shared_skill_does_not_publish(self):
        with patch.object(tools, 'run', return_value=''):
            with self.assertRaisesRegex(RuntimeError, 'Unsafe skill directory'):
                tools.install_skills(self.root, self.env)
        self.assertFalse((Path(self.env['CODEX_HOME']) / 'skills').exists())
