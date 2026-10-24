#!/usr/bin/env python3
"""Validate and place an already-materialized opaque checkpoint; never restore it."""

import argparse
import os
from pathlib import Path
import stat
import sys
import tempfile

import persistence


MAX_BYTES = 16 * 1024 * 1024


def plain_path(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise persistence.CheckpointError('Symlink paths are not accepted.')
    return path


def read_regular(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise persistence.CheckpointError('Expected a regular checkpoint of at most 16 MiB.')
        data = source.read(MAX_BYTES + 1)
        if not data or len(data) > MAX_BYTES:
            raise persistence.CheckpointError('Checkpoint is empty or too large.')
        return data


def import_checkpoint(source, root):
    root = plain_path(root)
    if not root.is_dir():
        raise persistence.CheckpointError('Checkout must already exist as a directory.')
    source = plain_path(source)
    data = read_regular(source)
    with persistence.checkpoint_lock(root):
        destination = persistence.checkpoint_path(root, None)
        descriptor, temporary = tempfile.mkstemp(prefix='.omniscient-dots-checkpoint-import-', dir=destination.parent)
        try:
            with os.fdopen(descriptor, 'wb') as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            # Validate the same immutable snapshot we will place. No native files
            # are read, restored or enumerated; decoded values are never logged.
            persistence.read_checkpoint(Path(temporary), max_decoded_bytes=64 * 1024 * 1024)
            try:
                os.link(temporary, destination)
                return 'imported'
            except FileExistsError:
                # Never replace an existing file, including a concurrent writer.
                descriptor = os.open(destination, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, 'rb') as existing:
                    info = os.fstat(existing.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                            or info.st_size != len(data) or existing.read(MAX_BYTES + 1) != data):
                        raise persistence.CheckpointError('Existing destination differs or is unsafe; preserved.')
                    os.fchmod(existing.fileno(), 0o600)
                return 'unchanged'
        finally:
            Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='Already-materialized private file (no connector download).')
    parser.add_argument('--root', type=Path, default=Path(__file__).absolute().parent)
    args = parser.parse_args()
    try:
        result = import_checkpoint(args.source, args.root)
    except (persistence.CheckpointError, OSError):
        # Do not expose payloads, connector URLs, source paths or OS diagnostics.
        print('Checkpoint import failed: invalid/unsafe input, unavailable path, or differing destination; existing state preserved.', file=sys.stderr)
        return 1
    print(f'Checkpoint {result}; mode 600. No credentials restored; setup has not run.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
