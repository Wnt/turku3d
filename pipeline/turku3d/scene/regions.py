"""Explicit selection of complete prepared regions, never legacy tile readers."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re

from .schema import validate_spawn, vector

SCHEMA = 'turku-prepared-regions/1'
IDENTITY = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
URL = re.compile(r'/scenes/[a-z0-9]+(?:-[a-z0-9]+)*\.scene\.json')
SHA = re.compile(r'[0-9a-f]{64}')


def validate_catalog(catalog, scenes=None):
    """Validate descriptors and, when supplied, exact pinned scene documents."""
    if not isinstance(catalog, dict) or set(catalog) != {'schema', 'defaultRegion', 'regions'}:
        raise ValueError('Incomplete prepared region catalog')
    if catalog['schema'] != SCHEMA or not isinstance(catalog['regions'], list):
        raise ValueError('Unsupported prepared region catalog')
    ids = set()
    for row in catalog['regions']:
        if not isinstance(row, dict) or set(row) != {'id', 'label', 'url', 'release', 'bounds', 'spawn'}:
            raise ValueError('Incomplete prepared region descriptor')
        if not isinstance(row['id'], str) or not IDENTITY.fullmatch(row['id']) or row['id'] in ids:
            raise ValueError('Invalid or duplicate prepared region identity')
        ids.add(row['id'])
        if not isinstance(row['label'], str) or not row['label'].strip():
            raise ValueError('Prepared region needs a label')
        if not isinstance(row['url'], str) or not URL.fullmatch(row['url']):
            raise ValueError('Prepared region URL must be a same-origin scene document')
        if not isinstance(row['release'], str) or not SHA.fullmatch(row['release']):
            raise ValueError('Prepared region needs a complete release hash')
        vector(row['bounds'], 4, 'region bounds')
        x, z, X, Z = row['bounds']
        if x >= X or z >= Z:
            raise ValueError('Empty prepared region bounds')
        validate_spawn(row['spawn'], row['bounds'])
        if scenes is not None:
            scene = scenes.get(row['id'])
            if scene is None or any(scene[key] != row[key] for key in ('id', 'release', 'bounds', 'spawn')):
                raise ValueError('Catalog descriptor differs from its pinned scene')
    if catalog['defaultRegion'] not in ids:
        raise ValueError('Default prepared region is missing')
    return catalog


def select_region(catalog, identity=None):
    validate_catalog(catalog)
    requested = catalog['defaultRegion'] if identity is None else identity
    for row in catalog['regions']:
        if row['id'] == requested:
            return row
    raise ValueError('Unknown prepared region: ' + str(requested))


def write_catalog(output, entries, *, default_region):
    """Entries are (scene path, public label, absolute same-origin URL)."""
    from .compile import release_hash
    from .schema import validate_scene
    regions, scenes = [], {}
    for path, label, url in entries:
        scene = validate_scene(json.loads(Path(path).read_text()))
        if release_hash(scene) != scene['release']:
            raise ValueError('Prepared scene release hash does not match its content')
        scenes[scene['id']] = scene
        regions.append(dict(id=scene['id'], label=label, url=url, release=scene['release'],
                            bounds=deepcopy(scene['bounds']), spawn=deepcopy(scene['spawn'])))
    catalog = dict(schema=SCHEMA, defaultRegion=default_region, regions=regions)
    validate_catalog(catalog, scenes)
    Path(output).write_text(json.dumps(catalog, indent=2) + '\n')
    return catalog
