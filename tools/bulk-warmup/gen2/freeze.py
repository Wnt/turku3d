#!/usr/bin/env python3
"""Host: freeze completed inputs as portable hard links and a content-addressed inventory."""
import argparse
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import xml.etree.ElementTree as ET
from recipe import acquisition_recipe

# derived/ (playgrounds) and landmark-descriptions/ are the farm's source overlay: AI facade
# and landmark descriptions must survive into every city run.
INPUTS=('raw','textures','facade-assets','facade-params','facade-approvals',
        'facade-characterization','supplements','regional','derived','landmark-descriptions')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def portable_vrts(data):
    for path in (data/'raw/ortho').rglob('*.vrt'):
        tree=ET.parse(path);changed=False
        for node in tree.iter('SourceFilename'):
            if not node.text:continue
            target=Path(node.text) if os.path.isabs(node.text) else path.parent/node.text
            if not target.resolve().is_relative_to(data.resolve()) or not target.is_file():
                raise RuntimeError('missing or external VRT dependency: '+str(target))
            if os.path.isabs(node.text):
                node.text=os.path.relpath(target,path.parent);node.set('relativeToVRT','1');changed=True
        if changed:
            temporary=path.with_suffix('.vrt.tmp');tree.write(temporary,encoding='utf-8');temporary.replace(path)


def inventory(data):
    """Reuse checksum-verified raw download ledgers; hash wrappers and authored data."""
    recorded={}
    for manifest in (data/'raw').glob('*/MANIFEST.jsonl'):
        for line in manifest.read_text().splitlines():
            row=json.loads(line)
            recorded['raw/'+manifest.parent.name+'/'+row['path']]=row
    result={}
    for name in INPUTS:
        for directory,_,files in os.walk(data/name,followlinks=True):
            for name in sorted(files):
                if name.startswith('.') or name.endswith(('.part','.tmp','.lock')):
                    continue
                source=Path(directory)/name
                relative=source.relative_to(data).as_posix()
                stat=source.stat();old=recorded.get(relative,{})
                # VRTs were relocated and JSON wrappers may carry changed source metadata.
                cached=(source.suffix not in ('.vrt','.json','.jsonl')
                        and old.get('bytes')==stat.st_size and len(old.get('sha256',''))==64)
                result[relative]=dict(bytes=stat.st_size,sha256=old['sha256'] if cached else digest(source))
    return dict(sorted(result.items()))


def freeze(root):
    state=root/'state';farm=root/'farm'
    with ExitStack() as stack:
        for name in ('prefetch','bulk'):
            handle=stack.enter_context((state/(name+'.lock')).open('a'))
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        coverage=json.loads((state/'PREFETCH_COMPLETE.json').read_text())
        if coverage.get('failures') or coverage.get('missing_tiles'):
            raise RuntimeError('acquisition is incomplete')
        acquired=json.loads((state/'acquisition.json').read_text())
        if acquired['recipe']!=acquisition_recipe(root/'repo') or coverage.get('recipe',acquired['recipe'])!=acquired['recipe']:
            raise RuntimeError('acquisition recipe changed; run prefetch again')
        jobs=state/'prefetch-jobs.json'
        if coverage['jobs_sha256']!=digest(jobs):
            raise RuntimeError('prefetch plan changed')
        regional=root/'data/regional'
        if not regional.exists():regional.symlink_to('../regional-city',target_is_directory=True)
        portable_vrts(root/'data')
        files=inventory(root/'data')
        encoded=json.dumps(files,sort_keys=True,separators=(',',':')).encode()
        version=hashlib.sha256(encoded).hexdigest()
        target=farm/'sources/versions'/version
        if not target.exists():
            temporary=target.with_name('.freeze-'+version)
            temporary.mkdir(parents=True,exist_ok=True)
            for relative,row in files.items():
                source=root/'data'/relative;dest=temporary/relative
                dest.parent.mkdir(parents=True,exist_ok=True)
                if not dest.exists():os.link(source.resolve(),dest)
                if dest.stat().st_size!=row['bytes']:
                    raise RuntimeError('source changed during freeze: '+relative)
            (temporary/'source-version.json').write_text(json.dumps(dict(version=version,files=files),sort_keys=True)+'\n')
            temporary.rename(target)
        report=dict(recipe=acquisition_recipe(root/'repo'),version=version,data_root=str(target),files=len(files),bytes=sum(r['bytes'] for r in files.values()),
                    by_group={name:sum(r['bytes'] for p,r in files.items() if p.startswith(name+'/')) for name in INPUTS},
                    at=time.time(),walk_sha256=digest(state/'walk.json'),prefetch_jobs_sha256=coverage['jobs_sha256'])
        (state/'sources.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report))
        return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path)
    freeze(p.parse_args().root.resolve())
