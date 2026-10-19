#!/usr/bin/env python3
"""Migrate state from a same-user tailscaled owning the loopback proxy."""
from pathlib import Path
import fcntl
import json
import os
import socket
import shutil
import signal
import tempfile
import subprocess
import sys


def listener_inodes(proc, port):
    address = socket.inet_aton('127.0.0.1')[::-1].hex().upper()
    expected = f'{address}:{port:04X}'
    return {fields[9] for line in (proc / 'net/tcp').read_text().splitlines()[1:]
            if len(fields := line.split()) > 9 and fields[1] == expected and fields[3] == '0A'}


def option(arguments, name):
    for index, value in enumerate(arguments):
        if value in (f'--{name}', f'-{name}'):
            return arguments[index + 1] if index + 1 < len(arguments) else None
        for prefix in (f'--{name}=', f'-{name}='):
            if value.startswith(prefix):
                return value[len(prefix):]
    return None


def discover(proc=Path('/proc'), port=1055):
    inodes = listener_inodes(proc, port)
    candidates = []
    for process in proc.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            if process.stat().st_uid != os.geteuid():
                continue
            executable = Path(os.readlink(process / 'exe').removesuffix(' (deleted)'))
            if executable.name != 'tailscaled':
                continue
            if not any(os.readlink(fd) in {f'socket:[{inode}]' for inode in inodes}
                       for fd in (process / 'fd').iterdir()):
                continue
            arguments = (process / 'cmdline').read_bytes().decode().rstrip('\0').split('\0')
            endpoint = option(arguments, 'socket')
            endpoint = Path(endpoint) if endpoint else executable.parent / 'tailscale-sandbox-state/localapi.json'
            if not endpoint.is_absolute():
                endpoint = (process / 'cwd').resolve() / endpoint
            state = option(arguments, 'state')
            statedir = option(arguments, 'statedir')
            if state and not state.startswith('/'):
                continue  # Non-file backends and relative paths need explicit handling.
            state_file = Path(state) if state else (Path(statedir) / 'tailscaled.state' if statedir else executable.parent / 'tailscale-sandbox-state/tailscaled.state')
            if not state_file.is_absolute():
                state_file = (process / 'cwd').resolve() / state_file
            started = (process / 'stat').read_text().rpartition(')')[2].split()[19]
            candidates.append((int(process.name), started, endpoint, state_file))
        except (OSError, UnicodeError):
            continue
    return list(set(candidates))


def reuse(root):
    endpoints = discover()
    if len(endpoints) != 1:
        return False
    pid, started, endpoint, state_file = endpoints[0]
    result = subprocess.run([str(root / 'bin/tailscale'), '--socket', str(endpoint), 'status', '--json'],
                            capture_output=True, text=True, timeout=10)
    try:
        status = json.loads(result.stdout)
        if not isinstance(status, dict) or not status.get('BackendState'):
            return False
    except ValueError:
        return False
    local = root / 'bin/tailscale-sandbox-state'
    source = state_file.parent
    if (not state_file.is_file() or state_file.is_symlink() or source.is_symlink()
            or source.resolve() == local.resolve()):
        return False
    # Finish validation and staging before interrupting the running daemon.
    staging = Path(tempfile.mkdtemp(prefix='.tailscale-migrate-', dir=root))
    descriptor = None
    state_lock = None
    try:
        if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
            return False
        descriptor = os.pidfd_open(pid)
        process = Path('/proc') / str(pid)
        if process.stat().st_uid != os.geteuid() or process.joinpath('stat').read_text().rpartition(')')[2].split()[19] != started:
            return False
        print(f'Stopping tailscaled PID {pid} to migrate state from {source}', flush=True)
        signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        import select
        if not select.select([descriptor], [], [], 20)[0]:
            raise OSError('The original tailscaled did not stop within 20 seconds.')
        state_lock = (source / 'localapi.json.lock').open('a+b')
        fcntl.flock(state_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # The endpoint and its lock describe the old process, not its identity.
        shutil.copytree(source, staging / 'state', symlinks=True,
                        ignore=shutil.ignore_patterns('localapi.json', 'localapi.json.lock'))
        copied = staging / 'state'
        if state_file.name != 'tailscaled.state':
            if (copied / 'tailscaled.state').exists():
                raise OSError('Source contains multiple possible identity files; refusing to replace one.')
            (copied / state_file.name).rename(copied / 'tailscaled.state')
        (copied / 'tailscaled.state').chmod(0o600)
        copied.chmod(0o700)
        if local.exists() or local.is_symlink():
            backup = Path(tempfile.mkdtemp(prefix='tailscale-state-before-migration-', dir=root / 'state'))
            local.rename(backup / 'tailscale-sandbox-state')
            print(f'Previous local state preserved in {backup}', flush=True)
        copied.rename(local)
        print(f'Tailscale state migrated to {local}; original state preserved at {source}', flush=True)
        return True
    finally:
        if state_lock is not None:
            state_lock.close()
        if descriptor is not None:
            os.close(descriptor)
        shutil.rmtree(staging)



if __name__ == '__main__':
    try:
        sys.exit(0 if reuse(Path(sys.argv[1]).resolve()) else 1)
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f'Tailscale migration failed: {error}', file=sys.stderr)
        sys.exit(2)
