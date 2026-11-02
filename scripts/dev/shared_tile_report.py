"""Read completed immutable farm products without requesting another build."""
import json
import re
from urllib.request import urlopen


def read(url):
    with urlopen(url, timeout=30) as response:
        return json.load(response)


def product(service, revision, tile):
    if not re.fullmatch(r'[a-f0-9]{40,64}', revision):
        raise ValueError('resolve aliases before reading a product')
    if not re.fullmatch(r't0_-?\d{1,5}_-?\d{1,5}', tile):
        raise ValueError('invalid tile')
    base = f'{service.rstrip("/")}/v/{revision}/coarse/{tile}/'
    # A receipt exists only after every full-product payload has been validated.
    ready = read(base + 'ready.json')
    return dict(revision=revision, tile=tile, ready=ready,
                inputs=read(base + 'input-cache.json'), stages=read(base + 'stage-times.json'))


def compare(before, after):
    if before['tile'] != after['tile']:
        raise ValueError('compare the same area')
    result = dict(tile=after['tile'], before=before['revision'], after=after['revision'],
                  generation=after['ready']['generation'],
                  same_product=before['ready']['generation'] == after['ready']['generation'],
                  built_by_revision=after['ready'].get('revision'),
                  metrics_scope='original completed build, not this read',
                  seconds=after['ready']['seconds'], queue_seconds=after['ready'].get('queue_seconds'))
    for section in ('inputs', 'stages'):
        old = {k: v for k, v in before[section].items() if 'key' in v}
        new = {k: v for k, v in after[section].items() if 'key' in v}
        changed = sorted(k for k in old.keys() & new.keys() if old[k]['key'] != new[k]['key'])
        misses = sorted(k for k, v in new.items() if v.get('cached') is False)
        hits = sum(v.get('cached') is True for v in new.values())
        known = sum(isinstance(v.get('cached'), bool) for v in new.values())
        result[section] = dict(changed=changed, added=sorted(new.keys() - old.keys()),
                               removed=sorted(old.keys() - new.keys()), hits=hits, misses=misses,
                               hit_rate=round(hits / known, 4) if known else None,
                               unchanged_misses=sorted(set(misses) - set(changed) - (new.keys() - old.keys())),
                               miss_seconds={k: new[k].get('seconds') for k in misses})
    return result
