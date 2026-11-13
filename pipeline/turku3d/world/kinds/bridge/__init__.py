"""Bridges: the structure under and along every deck the ground kind lays over water.

One entity per deck (`ground/decks.py` finds it from OpenStreetMap and the street register). Its facts come from the
map and the scan (`survey.py`): what the bridge carries, its era and structure tags, where the water and the abutment
faces lie along the axis, the ground beside it, the piers the lidar sees and the gates mapped under its ends. Type
rules turn them into abutments, piers, girders or arches, edge beams and parapets (`build.py`); what the map cannot
say about a named bridge is data in `profiles.json`, keyed by its Wikidata id. The deck's top and slab stay the
ground kind's, so streets, kerbs and paving run on across the bridge.

Schema: docs/specs/bridge-description.md.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from ...api import Area, Output, Stub, envelope
from ..ground import decks as decks_module
from . import build, survey

VERSION = 1
HERE = Path(__file__).parent


@lru_cache(maxsize=1)
def profiles() -> dict:
    return json.loads((HERE / 'profiles.json').read_text())['bridges']


def entity_id(key: str) -> str:
    return 'bridge/' + re.sub(r'[^a-z0-9-]+', '-', key.lower()).strip('-')


class Bridge:
    name = 'bridge'
    code = (Path(decks_module.__file__), HERE / 'profiles.json')

    def discover(self, area: Area, sources) -> list[Stub]:
        stubs = []
        for key, own in survey.candidates(area.bbox(survey.FIND)):
            facts = survey.facts(key, own)
            if facts is None:
                continue
            xs = [p[0] for ring in facts['rings'] for p in ring]
            ys = [p[1] for ring in facts['rings'] for p in ring]
            box = (min(xs), min(ys), max(xs), max(ys))
            if not area.touches(box):
                continue
            stubs.append(Stub(entity_id(key), box, [{'source': 'osm', 'key': key}, {'source': 'lidar', 'key': key}], facts))
        return stubs

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        """Source facts, the named bridge's profile, then what a stored description sets (`profile` keys win)."""
        stored = stored or {}
        profile = {**profiles().get(stub.facts.get('wikidata', ''), {}), **stored.get('profile', {})}
        return {**envelope(self.name, VERSION, stub, stored, 'w2-bridges'), **stub.facts, 'profile': profile}

    def compile(self, description: dict) -> Output:
        return build.compile_bridge(description)
