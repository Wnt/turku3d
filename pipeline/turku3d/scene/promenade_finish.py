"""Small reference-registered promenade finish; no repeating invented street grid."""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import numpy as np
import shapely
from .surface_inlays import drape
from .compile import release_hash
from .schema import validate_scene

# Positions are authored estimates from two registered 2019 views, with the
# permanent asphalt/sett/drain arrangement corroborated in 2023/2024 photographs.
# Widths, individual joints and colour are construction/lookdev priors.
LAYOUT={
    'version':'promenade-finish/2',
    'id':'office-promenade-observed-finish',
    'uncertaintyM':.8,
    'referenceHeightM':3.7,
    'heightToleranceM':.35,
    'drain':{'endpoints':[[151.72,266.21],[185.68,247.16]],'observedSegment':[[169.84,256.04],[173.91,253.76]],'surroundWidthM':.60,'gratingWidthM':.10},
    'crossBand':{'endpoints':[[172.55,254.52],[177.94,264.13]],'observedSegment':[[172.55,254.52],[175.70,260.14]],'mappedBoundary':'osm:way:1049978299:0','boundaryIntersectionXZ':[177.89787706602306,264.05650061671184],'widthM':.60},
    'observed':'One cross-band direction/width and a bounded visible drain segment; main asphalt retained.',
    'inferred':'Cross-band continues to the mapped hedge front edge (0.08m visual overlap); drain continues to retained office facade endpoints. These continuations are inferred, not newly observed endpoints. Map registration +/-0.8m; widths +/-0.15m; 0.12m staggered setts,0.012m joints,0.04m grate pitch and restrained rose-grey palette are priors. No periodic band repetition.',
}


def frame(endpoints):
    a,b=np.asarray(endpoints,float);t=b-a;length=float(np.linalg.norm(t));t/=length
    return a,t,np.array([-t[1],t[0]]),length


def rectangle(a,t,n,start,end,left,right):
    return shapely.Polygon([a+t*start+n*left,a+t*end+n*left,a+t*end+n*right,a+t*start+n*right])


def append_finish(scene,layout=LAYOUT):
    """Append passable visual layers, retaining all existing scene records exactly."""
    if any(m['entityId'].startswith(layout['id']) for m in scene['meshes']):raise ValueError('Finish already applied')
    source=next(m for m in scene['materials'] if m['id']=='curb')
    granite=deepcopy(source);granite['id']='promenade-rose-granite';granite['color']=(np.asarray(source['color'])*[.85,.66,.60]).tolist();granite['surface']='granite'
    materials=[granite,dict(id='promenade-sett-joint',color=[.055,.05,.046],roughness=.95,metalness=0,surface='stone'),dict(id='promenade-drain-dark',color=[.018,.021,.025],roughness=.7,metalness=.8),dict(id='promenade-drain-rib',color=[.075,.083,.09],roughness=.6,metalness=.7)]
    scene['materials'].extend(materials);receipts=[]
    def add(suffix,polygon,material,axes,lift=.002):
        rows,receipt=drape(scene,layout['id']+':'+suffix,polygon,material,reference_height=layout['referenceHeightM'],height_tolerance=layout['heightToleranceM'],optical_lift=lift,uv_axes=axes)
        if receipt['coveredAreaM2']<receipt['requestedAreaM2']*.995:raise ValueError('Uncovered promenade finish: '+suffix)
        scene['meshes'].extend(rows);receipts.append(receipt)
    a,t,n,length=frame(layout['crossBand']['endpoints']);width=layout['crossBand']['widthM'];axes=np.array([t,n])
    add('band-joints',rectangle(a,t,n,0,length,-width/2,width/2),'promenade-sett-joint',axes)
    stones=[];pitch=.12;joint=.012
    for row in range(int(np.ceil(length/pitch))):
        for column in range(-1,6):
            left=-width/2+column*pitch+(row%2)*pitch/2+joint/2;right=min(width/2,left+pitch-joint);left=max(-width/2,left)
            if right<=left:continue
            start=row*pitch+joint/2;end=min(length,(row+1)*pitch-joint/2)
            if end>start:stones.append(rectangle(a,t,n,start,end,left,right))
    add('band-setts',shapely.union_all(stones),'promenade-rose-granite',axes,.003)
    a,t,n,length=frame(layout['drain']['endpoints']);width=layout['drain']['surroundWidthM'];grate=layout['drain']['gratingWidthM'];axes=np.array([t,n])
    surround=rectangle(a,t,n,0,length,-width/2,width/2)
    # Clip the cross-band beneath the drain surround by their small optical order.
    add('drain-surround',surround,'promenade-rose-granite',axes,.0035)
    add('drain-channel',rectangle(a,t,n,0,length,-grate/2,grate/2),'promenade-drain-dark',axes,.004)
    bars=[]
    for s in np.arange(0,length,.04):bars.append(rectangle(a,t,n,float(s),min(length,float(s)+.014),-grate/2,grate/2))
    add('drain-ribs',shapely.union_all(bars),'promenade-drain-rib',axes,.0045)
    raw=json.dumps(layout,sort_keys=True,separators=(',',':')).encode()
    scene['sources'].append(dict(id=layout['id'],sha256=hashlib.sha256(raw).hexdigest(),kind='reference-registered-surface-layout',uncertaintyM=layout['uncertaintyM']))
    scene['coverage']['entityIds']=sorted(set(scene['coverage']['entityIds'])|{m['entityId'] for m in scene['meshes'] if m['entityId'].startswith(layout['id'])})
    scene['release']=release_hash(scene);validate_scene(scene)
    return dict(layout=layout,receipts=receipts,sourceRelease=scene['release'])


def main():
    p=argparse.ArgumentParser();p.add_argument('scene',type=Path);p.add_argument('output',type=Path);args=p.parse_args()
    scene=json.loads(args.scene.read_text());before=deepcopy(scene);receipt=append_finish(scene)
    for name in ('supports','boundaries','collision','fixtures','emitters','assets','spawn','lightOccluders'):
        if scene[name]!=before[name]:raise AssertionError('Changed canonical '+name)
    if scene['meshes'][:len(before['meshes'])]!=before['meshes']:raise AssertionError('Changed original mesh/light fields')
    args.output.mkdir(parents=True,exist_ok=True)
    for row in scene['collision'].values():
        raw=(args.scene.parent/row['url']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('Source collision hash mismatch')
        (args.output/row['url']).write_bytes(raw)
    (args.output/(scene['id']+'.scene.json')).write_text(json.dumps(scene,separators=(',',':'))+'\n')
    receipt.update(inputRelease=before['release'],preservedMeshCount=len(before['meshes']),addedMeshes=len(scene['meshes'])-len(before['meshes']),collisionFiles=len(scene['collision']))
    (args.output/'finish-audit.json').write_text(json.dumps(receipt,indent=2));print(json.dumps({k:receipt[k] for k in ('sourceRelease','inputRelease','preservedMeshCount','addedMeshes','collisionFiles')}))

if __name__=='__main__':main()
