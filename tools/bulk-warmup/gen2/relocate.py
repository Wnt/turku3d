#!/usr/bin/env python3
"""Relocate copied farm overlay links and raster references on the owned staging host."""
import argparse
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);a=p.parse_args();root=a.root.resolve()
replacements={
 '/home/wnt/turku3d-local/loops/tiles/farm/sources': str(root/'farm/sources'),
 '/home/wnt/turku3d-local/combined/data':str(root/'data'),
 '/home/wnt/turku3d-bulk-warmup/data':str(root/'data'),
 '/srv/turku3d/data':str(root/'data'),
 '/home/ubuntu/turku3d-drafts/data':str(root/'data'),
}
links=0;broken=[]
for directory,dirs,files in os.walk(root/'farm/sources',followlinks=False):
 for name in [*dirs,*files]:
  path=Path(directory)/name
  if not path.is_symlink(): continue
  target=os.readlink(path)
  for old,new in replacements.items():
   if target==old or target.startswith(old+'/'):
    path.unlink();path.symlink_to(os.path.relpath(new+target[len(old):],path.parent));links+=1;break
  if not path.exists(): broken.append(str(path.relative_to(root)))
rasters=0;missing=[]
for path in (root/'data/raw/ortho').rglob('*.vrt'):
 tree=ET.parse(path);changed=False
 for node in tree.iter('SourceFilename'):
  target=node.text
  if not target:continue
  if os.path.isabs(target):
   for old,new in replacements.items():
    if target.startswith(old+'/'):target=new+target[len(old):];break
   if not Path(target).is_file():missing.append(dict(vrt=str(path),source=target));continue
   node.text=os.path.relpath(target,path.parent);node.set('relativeToVRT','1');changed=True
 if changed: tree.write(path,encoding='utf-8');rasters+=1
report=dict(relocated_links=links,unresolved_links=broken,portable_vrts=rasters,missing_vrt_sources=missing)
(root/'state/relocation.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:len(v) if isinstance(v,list) else v for k,v in report.items()}))
if broken or missing:raise SystemExit(1)
