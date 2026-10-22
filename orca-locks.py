#!/usr/bin/env python3
"""Remove stale Orca profile locks when no Orca process is running."""

import os
from pathlib import Path
import shutil
import sys


ORCA_PROCESSES = {'orca', 'orca-ide', 'orca.appimage', 'orcad'}
SHELL_PROCESSES = {'bash', 'dash', 'sh', 'zsh', 'fish', 'env'}
SINGLETON_FILES = {'SingletonLock', 'SingletonCookie', 'SingletonSocket'}


def orca_running(proc=Path('/proc')):
    for process in proc.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            arguments = (process / 'cmdline').read_bytes().split(b'\0')
            if not any(arguments):
                continue  # Exited processes do not hold profile locks.
            command = (process / 'comm').read_text().strip().lower()
            try:
                executable = Path(os.readlink(process / 'exe').removesuffix(' (deleted)')).name.lower()
            except PermissionError:
                # Other users' processes can expose cmdline and comm while Linux
                # denies exe access. That alone is not evidence of a live Orca.
                executable = command
            if executable in ORCA_PROCESSES:
                return True
            # A shell launcher has not opened the profile yet. In particular,
            # this cleanup's own parent launcher must not block stale cleanup.
            if executable in SHELL_PROCESSES:
                continue
            if command in ORCA_PROCESSES or command.startswith('orca helper'):
                return True
            for argument in arguments[1:]:
                path = Path(os.fsdecode(argument))
                if path.name.lower() in ('orcad', 'orcad.js') or {'orcad-artifacts', 'orcad-template'} & set(path.parts):
                    return True
                if path.name == 'daemon-entry.js' and any('orca' in part.lower() for part in path.parts):
                    return True
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            return True  # Keep locks if a live process cannot be inspected.
    return False


def clear_stale_locks(profile, *, proc=Path('/proc')):
    profile = Path(profile)
    if not profile.is_dir() or orca_running(proc):
        return 0

    removed = 0

    def remove(path):
        nonlocal removed
        try:
            if path.is_symlink() or not path.is_dir():
                path.unlink()
            else:
                shutil.rmtree(path)
        except FileNotFoundError:
            return
        removed += 1

    # Participant, candidate and maintenance records are all transient locks.
    remove(profile / '.profile-state-access')
    for directory, subdirectories, filenames in os.walk(profile, followlinks=False):
        for name in [*subdirectories, *filenames]:
            if name.lower() == 'lock' or name.endswith('.lock') or name in SINGLETON_FILES:
                remove(Path(directory) / name)
                if name in subdirectories:
                    subdirectories.remove(name)
    return removed


if __name__ == '__main__':
    try:
        removed = clear_stale_locks(Path(sys.argv[1]))
        if removed:
            print(f'Removed {removed} stale Orca lock entries.', file=sys.stderr)
    except OSError as error:
        print(f'Could not clear Orca locks: {error}', file=sys.stderr)
        sys.exit(1)
