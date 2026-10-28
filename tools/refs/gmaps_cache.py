"""Owned content-addressed cache accounting without repeated hard-link stats."""
import os
from pathlib import Path
import stat


def owned_usage(root):
    """Count physical files once; DirEntry inode/type checks avoid redundant stats."""
    root = Path(root)
    stack = [(str(root), root.stat().st_dev)]
    seen = set()
    total = 0
    while stack:
        directory, device = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                try:
                    if entry.is_symlink():
                        raise ValueError('Symlinks are prohibited in the owned capture cache')
                    if entry.is_dir(follow_symlinks=False):
                        stack.append((entry.path, entry.stat(follow_symlinks=False).st_dev))
                        continue
                    key = device, entry.inode()
                    if key in seen:
                        continue
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISREG(info.st_mode):
                        seen.add(key)
                        total += info.st_size
                except FileNotFoundError:
                    continue
    return total


def reserve_owned(root, limit, reserve=0, protected=()):
    # The legacy LRU layout does not apply to session catalogues. Preserve all
    # source evidence and fail the same physical-byte quota before allocation.
    if owned_usage(root) + reserve > limit:
        raise ValueError('Owned capture cache exceeds its physical-byte allocation limit')
    return 0


