#!/usr/bin/env python3
"""Install the supervisor's Node toolchain and UI-equivalent discovery skills."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

NODE = '24.22.1'
SHA256 = '6c172a9ab6718f7b801fb5a7bd486af2ee5e32758086a04b84fcce2aa77095e6'
SOURCE = 'https://github.com/stablyai/orca/tree/071747853f5995ee233b36751613f09a60007c2b'
SKILLS = ('orca-cli', 'orchestration')


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def compatible(env):
    try:
        version = run('node', '--version', env=env)
        return (tuple(int(part) for part in version.lstrip('v').split('.')[:3]) >= (22, 20, 0)
                and bool(run('npm', '--version', env=env))
                and bool(run('npx', '--version', env=env)))
    except (OSError, ValueError, subprocess.CalledProcessError):
        return False


def runtime(root, env):
    base = root / 'state/node'
    target = base / f'node-v{NODE}-linux-x64'
    if not target.exists() and compatible(env):
        return env
    if not target.exists():
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temporary:
            temporary = Path(temporary)
            archive = temporary / 'node.tar.xz'
            run('curl', '--fail', '--location', '--silent', '--show-error',
                f'https://nodejs.org/dist/v{NODE}/node-v{NODE}-linux-x64.tar.xz', '-o', str(archive), env=env)
            if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
                raise RuntimeError('Node archive SHA-256 mismatch')
            run('tar', '-xJf', str(archive), '-C', str(temporary), env=env)
            (temporary / target.name).rename(target)
    env = {**env, 'PATH': str(target / 'bin') + os.pathsep + env['PATH']}
    if (not compatible(env) or run('node', '--version', env=env) != 'v' + NODE
            or run('npm', '--version', env=env) != '11.19.1'
            or run('npx', '--version', env=env) != '11.19.1'):
        raise RuntimeError('Node/npm/npx verification failed')
    return env


def snapshot(path):
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f'Unsafe skill directory: {path}')
    result = {}
    for item in path.rglob('*'):
        if item.is_symlink():
            raise RuntimeError(f'Unexpected skill symlink: {item}')
        if item.is_file():
            result[str(item.relative_to(path))] = hashlib.sha256(item.read_bytes()).hexdigest()
    if 'SKILL.md' not in result:
        raise RuntimeError(f'Missing SKILL.md: {path}')
    return result


def install_skills(root, env):
    state = root / 'state/orca-tools'
    state.mkdir(parents=True, exist_ok=True)
    manifest = state / 'skills.json'
    previous = json.loads(manifest.read_text()) if manifest.exists() else {}
    # Run the pinned official equivalent in an isolated HOME. Never let skills
    # CLI overwrite real global skill directories or its lock files.
    with tempfile.TemporaryDirectory(dir=state) as temporary:
        stage = Path(temporary)
        staged_env = {**env, 'HOME': str(stage), 'CODEX_HOME': str(stage / '.codex'),
                      'npm_config_cache': str(root / 'state/cache/npm'),
                      'XDG_CONFIG_HOME': str(stage / '.config'),
                      'XDG_DATA_HOME': str(stage / '.local/share'),
                      'XDG_CACHE_HOME': str(stage / '.cache')}
        run('npx', '--yes', 'skills@1.7.2', 'add', SOURCE,
            '--skill', *SKILLS, '--global', '--agent', 'codex', '--agent', 'universal', '-y', env=staged_env)
        plans = []
        for skill in SKILLS:
            source = stage / '.agents/skills' / skill
            hashes = snapshot(source)
            if 'ORCA_CLI_COMMAND' not in (source / 'SKILL.md').read_text():
                raise RuntimeError(f'Skill lacks wrapper resolution: {skill}')
            # skills@1.7.2 treats Codex as universal: both agents use
            # HOME/.agents/skills, without a CODEX_HOME link.
            for parent in (Path(env['CODEX_HOME']) / 'skills', Path(env['HOME']) / '.agents/skills'):
                for ancestor in (parent, *parent.parents):
                    if ancestor.is_symlink():
                        raise RuntimeError(f'Preserving symlinked user directory: {ancestor}')
                dest = parent / skill
                key = str(dest)
                if dest.exists() or dest.is_symlink():
                    current = snapshot(dest)
                    if current != hashes and current != previous.get(key):
                        raise RuntimeError(f'Preserving existing user skill; resolve conflict manually: {dest}')
                plans.append((source, dest, hashes))
        # Preflight every destination before changing any skill.
        installed = {}
        for source, dest, hashes in plans:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists() or snapshot(dest) != hashes:
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(source, dest)
            if snapshot(dest) != hashes:
                raise RuntimeError(f'Skill verification failed: {dest}')
            installed[str(dest)] = hashes
        manifest.write_text(json.dumps(installed, indent=2) + '\n')


def main():
    root = Path(__file__).resolve().parent
    env = runtime(root, dict(os.environ))
    wrapper = str(root / 'bin/orca')
    env['ORCA_CLI_COMMAND'] = wrapper
    run(wrapper, '--help', env=env)
    for skill in SKILLS:
        run(wrapper, 'skills', 'get', skill, env=env)
    install_skills(root, env)
    print('Verified Orca wrapper, Node/npm/npx, and Codex/universal Orca skills.')


if __name__ == '__main__':
    main()
