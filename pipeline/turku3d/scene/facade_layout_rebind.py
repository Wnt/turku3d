"""Repair a collapsed facade grid while retaining its exact existing visual mesh.

A corrected normal producer supplies candidate instructions. Identity includes
all nonvertical layout/style/material fields; missing or ambiguous matches fail.
Only the four broken vertical dimensions are rebound on original vertex ranges.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
from turku3d.package.gltf import read_glb

VERTICAL=('storey','ground','upper')


def identity(instruction):
    result={k:v for k,v in instruction.items() if k not in (*VERTICAL,'start','count')}
    result['size']=result['size'][:1]
    return json.dumps(result,sort_keys=True,separators=(',',':'))


def rebind_instructions(original,candidate):
    lookup={}
    for row in candidate:
        lookup.setdefault(identity(row),set()).add((row['size'][1],*(row[k] for k in VERTICAL)))
    output=[];changed=0
    for row in original:
        match=lookup.get(identity(row),set())
        if len(match)!=1:raise ValueError('Missing or ambiguous corrected facade instruction')
        size,storey,ground,upper=next(iter(match));new=deepcopy(row)
        if (row['size'][1],*(row[k] for k in VERTICAL))!=(size,storey,ground,upper):
            if row['storey']>.11:raise ValueError('Refusing to replace a noncollapsed facade grid')
            if not 2.2<=storey<=5.5:raise ValueError('Corrected one-storey envelope is unsupported')
            new['size'][1]=size;new.update(storey=storey,ground=ground,upper=upper);changed+=1
        output.append(new)
    return output,changed


def rebind(source,candidate):
    original,blob=read_glb(source);proposed,_=read_glb(candidate);out=deepcopy(original);changed=0
    nodes={n.get('name'):n for n in proposed['nodes'] if n.get('extras',{}).get('facade_kit_walls')}
    for node in out['nodes']:
        extra=node.get('extras',{});old=extra.get('facade_kit_walls')
        if not old:continue
        candidate_node=nodes.get(node.get('name'))
        if candidate_node is None or candidate_node['extras'].get('building_id')!=extra.get('building_id'):
            raise ValueError('Corrected source belongs to another building')
        rows,count=rebind_instructions(old,candidate_node['extras']['facade_kit_walls']);changed+=count
        extra['facade_kit_walls']=rows
        extra['facade_kit']={**rows[0],'count':extra['facade_kit']['count']}
    if not changed:raise ValueError('No collapsed facade instruction was repaired')
    encoded=json.dumps(out,separators=(',',':')).encode();encoded+=b' '*((-len(encoded))%4)
    binary=blob+b'\0'*((-len(blob))%4)
    result=struct.pack('<III',0x46546c67,2,28+len(encoded)+len(binary))+struct.pack('<II',len(encoded),0x4e4f534a)+encoded+struct.pack('<II',len(binary),0x004e4942)+binary
    return result,{'originalSha256':hashlib.sha256(source).hexdigest(),'candidateSha256':hashlib.sha256(candidate).hexdigest(),'sha256':hashlib.sha256(result).hexdigest(),'changedInstructions':changed,'preservedBinaryBytes':len(blob),'preserved':'all mesh positions/indices/normals/UVs/materials and instruction ranges/styles/colours; only vertical layout dimensions changed'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('candidate',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    result,receipt=rebind(a.source.read_bytes(),a.candidate.read_bytes());a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_bytes(result);print(json.dumps(receipt))
