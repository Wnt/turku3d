"""Shared authored night rules. Photo references are private; only parameter data ships."""
import json
from functools import lru_cache
from pathlib import Path

RULES_PATH = Path(__file__).with_suffix('.json')


@lru_cache(maxsize=2)
def _rules(content):
    return json.loads(content)


def rules():
    return _rules(RULES_PATH.read_text())


def use_class(attributes):
    """Conservative building-use class; unknown uses retain residential occupancy."""
    a = attributes or {}
    generic = ('', 'yes', 'unknown', 'none', 'building')
    value = next((str(a[k]).casefold() for k in ('osm_use', 'use', 'amenity', 'building')
                  if str(a.get(k) or '').casefold() not in generic), '')
    if value in ('restaurant', 'hotel', 'cafe', 'pub', 'nightclub', 'bar') or value.startswith(('ravintola', 'hotelli')):
        return 2
    if value in ('office', 'school', 'education', 'university', 'college', 'administrative',
                 '1120', '1130', '1140', '1970') or value.startswith(('toimisto', 'koulu', 'opetus')):
        return 1
    return 0


def lamp_kind(tags, fallback):
    """Explicit lamp technology/temperature overrides a positional colour prior."""
    tags = tags or {}
    method = str(tags.get('light:method', tags.get('lamp_type', ''))).casefold()
    if any(word in method for word in ('sodium', 'hps', 'lps')):
        return 'sodium'
    temperature = str(tags.get('light:colour_temperature', tags.get('colour_temperature', ''))).lower().rstrip('k').strip()
    try:
        if 1800 <= float(temperature) <= 3200:
            return 'warm_led'
    except ValueError:
        pass
    if 'led' in method:
        return 'led'
    return fallback


def lamp_height(tags, fallback):
    value = str((tags or {}).get('height', '')).lower().removesuffix('m').strip()
    try:
        height = float(value)
        return height if 2 <= height <= 16 else fallback
    except ValueError:
        return fallback
