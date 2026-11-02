#!/usr/bin/env python3
"""Read immutable proof metadata and compare source keys, payload hashes, sizes and counts."""
import argparse
import json
from pathlib import Path

FILES=('manifest','ready','input-cache','stage-times')


def snapshot(root,generation,tiles):
    return {tile:{name:json.loads(path.read_text()) for name in FILES
                  if (path:=root/'products'/generation/tile/(name+'.json')).exists()} for tile in tiles}


def keys(row,name):
    return {key:value['key'] for key,value in row.get(name,{}).items()
            if isinstance(value,dict) and 'key' in value}


def payloads(manifest):
    result={kind+'/'+key:{field:value.get(field) for field in ('sha256','bytes','tris')}
            for kind in ('visual','collision','vehicle_models')
            for key,value in manifest.get(kind,{}).items()}
    if manifest.get('roads'):
        result['roads/graph']={field:manifest['roads'].get(field) for field in ('sha256','bytes','nodes','edges')}
    return result


def compare(before,after):
    result={}
    for tile in sorted(before.keys()|after.keys()):
        if tile not in before or tile not in after:
            result[tile]={'missing_from':'before' if tile not in before else 'after'};continue
        a,b=before[tile],after[tile];ma,mb=a['manifest'],b['manifest']
        pa,pb=payloads(ma),payloads(mb)
        changed={name:sorted(k for k in keys(a,name).keys()|keys(b,name).keys()
                            if keys(a,name).get(k)!=keys(b,name).get(k))
                 for name in ('input-cache','stage-times')}
        av,bv=ma['visual'][tile],mb['visual'][tile]
        collisions_a,collisions_b=ma.get('collision',{}),mb.get('collision',{})
        result[tile]=dict(same_producer=ma.get('code')==mb.get('code'),
            producer_before=ma.get('code'),producer_after=mb.get('code'),
            same_farm_generation=a['ready']['generation']==b['ready']['generation'],
            generation_before=a['ready']['generation'],generation_after=b['ready']['generation'],
            changed_input_keys=changed['input-cache'],changed_stage_keys=changed['stage-times'],
            all_payload_hashes_equal=pa.keys()==pb.keys() and all(pa[k]['sha256']==pb[k]['sha256'] for k in pa),
            changed_payloads=[dict(key=k,before=pa.get(k),after=pb.get(k)) for k in sorted(pa.keys()|pb.keys()) if pa.get(k)!=pb.get(k)],
            visual={field:[av.get(field),bv.get(field)] for field in ('bytes','tris','brotli_bytes','sha256')},
            collision_cells=[len(collisions_a),len(collisions_b)],
            collision_bytes=[sum(v['bytes'] for v in collisions_a.values()),sum(v['bytes'] for v in collisions_b.values())],
            geometry_counts={k:[ma.get(k),mb.get(k)] for k in ('kerb_triangles','street_profiles','stairs','walkable','gardens','boats','marking_triangles','rule_paint')},
            build_seconds=[a['ready'].get('seconds'),b['ready'].get('seconds')])
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    snap=sub.add_parser('snapshot');snap.add_argument('root',type=Path);snap.add_argument('generation');snap.add_argument('tiles',nargs='+')
    diff=sub.add_parser('compare');diff.add_argument('before',type=Path);diff.add_argument('after',type=Path)
    a=p.parse_args()
    result=snapshot(a.root,a.generation,a.tiles) if a.action=='snapshot' else compare(json.loads(a.before.read_text()),json.loads(a.after.read_text()))
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
