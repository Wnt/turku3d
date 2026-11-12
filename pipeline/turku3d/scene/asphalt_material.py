"""Encode the pinned, reference-qualified worn-asphalt albedo repair.

The image tool owns raster appearance. This adapter only resamples to a GPU-safe
extent and encodes sRGB; it never repaints, flattens or adjusts image contrast.
The library's two-metre repeat and existing normal/ORM products stay separate.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
from PIL import Image
from .asset_store import resolve_asset

SOURCE_SHA='210ef8a9b9edf0dd789c13310165a60770eee168da0ec110a1621395b87ec19e'
SIZE=(1024,1024)


def produce(source,output):
    source=resolve_asset(source);raw=source.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=SOURCE_SHA:raise ValueError('Unqualified asphalt source image')
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    with Image.open(source) as image:
        if image.size!=(1254,1254):raise ValueError('Unexpected asphalt source extent')
        image.convert('RGB').resize(SIZE,Image.Resampling.LANCZOS).save(output/'albedo.png')
    tool=shutil.which('toktx') or str(Path.home()/'turku3d/tools/ktx-4.4.2/usr/local/bin/toktx')
    target=output/'albedo.ktx2'
    subprocess.run([tool,'--t2','--genmipmap','--threads','1','--assign_oetf','srgb',
                    '--assign_primaries','bt709','--encode','etc1s','--qlevel','180','--clevel','1',
                    str(target),str(output/'albedo.png')],check=True,capture_output=True)
    data=target.read_bytes();extent=struct.unpack_from('<II',data,20)
    if extent!=SIZE:raise ValueError('Unexpected asphalt KTX2 extent')
    sha=hashlib.sha256(data).hexdigest();final=output/(sha+'.ktx2');target.replace(final)
    result=dict(sourceSha256=SOURCE_SHA,extentMetres=[2,2],extent=list(SIZE),bytes=len(data),
                texture=dict(url='shared/'+final.name,sha256=sha,wrap='repeat'),
                processing='RGB conversion; Lanczos resize1254 to1024; sRGB ETC1S encoding; no tonal edits')
    (output/'material.json').write_text(json.dumps(result,indent=2)+'\n');return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();print(json.dumps(produce(args.source,args.output),indent=2))
