#!/usr/bin/env python3
"""Give Orca a short alias without moving or copying its persistent profile."""
import hashlib
import os
from pathlib import Path
import stat
import sys


def profile_path(profile):
    profile = Path(profile).absolute()
    # Reserve room for Orca's socket names and nested profile IDs (sun_path=108).
    if len(os.fsencode(str(profile))) <= 40:
        return profile
    target = profile.resolve()
    directory = Path('/tmp') / f'od-{os.getuid()}'
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError('Unsafe Orca profile alias directory')
    alias = directory / hashlib.sha256(os.fsencode(str(target))).hexdigest()[:16]
    try:
        alias.symlink_to(target, target_is_directory=True)
    except FileExistsError:
        if not alias.is_symlink() or alias.resolve() != target:
            raise RuntimeError('Orca profile alias collision')
    return alias


if __name__ == '__main__':
    print(profile_path(sys.argv[1]))
