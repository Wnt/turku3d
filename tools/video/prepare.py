"""Clone immutable published tile assets into the task sandbox; build no shared output."""
import json
import os
import shutil
import subprocess
from pathlib import Path

work=Path(os.environ['TURKU3D_WORK'])
source=(Path(os.environ['TURKU3D_DATA'])/'live/tiles').resolve()
target=work/'dist/tiles'
if target.is_symlink():
    raise SystemExit('Refusing a symlink target')
shutil.copytree(source,target,dirs_exist_ok=True)
root=work/'video'
for name in ('assets','shots','audio','edit','review'):(root/name).mkdir(parents=True,exist_ok=True)
(root/'provenance.json').write_text(json.dumps({
    'game_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
    'tile_release':str(source.parent),'tile_source':'immutable published pipeline assets cloned into this sandbox; own production game build',
},indent=2)+'\n')
print(f'Cloned {source} -> {target}')
