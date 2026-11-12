"""Apply qualified register/outline cues to the existing facade producer.

Only missing attributes are filled. Shell geometry and approved facade descriptions
stay authoritative; the ordinary facade-program resolver owns all visual priors.
"""
from copy import deepcopy
import hashlib
import json
import math

from .grammar import qualify_facade


def _missing(value):
    return value is None or value == '' or value == 'unknown'


def qualified_buildings(buildings, inventory):
    """Return copied buildings and serializable source/authority receipts.

    The private inventory contains stable IDs, qualified municipal attributes and
    independently matched OSM outlines. A conflicting known attribute is retained
    and reported, never silently replaced. Multiple source shell parts may share
    one facade identity; each gets the same qualified missing fields.
    """
    rows={}
    for row in inventory['buildings']:
        identity=str(row['buildingId'])
        if identity in rows:raise ValueError('Duplicate qualified building identity: '+identity)
        rows[identity]=row
    output=[];receipts=[]
    for original in buildings:
        identity=str(original.facade_key)
        row=rows.get(identity)
        if row is None:
            raise ValueError('Building has no qualified inventory record: '+identity)
        building=deepcopy(original)
        attrs=deepcopy(getattr(original,'attributes',{}));before=deepcopy(attrs)
        register=deepcopy(row.get('registerEvidence',{}))
        if not _missing(row.get('year')):register.setdefault('construction_year',row['year'])
        try:levels=float(row.get('levels'))
        except (TypeError,ValueError):levels=math.nan
        if math.isfinite(levels) and levels.is_integer() and 0<levels<=100:
            register.setdefault('levels',int(levels))
        conflicts={}
        for key,value in register.items():
            if _missing(value):continue
            if _missing(attrs.get(key)):
                attrs[key]=value
                if key=='levels':attrs['levels_source']='register'
            elif attrs[key]!=value:conflicts[key]={'retained':attrs[key],'incoming':value}
        qualified=qualify_facade(attrs,row['grammar'].get('match'))
        building.attributes=qualified['attributes']
        output.append(building)
        receipts.append({'buildingId':identity,'sourceKey':str(original.key),
            'inputAttributes':before,'outputAttributes':deepcopy(building.attributes),
            'knownAttributeConflicts':conflicts,'archetype':qualified['archetype'],
            'archetypeStatus':qualified['archetypeStatus'],'cues':qualified['cues'],
            'unknownMaterial':qualified['constraints']['unknownMaterial'],
            'approvedProgramPolicy':'existing resolver and same-wall authority unchanged',
            'inventoryRecordSha256':hashlib.sha256(json.dumps(row,sort_keys=True,separators=(',',':')).encode()).hexdigest()})
    return output,receipts
