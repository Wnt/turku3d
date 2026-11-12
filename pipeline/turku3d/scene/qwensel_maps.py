"""Generate a reviewed Qwensel elevation using existing AI material parameters."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import math
import struct
import shutil
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw
from turku3d.landmarks.elevations import texture
from turku3d.landmarks.procedural import fieldstone, maps
from .qwensel_details import QWENSEL_FRONTAGE, frontage_length


def frontage_maps(data=QWENSEL_FRONTAGE):
    width,height=frontage_length(data),data['elevationHeight']
    d=data['descriptionParameters'];openings=[]
    for w in data['windows']:
        openings.append({'shape':'rect','x_m':w['along'],'sill_m':w['sill'],'width_m':w['width'],
                         'height_m':w['height'],'columns':w['columns'],'rows':w['rows'],
                         'trim_m':.11,'frame_colour':d['trim_colour'],'colour':[49,61,61]})
    door=data['door']
    openings.append({'shape':'rect','kind':'door','x_m':door['along'],'sill_m':door['sill'],
                     'width_m':door['width'],'height_m':door['height'],'columns':2,'rows':1,
                     'colour':d['trim_colour'],'trim_m':.12})
    row={'width_m':width,'height_m':height,'plinth_m':data['plinthHeight'],'openings':openings}
    generated=[Image.open(io.BytesIO(b)).convert('RGB') for b in texture(json.dumps(d),json.dumps(row))]
    # Retain the existing AI rubble-stone family rather than replacing it with
    # the elevation helper's flat plinth colour. Reference pixels are not inputs.
    nx,ny=generated[0].size;stone_h=round(ny*data['plinthHeight']/height)
    stone=maps(*fieldstone(width,height,d['stone'],17),width,height)
    for image,blob in zip(generated,stone):
        source=Image.open(io.BytesIO(blob)).convert('RGB')
        image.paste(source.crop((0,ny-stone_h,nx,ny)),(0,ny-stone_h))
    # Opaque panelled leaves: the observed entrance has no upper glazing.
    draw=ImageDraw.Draw(generated[0]);sx,sy=nx/width,ny/height
    for leaf in range(2):
        x=door['along']-door['width']/2+leaf*door['width']/2
        for low,h in ((.15,.66),(.94,1.10)):
            rect=((x+.09)*sx,(height-door['sill']-low-h)*sy,
                  (x+door['width']/2-.09)*sx,(height-door['sill']-low)*sy)
            draw.rectangle(rect,fill=(184,191,186),outline=(224,228,222),width=2)
    # Basis transcoding selects block-compressed GPU formats. WebGPU requires
    # the base extent to align to 4x4 blocks; power-of-two extents also give a
    # conventional full mip chain. Resampling retains the same metric UV domain.
    size=tuple(2**math.ceil(math.log2(max(4,n))) for n in generated[0].size)
    return [image.resize(size,Image.Resampling.LANCZOS) for image in generated]


def write_maps(directory,data=QWENSEL_FRONTAGE):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    tool=shutil.which('toktx') or str(Path.home()/'turku3d/tools/ktx-4.4.2/usr/local/bin/toktx')
    records={}
    for name,image in zip(('albedo','normal','orm'),frontage_maps(data)):
        png=directory/f'qwensel-front-{name}.png';image.save(png)
        temporary=directory/f'qwensel-front-{name}.ktx2'
        options=['--t2','--genmipmap','--threads','1','--assign_oetf','srgb' if name=='albedo' else 'linear',
                 '--assign_primaries','bt709' if name=='albedo' else 'none','--encode','etc1s' if name=='albedo' else 'uastc']
        options+=['--qlevel','180','--clevel','1'] if name=='albedo' else ['--uastc_quality','2','--zcmp','18']
        subprocess.run([tool,*options,str(temporary),str(png)],check=True,capture_output=True)
        encoded=temporary.read_bytes();encoded_size=struct.unpack_from('<II',encoded,20)
        if encoded_size!=image.size or any(n%4 for n in encoded_size):
            raise ValueError('Qwensel compressed texture extent is not GPU block-aligned')
        digest=hashlib.sha256(encoded).hexdigest();target=directory/f'{digest}.ktx2'
        temporary.replace(target);records[name]={'sha256':digest,'path':str(target.resolve()),'uri':f'../shared/{digest}.ktx2','width':image.width,'height':image.height}
    metadata={'sourceGlbSha256':data['authoredGlbSha256'],'frontageDataSha256':hashlib.sha256(Path(__file__).with_name('qwensel_frontage.json').read_bytes()).hexdigest(),'maps':records}
    (directory/'maps.json').write_text(json.dumps(metadata,indent=2));return metadata


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('directory');args=parser.parse_args()
    print(json.dumps(write_maps(args.directory)))
