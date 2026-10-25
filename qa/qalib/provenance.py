"""Content identities keep regression comparisons honest across poses, photos and prompts."""
import hashlib
import json
import subprocess
from .paths import REPO, resolve


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def view_digest(view):
    return digest({k: view.get(k) for k in ('id', 'pose', 'width', 'height', 'time', 'pose_confidence', 'source')} | {
        'image_sha256': file_digest(resolve(view['image']))})


def git_state():
    def git(*args):
        return subprocess.run(['git', '-C', str(REPO), *args], check=True, capture_output=True, text=True).stdout.strip()
    return {'commit': git('rev-parse', 'HEAD'), 'dirty': bool(git('status', '--porcelain', '--untracked-files=normal'))}
