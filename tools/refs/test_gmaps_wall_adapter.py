"""The private scorer selects v4 precisely and hashes the extra evaluator."""

import hashlib
import sys
from types import SimpleNamespace

from gmaps_wall_adapter import install


def test_v4_dispatch_and_versioned_digest(monkeypatch):
    old = lambda scene, record, triangles: 'old'
    facade = SimpleNamespace(sampler=old)
    loop = SimpleNamespace(scorer_digest=lambda: 'private-digest')
    adapter = SimpleNamespace(sampler=lambda scene, record, triangles: 'v4', __file__=__file__)
    monkeypatch.setitem(sys.modules, 'facade_sampling', facade)
    monkeypatch.setitem(sys.modules, 'loop', loop)
    monkeypatch.setitem(sys.modules, 'gmaps_facade_v4', adapter)
    install()
    assert facade.sampler(None, {'kit': {'version': 4}}, None) == 'v4'
    assert facade.sampler(None, {'kit': {'version': 3}}, None) == 'old'
    assert facade.sampler(None, {}, None) == 'old'
    digest = loop.scorer_digest()
    assert digest != 'private-digest' and len(digest) == len(hashlib.sha256().hexdigest())
    install()
    assert loop.scorer_digest() == digest
