"""Resolve content-addressed source GLB buffers into one embedded buffer."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import urllib.parse
import urllib.request
import numpy as np


def encode_glb(document,binary):
    encoded=json.dumps(document,separators=(',',':')).encode();encoded+=b' '*((-len(encoded))%4)
    binary+=b'\0'*((-len(binary))%4)
    return struct.pack('<III',0x46546c67,2,28+len(encoded)+len(binary))+struct.pack('<II',len(encoded),0x4e4f534a)+encoded+struct.pack('<II',len(binary),0x004e4942)+binary


def embedded_buffers(document,binary,base_url,fetch=None):
    """Preserve meshopt fallback declarations while resolving every real buffer."""
    document=copy.deepcopy(document);chunks=[];offsets={};size=0;fallback={}
    if fetch is None:fetch=lambda url:urllib.request.urlopen(url,timeout=30).read()
    for i,buffer in enumerate(document.get('buffers',[])):
        if buffer.get('extensions',{}).get('EXT_meshopt_compression',{}).get('fallback'):
            fallback[i]=len(fallback)+1;continue
        raw=binary if 'uri' not in buffer else fetch(urllib.parse.urljoin(base_url,buffer['uri']))
        if 'uri' in buffer and hashlib.sha256(raw).hexdigest()!=Path(buffer['uri']).stem:raise ValueError('Source buffer hash mismatch')
        if len(raw)<buffer['byteLength']:raise ValueError('Truncated source buffer')
        raw=raw[:buffer['byteLength']];offsets[i]=size;chunks.append(raw);size+=len(raw)
        pad=(-size)%4;chunks.append(b'\0'*pad);size+=pad
    for view in document.get('bufferViews',[]):
        index=view['buffer']
        if index in fallback:view['buffer']=fallback[index]
        else:view.update(buffer=0,byteOffset=offsets[index]+view.get('byteOffset',0))
        extension=view.get('extensions',{}).get('EXT_meshopt_compression')
        if extension:
            index=extension['buffer'];extension.update(buffer=0,byteOffset=offsets[index]+extension.get('byteOffset',0))
    originals=document['buffers'];document['buffers']=[dict(byteLength=size)]+[originals[index] for index in fallback]
    return document,b''.join(chunks)


def node_world_matrices(document):
    """glTF parent transforms, including rotation/scale, exactly once per node."""
    from scipy.spatial.transform import Rotation
    parents={child:i for i,node in enumerate(document['nodes']) for child in node.get('children',[])}
    matrices={}
    def world(i):
        if i in matrices:return matrices[i]
        node=document['nodes'][i]
        if 'matrix' in node:local=np.asarray(node['matrix']).reshape(4,4).T
        else:
            local=np.eye(4);local[:3,:3]=Rotation.from_quat(node.get('rotation',[0,0,0,1])).as_matrix()@np.diag(node.get('scale',[1,1,1]));local[:3,3]=node.get('translation',[0,0,0])
        matrices[i]=world(parents[i])@local if i in parents else local
        return matrices[i]
    for i in range(len(document['nodes'])):world(i)
    return matrices
