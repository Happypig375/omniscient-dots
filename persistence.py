#!/usr/bin/env python3
"""Save a private setup checkpoint; restore missing files."""

import argparse
import base64
import binascii
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import zlib


ORCA_PROFILE = 'bin/orca.AppImage.config/orca'
TAILSCALE_STATE = 'bin/tailscale-sandbox-state/tailscaled.state'
DEFAULT_CHECKPOINT = 'state/.omniscient-dots-checkpoint'
CHECKPOINT_PADDING = bytes(8)
CHECKSUM_BYTES = hashlib.sha256().digest_size
CREDENTIAL_FILES = (
    'state/gh/hosts.yml',
    'state/codex/auth.json',
    TAILSCALE_STATE,
    f'{ORCA_PROFILE}/orca-environments.json',
    f'{ORCA_PROFILE}/orca-environment-sidecar.json',
    f'{ORCA_PROFILE}/orca-e2ee-keypair.json',
)
ROUTE_FILE = re.compile(r'state/remote-orca/[A-Za-z0-9_.-]+\.route\.json\Z')


class CheckpointError(Exception):
    """A diagnostic that never includes credential contents."""


def credential_path(root, name):
    if name not in CREDENTIAL_FILES and not ROUTE_FILE.fullmatch(name):
        raise CheckpointError('Checkpoint contains an unsupported file path.')
    path = root
    for part in Path(name).parts:
        path = path / part
        if path.is_symlink():
            raise CheckpointError(f'Checkpoint entry path is a symlink: {path}')
    if path.exists() and not path.is_file():
        raise CheckpointError(f'Checkpoint entry path is not a regular file: {path}')
    return path


def checkpoint_path(root, filename):
    path = Path(filename).expanduser().absolute() if filename else root / DEFAULT_CHECKPOINT
    if path.is_symlink() or path.exists() and not path.is_file():
        raise CheckpointError(f'Checkpoint must be a regular file: {path}')
    for name in CREDENTIAL_FILES:
        if path.resolve() == (root / name).resolve():
            raise CheckpointError('The checkpoint cannot replace a native credential file.')
    resolved = path.resolve()
    if resolved.is_relative_to(root) and ROUTE_FILE.fullmatch(resolved.relative_to(root).as_posix()):
        raise CheckpointError('The checkpoint cannot replace a saved Orca route.')
    return path


def route_payload(data):
    try:
        value = json.loads(data)
        host, port = value['remote_host'], value['remote_port']
        if not isinstance(host, str) or not host or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError
        route = {'remote_host': host, 'remote_port': port}
        if 'local_port' in value:
            local_port = value['local_port']
            if type(local_port) is not int or not 1 <= local_port <= 65535:
                raise ValueError
            route['local_port'] = local_port
        # The port binds the saved pairing to its loopback bridge; PIDs expire.
        return json.dumps(route).encode()
    except (ValueError, KeyError, TypeError):
        raise CheckpointError('A saved Orca route is invalid.') from None


def read_checkpoint(path, *, max_decoded_bytes=None):
    if not path.exists():
        return {}
    try:
        data = path.read_bytes()
        if not data.startswith(CHECKPOINT_PADDING):
            raise ValueError
        data = data[len(CHECKPOINT_PADDING):]
        payload = data[CHECKSUM_BYTES:]
        if data[:CHECKSUM_BYTES] != hashlib.sha256(payload).digest():
            raise ValueError
        stream = zlib.decompressobj(wbits=-zlib.MAX_WBITS)
        data = stream.decompress(payload, 0 if max_decoded_bytes is None else max_decoded_bytes + 1)
        if max_decoded_bytes is not None and len(data) > max_decoded_bytes:
            raise ValueError
        if not stream.eof or stream.unused_data:
            raise ValueError
        checkpoint = json.loads(data)
        if not isinstance(checkpoint, dict) or type(checkpoint.get('version')) is not int or checkpoint['version'] != 3:
            raise ValueError
        entries = checkpoint['files']
        if not isinstance(entries, dict):
            raise ValueError
        decoded = {}
        for name, encoded in entries.items():
            if name not in CREDENTIAL_FILES and not ROUTE_FILE.fullmatch(name):
                raise ValueError
            if not isinstance(encoded, str):
                raise ValueError
            data = base64.b64decode(encoded, validate=True)
            if not data:
                raise ValueError
            decoded[name] = route_payload(data) if ROUTE_FILE.fullmatch(name) else data
        return decoded
    except (ValueError, KeyError, TypeError, binascii.Error, zlib.error):
        raise CheckpointError(f'Checkpoint is invalid: {path}') from None


