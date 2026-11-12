"""Qualified source cues for reusing the city's facade grammar on unseen blocks.

This adapter does not author a facade or replace an approved program. Geometry
matching establishes identity; mapped attributes remain priors, not observations
of window counts, ornamental details, or calibrated paint. Raw source geometry
and imagery are never part of its serializable result.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math

MATERIALS = {'wood':'wood','plaster':'render','render':'render','brick':'brick',
             'bricks':'brick','concrete':'concrete','stone':'stone','granite':'stone',
             'metal':'metal','metal_plates':'metal','glass':'glass'}
CUE_TAGS = ('building:cladding','building:material','building:colour',
            'building:levels','roof:levels','roof:shape','roof:material',
            'start_date','construction_year','building','building:use')


def footprint_match(footprint, candidates):
    """Choose a unique outline, requiring substantial agreement in both directions.

    Candidates contain ``id``, ``geometry`` and ``tags``. Thresholds are matching
    policy, not confidence probabilities. An equally plausible outline abstains.
    """
    import shapely
    outline=shapely.make_valid(shapely.force_2d(footprint))
    if outline.area<=0:raise ValueError('A positive-area building footprint is required')
    ranked=[]
    for candidate in candidates:
        geom=shapely.make_valid(shapely.force_2d(candidate['geometry']))
        if geom.geom_type=='LineString' and geom.is_ring:geom=shapely.Polygon(geom)
        if geom.area<=0:continue
        area=outline.intersection(geom).area
        if area<=0:continue
        metrics={'iou':area/outline.union(geom).area,'buildingCoverage':area/outline.area,
                 'candidateCoverage':area/geom.area}
        ranked.append((metrics['iou'],str(candidate['id']),metrics,candidate))
    ranked.sort(key=lambda r:(-r[0],r[1]))
    # Cached OSM extracts can repeat one object for several source rows. Identity
    # duplicates are not independent competing buildings; conflicting cues abstain.
    identities={}
    for row in ranked:
        identity=row[1]
        if identity in identities:
            left={k:identities[identity][3]['tags'].get(k) for k in CUE_TAGS}
            right={k:row[3]['tags'].get(k) for k in CUE_TAGS}
            if left!=right:
                return {'accepted':False,'id':identity,'reason':'conflicting records for one source identity'}
        else:identities[identity]=row
    ranked=list(identities.values())
    if not ranked:return {'accepted':False,'reason':'no overlapping outline'}
    _,identity,metrics,source=ranked[0]
    result={'accepted':False,'id':identity,'metrics':metrics}
    if metrics['iou']<.75 or min(metrics['buildingCoverage'],metrics['candidateCoverage'])<.8:
        return {**result,'reason':'insufficient two-way footprint agreement'}
    if len(ranked)>1 and ranked[0][0]-ranked[1][0]<.15:
        return {**result,'reason':'ambiguous overlapping outlines','runnerUp':ranked[1][1]}
    return {**result,'accepted':True,'tags':{k:source['tags'][k] for k in CUE_TAGS if k in source['tags']}}


def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def qualify_facade(attributes, match=None, *, approved_program=None, existing_description=None):
    """Fill missing source attributes and expose the existing reusable archetype.

    Explicit register cladding/storeys and retained AI/program fields are never
    overwritten. Fractional OSM levels remain a cue for the existing shell-height
    resolver; they are not rounded into a fabricated count here.
    """
    from turku3d.facade_kit import renderer
    renderer()  # existing import-path/bootstrap contract for turkutex
    from turkutex.facade_archetypes import archetype, parsed_year

    attrs=deepcopy(attributes);cues={};tags=(match or {}).get('tags',{}) if (match or {}).get('accepted') else {}
    def cue(key,value,source):
        cues[key]={'value':value,'source':source,'support':.4,'status':'mapped prior'}
        if attrs.get(key) in (None,'','unknown'):attrs[key]=value
    identity=(match or {}).get('id','unknown')
    label='OSM '+identity+'; unique footprint match'
    # Cladding describes the external surface more directly than structural material.
    material=tags.get('building:cladding') or tags.get('building:material')
    if material in MATERIALS:cue('material_class',MATERIALS[material],label)
    for source,target in (('roof:shape','roof_shape'),('roof:material','roof_material')):
        if tags.get(source):cue(target,tags[source],label)
    year=parsed_year(tags.get('start_date') or tags.get('construction_year'))
    if year:cue('construction_year',year,label)
    raw_levels=tags.get('building:levels')
    if raw_levels is not None:
        try:levels=float(raw_levels)
        except (TypeError,ValueError):levels=math.nan
        if math.isfinite(levels) and 0<levels<=100:
            cues['mapped_levels']={'value':levels,'source':label,'support':.4,'status':'mapped prior; may include attic convention'}
            if levels.is_integer() and not attrs.get('levels'):
                attrs['levels']=int(levels);attrs['levels_source']='osm'
    if tags.get('building:colour'):
        cues['paint_family']={'value':tags['building:colour'],'source':label,'support':.4,
                              'status':'uncalibrated named/encoded colour cue; not photometric truth'}
    if not attrs.get('osm_use') and (tags.get('building:use') or tags.get('building')):
        cue('osm_use',tags.get('building:use') or tags['building'],label)
    retained={}
    for name,value in (('approvedProgram',approved_program),('existingDescription',existing_description)):
        if value is not None:retained[name]={'sha256':_hash(value),'policy':'preserve unchanged; same-wall supported fields retain authority'}
    return {'attributes':attrs,'cues':cues,'match':deepcopy(match),'archetype':archetype(attrs),
            'archetypeStatus':'existing architectural prior, not an observed facade',
            'retained':retained,'constraints':{'inferExactOpenings':False,'copyHeroOrnaments':False,
                'promoteColourToMeasurement':False,'unknownMaterial':attrs.get('material_class') in (None,'','unknown')}}
