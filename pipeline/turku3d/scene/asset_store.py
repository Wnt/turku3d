"""Locate immutable prepared assets outside the published source repository."""
import hashlib
from pathlib import Path
import re
import urllib.parse
import urllib.request
from turku3d.config import data_root

HASH_NAME=re.compile(r'^[0-9a-f]{64}\.[a-zA-Z0-9]+$')


def prepared_asset_root():
    return data_root()/'prepared-assets'


def resolve_asset(path,asset_root=None):
    requested=Path(path).expanduser()
    candidate=requested if requested.is_file() else (Path(asset_root).expanduser() if asset_root else prepared_asset_root())/'shared'/requested.name
    if not requested.is_file() and not HASH_NAME.fullmatch(requested.name):
        raise FileNotFoundError('Missing source asset: '+str(requested))
    if not candidate.is_file():raise FileNotFoundError('Missing content-addressed asset: '+str(candidate))
    if HASH_NAME.fullmatch(candidate.name) and hashlib.sha256(candidate.read_bytes()).hexdigest()!=candidate.stem:
        raise ValueError('Prepared asset hash mismatch: '+candidate.name)
    return candidate.resolve()


def read_asset_url(url):
    """Use a verified local content-store object before fetching a source URI."""
    parsed=urllib.parse.urlparse(url);name=Path(parsed.path).name
    if HASH_NAME.fullmatch(name):
        try:return resolve_asset(prepared_asset_root()/'shared'/name).read_bytes()
        except FileNotFoundError:pass
    return urllib.request.urlopen(url,timeout=30).read()
