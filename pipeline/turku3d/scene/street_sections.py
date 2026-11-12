"""Pinned measured street sections and registered surface finishes.

The source adapter owns the fit. This compiler only partitions its declared
rails and grades shared vertices, so render and collision share every curb.
"""
import hashlib
import pickle
from pathlib import Path

import numpy as np
import shapely
from turku3d.coarse.street_profiles import RAILS, REACH, grade
from turku3d.coarse.street_materials import street_finish, FIELDSTONE, BRICK, CONCRETE_PAVERS

FINISH_ALIASES = {
    'curb': 'draft_granite_curb',
    'concrete-pavers': 'draft_concrete_pavers',
    'brick-paving': 'draft_brick_paving',
    'fieldstone': 'draft_fieldstone',
    'gravel': 'draft_gravel',
}
PALETTE = {
    'curb': ([.34,.32,.29],.9,0),
    'concrete-pavers': ([.39,.37,.33],.9,0),
    'brick-paving': ([.35,.19,.13],.9,0),
    'fieldstone': ([.35,.33,.30],.9,0),
    'gravel': ([.30,.27,.22],.95,0),
}
PHYSICS = {'concrete-pavers':'paving_slab','brick-paving':'paving_slab','fieldstone':'cobblestone'}


def freeze(output,bounds,*,exclude_bounds=()):
    """Freeze register finishes and raw-return fits in an owned evidence cache.

    Exclusion rectangles preserve an already accepted pilot while adjoining
    streets gain their own measured sections. Source stations stay canonical.
    """
    import geopandas as gpd
    from turku3d import config as C
    from turku3d.ground.inputs import laz_paths,read_returns
    from .ground_survey import ground_returns
    from turku3d.coarse.street_survey import shared_lines,measure
    bounds=list(bounds);window=shapely.box(*bounds)
    extension=window.difference(shapely.union_all([shapely.box(*b) for b in exclude_bounds]))
    path=C.raw_dir('wfs')/'work-3x3/GIS_Katualueet.parquet'
    frame=gpd.read_parquet(path);frame=frame[frame.geometry_local.intersects(extension.buffer(8))]
    rows=[(shapely.force_2d(shapely.make_valid(r.geometry_local)),r) for _,r in frame.iterrows()]
    lines=shared_lines(rows,extension);box=C.BBoxLocal(*bounds).buffered(12)
    paths=laz_paths(box);raw,classes=read_returns(paths,box,[2,12]);points=ground_returns(raw,classes)
    corridor=shapely.union_all([line.buffer(1.7) for _,line in lines])
    points=points[shapely.intersects_xy(corridor,points[:,0],points[:,2])]
    profiles=[]
    for p in measure(lines,points,extension):
        for segment in shapely.get_parts(p['line'].intersection(extension)):
            if segment.geom_type!='LineString' or segment.length<2:continue
            coords=np.asarray(segment.coords)
            stations=np.array([p['line'].project(shapely.Point(x)) for x in coords])
            profiles.append(dict(key=p['key'],line=segment,
                knots=np.r_[0,np.cumsum(np.linalg.norm(np.diff(coords,axis=0),axis=1))],
                heights=np.stack([np.interp(stations,p['knots'],p['heights'][:,i]) for i in (0,1)],axis=1)))
    value=dict(bounds=bounds,profiles=profiles,
        finishes=[dict(id=str(r.feature_key),name=str(r.Kadunnimi),kind=str(r.Katuosalaji),
            finish=str(r.Pintamateriaali),geometry=g.intersection(extension)) for g,r in rows if g.intersects(extension)],
        sources=[dict(name=p.name,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in [path,*paths]])
    Path(output).parent.mkdir(parents=True,exist_ok=True)
    Path(output).write_bytes(pickle.dumps(value,protocol=pickle.HIGHEST_PROTOCOL))
    return dict(sourceLines=len(lines),profiles=len(profiles),stations=sum(len(p['knots']) for p in profiles),
                lengthM=sum(p['line'].length for p in profiles),sourcePoints=len(points))


def load(cache,bounds):
    path=Path(cache)/'street-survey.pkl'
    if not path.exists():return {},[]
    raw=path.read_bytes();value=pickle.loads(raw)
    if value['bounds']!=list(bounds):raise ValueError('Street survey does not match prepared bounds')
    for profile in value['profiles']:
        knots=np.asarray(profile['knots']);heights=np.asarray(profile['heights'])
        if (len(knots)<2 or heights.shape!=(len(knots),2) or not np.isfinite(heights).all()
                or not np.all(np.diff(knots)>0) or np.any(heights[:,1]<=heights[:,0])):
            raise ValueError('Street section must have ordered stations and a measured high side')
    return value,[dict(id=path.name,sha256=hashlib.sha256(raw).hexdigest(),kind='measured-street-sections',uncertaintyM=.03)]


def finish(name):
    classified=street_finish(name)
    if classified in (FIELDSTONE,BRICK,CONCRETE_PAVERS):
        return {FIELDSTONE:'fieldstone',BRICK:'brick-paving',CONCRETE_PAVERS:'concrete-pavers'}[classified]
    text=name.casefold()
    if 'asfalt' in text:return 'road'
    if 'noppakivi' in text or 'luonnonkivi' in text:return 'cobblestone'
    if 'sora' in text:return 'gravel'
    return None


def regions(section,dry):
    result=[]
    for row in section.get('finishes',[]):
        material=finish(row['finish'])
        if material is not None:
            kind='road' if row['kind']=='Ajorata' else 'footway'
            result.append((row['geometry'].intersection(dry),kind,material))
    # The narrow surveyed cap and face have a granite material, independent of
    # the carriageway or pavement on either side.
    for p in section.get('profiles',[]):
        cap=p['line'].buffer(.18,single_sided=True,join_style='mitre').union(
            p['line'].buffer(-.015,single_sided=True,join_style='mitre'))
        result.append((cap.intersection(dry),'footway','curb'))
    return [(shapely.set_precision(g,1e-5),k,m) for g,k,m in result if not g.is_empty]


def boundaries(section,dry):
    result=[]
    for patch in section.get('profiles',[]):
        line=patch['line']
        for width in RAILS:
            result.append(line.buffer(width,single_sided=True,join_style='mitre').boundary.intersection(dry))
        for station in np.unique(np.r_[patch['knots'],.5,line.length-.5]):
            centre=np.asarray(line.interpolate(station).coords[0])
            tangent=np.asarray(line.interpolate(min(line.length,station+.05)).coords[0])-np.asarray(line.interpolate(max(0,station-.05)).coords[0])
            tangent/=max(np.linalg.norm(tangent),1e-12);normal=np.array([-tangent[1],tangent[0]])
            result.append(shapely.LineString([centre-normal*(REACH+.1),centre+normal*(REACH+.1)]).intersection(dry))
    return result


def height(section,xz,y):
    if not section.get('profiles'):return y
    return grade(np.c_[xz[:,0],y,xz[:,1]],section['profiles'])[:,1]


if __name__=='__main__':
    import argparse,json
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--bounds',type=float,nargs=4,required=True)
    parser.add_argument('--exclude-bounds',type=float,nargs=4,action='append',default=[])
    args=parser.parse_args()
    print(json.dumps(freeze(args.output,args.bounds,exclude_bounds=args.exclude_bounds),indent=2))