def write_checkpoint(path, files):
    contents = {'version': 3, 'saved_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'files': {name: base64.b64encode(data).decode('ascii') for name, data in sorted(files.items())}}
    # Neutral padding avoids file-type guesses based on the random-looking digest.
    # Raw DEFLATE omits archive signatures; the unkeyed checksum detects corruption.
    compressor = zlib.compressobj(level=9, wbits=-zlib.MAX_WBITS)
    data = compressor.compress(json.dumps(contents, separators=(',', ':')).encode()) + compressor.flush()
    write_private(path, CHECKPOINT_PADDING + hashlib.sha256(data).digest() + data, replace=True)


@contextmanager
def checkpoint_lock(root):
    state = root / 'state'
    if state.is_symlink():
        raise CheckpointError(f'Checkpoint state directory is a symlink: {state}')
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = root / f'{DEFAULT_CHECKPOINT}.lock'
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'r+b') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CheckpointError('Another checkpoint save or restore is in progress; retry when it finishes.') from None
        yield


def write_private(path, data, *, replace):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.omniscient-dots-checkpoint-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            # Never clobber a login created/refreshed since the existence check.
            try:
                os.link(temporary, path)
            except FileExistsError:
                return False
        return True
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_checkpoint(root, filename=None):
    root = Path(root).resolve()
    with checkpoint_lock(root):
        checkpoint = checkpoint_path(root, filename)
        files = read_checkpoint(checkpoint)
        routes = root / 'state/remote-orca'
        if routes.is_symlink():
            raise CheckpointError(f'Orca route directory is a symlink: {routes}')
        names = [*CREDENTIAL_FILES, *(p.relative_to(root).as_posix() for p in sorted(routes.glob('*.route.json')))]
        for name in names:
            path = credential_path(root, name)
            if path.exists():
                data = path.read_bytes()
                if not data:
                    raise CheckpointError(f'Cannot save an empty credential file: {path}')
                files[name] = route_payload(data) if ROUTE_FILE.fullmatch(name) else data
        write_checkpoint(checkpoint, files)
        return checkpoint, len(files)


def restore_checkpoint(root, filename=None):
    root = Path(root).resolve()
    with checkpoint_lock(root):
        checkpoint = checkpoint_path(root, filename)
        if filename is not None and not checkpoint.exists():
            raise CheckpointError(f'Checkpoint does not exist: {checkpoint}')
        files = read_checkpoint(checkpoint)
        # Validate every target before writing any credentials.
        targets = [(name, credential_path(root, name), data) for name, data in files.items()]
        restored = 0
        for name, path, data in targets:
            if path.exists():
                continue
            if name == TAILSCALE_STATE:
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                descriptor = os.open(path.parent / 'localapi.json.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, 'r+b') as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        raise CheckpointError('Stop the tailscaled using this state directory before restoring its missing identity.') from None
                    restored += write_private(path, data, replace=False)
            else:
                restored += write_private(path, data, replace=False)
        if checkpoint.exists():
            checkpoint.chmod(0o600)
        return checkpoint, restored


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(prog='./setup.sh checkpoint', description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent, help=argparse.SUPPRESS)
    parser.add_argument('--quiet', action='store_true', help='Print only errors.')
    parser.add_argument('action', choices=('save', 'restore'))
    parser.add_argument('file', nargs='?', metavar='<file>', help=f'Default: {DEFAULT_CHECKPOINT}.')
    args = parser.parse_args()
    operation = save_checkpoint if args.action == 'save' else restore_checkpoint
    path, count = operation(args.root, args.file)
    if not args.quiet:
        if args.action == 'save':
            print(f'Checkpoint saved to {path} ({count} files; mode 600).')
        else:
            if path.exists():
                print(f'Checkpoint restored from {path}: {count} missing files restored; existing files kept.')
            else:
                print(f'No checkpoint exists yet: {path}')


if __name__ == '__main__':
    try:
        main()
    except (CheckpointError, OSError) as error:
        # File/OS diagnostics are safe; never print decoded checkpoint payloads.
        print(f'Checkpoint failed: {error}', file=sys.stderr)
        sys.exit(1)
