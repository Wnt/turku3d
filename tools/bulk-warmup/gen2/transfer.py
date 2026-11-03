#!/usr/bin/env python3
"""Pull frozen sources or validated farm products to osgallery-dev; ack before pruning.

Run on the receiving machine. First use an owned staging farm root to prove adoption.
The sources mode receives the immutable profile without writing combined/data/raw.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import struct
import tempfile
import sys
import time

HEX=re.compile(r'[a-f0-9]{64}\Z')
REV=re.compile(r'[a-f0-9]{40,64}\Z')
TILE=re.compile(r't0_-?\d+_-?\d+\Z')


def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX)
        yield


def atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp-'+str(os.getpid()))
    temporary.write_text(json.dumps(value,sort_keys=True)+'\n');temporary.replace(path)


def validate_sources(directory,expected,*,raw=True):
    manifest=json.loads((directory/'source-version.json').read_text())
    if manifest['version']!=expected:raise ValueError('source version mismatch')
    content=json.dumps(manifest['files'],sort_keys=True,separators=(',',':')).encode()
    if hashlib.sha256(content).hexdigest()!=expected:raise ValueError('source inventory hash mismatch')
    for relative,row in manifest['files'].items():
        path=directory/relative
        if not path.resolve().is_relative_to(directory.resolve()):raise ValueError('source outside profile')
        if not raw and relative.startswith('raw/'):continue
        if path.stat().st_size!=row['bytes'] or digest(path)!=row['sha256']:
            raise ValueError('source hash mismatch: '+relative)
    return manifest


def references(value):
    """Every relative path-like string in a manifest or JSON payload, at any depth."""
    if isinstance(value,dict):
        for v in value.values():yield from references(v)
    elif isinstance(value,list):
        for v in value:yield from references(v)
    elif (isinstance(value,str) and 0<len(value)<512 and '.' in value
          and not value.startswith(('data:','http:','https:','/'))):
        yield value


def publication(root,tile):
    """Published payloads and small provenance reports, with every reachable dependency.

    Walks every relative reference in the manifest (visual, collision, roads, vehicle and
    building models, playgrounds, piers, ... and any section added later) and in the JSON and
    GLB files it reaches, so each ../shared/ asset a manifest names travels with the tile.
    """
    root=Path(root).resolve();product=root/tile
    files={}
    def visit(path):
        path=path.resolve()
        if not path.is_relative_to(root):raise ValueError('payload outside generation')
        relative=str(path.relative_to(root))
        if relative in files:return
        if not (path.is_relative_to(product) or path.is_relative_to(root/'shared')):
            raise ValueError('payload outside tile/shared layout')
        files[relative]=path.stat().st_size
        if path.suffix=='.json' and path.stat().st_size<64*2**20:
            try:doc=json.loads(path.read_text())
            except ValueError:return
            follow(path.parent,doc)
            return
        with path.open('rb') as stream:
            header=stream.read(20)
            if header[:4]==b'glTF':
                length,kind=struct.unpack_from('<II',header,12)
                if kind!=0x4e4f534a:raise ValueError('GLB has no JSON chunk')
                doc=json.loads(stream.read(length))
                for item in [*doc.get('images',[]),*doc.get('buffers',[])]:
                    uri=item.get('uri')
                    if uri and not uri.startswith('data:'):visit(path.parent/uri)
    def follow(base,doc):
        for value in references(doc):
            candidate=(base/value)
            try:resolved=candidate.resolve()
            except OSError:continue
            if resolved.is_file() and resolved.is_relative_to(root) and (
                    resolved.is_relative_to(product) or resolved.is_relative_to(root/'shared')):
                visit(resolved)
    manifest=json.loads((product/'manifest.json').read_text())
    entries=[v for kind in ('visual','collision','vehicle_models') for v in manifest.get(kind,{}).values()]
    if manifest.get('roads'):entries.append(manifest['roads'])
    for entry in entries:visit(product/entry['url'])
    for name in ('manifest.json','ready.json','input-cache.json','stage-times.json'):
        if (product/name).exists():visit(product/name)
    return files


def verified_ack(product,generation,tile,destination):
    ready=json.loads((product/'ready.json').read_text())
    if ready['generation']!=generation:raise ValueError('ready generation mismatch')
    return dict(verified=True,generation=generation,tile=tile,revision=ready['revision'],
                manifest_sha256=digest(product/'manifest.json'),destination=str(destination),at=time.time())


def prune(root,generation,tile):
    """Host-side, only after a receiver validated the complete payload graph."""
    if not HEX.fullmatch(generation) or not TILE.fullmatch(tile):raise ValueError('invalid product key')
    farm=root/'farm';product=farm/'products'/generation/tile
    with locked(farm/'locks'/(generation+'-'+tile+'.lock')):
        ack=json.loads((farm/'exported'/generation/(tile+'.json')).read_text())
        if not ack.get('verified') or ack.get('generation')!=generation or ack.get('tile')!=tile:
            raise ValueError('invalid acknowledgement')
        if not product.exists():return
        if digest(product/'manifest.json')!=ack['manifest_sha256']:
            raise ValueError('product changed since acknowledgement')
        ready=json.loads((product/'ready.json').read_text())
        if ready['generation']!=generation or ready['revision']!=ack['revision']:
            raise ValueError('product identity changed')
        # Keep the three small warm seeds so pressure/resume never loses its
        # preflight proofs. They cost <200 MiB; the coordinator retires the host.
        if tile in ('t0_0_0','t0_1_0','t0_-1_0') and (product.parent/'warm.json').exists():
            return
        shutil.rmtree(product)


VALIDATOR=None  # shared_tiles.Farm of the producing revision; set before the pool forks


def adopt(batch,final,generation,tile):
    """Validate one pulled tile in a batch root and move it into the farm (pool worker)."""
    try:
        manifest=json.loads((batch/tile/'manifest.json').read_text())
        VALIDATOR.validate(batch,tile,manifest)
        if manifest.get('pass_kind')!='full':raise ValueError('not a full-pass product')
        target=final/tile
        ack=verified_ack(batch/tile,generation,tile,target)
        with locked(final.parent.parent/'locks'/(generation+'-'+tile+'.lock')):
            if target.exists():
                VALIDATOR.validate(final,tile,json.loads((target/'manifest.json').read_text()))
                if digest(target/'manifest.json')!=ack['manifest_sha256']:
                    raise ValueError('existing farm product differs; refusing to replace '+tile)
                shutil.rmtree(batch/tile)
            else:(batch/tile).rename(target)
        return ack,None
    except Exception as error:
        return None,f'{tile}: {type(error).__name__}: {error}'[:500]


class Pull:
    def __init__(self,args):self.a=args
    def remote(self,code,*args,input=None):
        command=shlex.join(['python3','-c',code,*map(str,args)])
        return subprocess.check_output(['ssh',self.a.host,command],input=input)
    def json(self,path):
        return json.loads(self.remote('import sys;from pathlib import Path;print(Path(sys.argv[1]).read_text())',path))
    def sync(self,source,dest,extra=()):
        dest.mkdir(parents=True,exist_ok=True)
        proc=subprocess.Popen(['rsync','-aH','--partial','--partial-dir=.rsync-partial',*extra,
                               self.a.host+':'+source,str(dest)+'/'],start_new_session=True)
        pidfile=self.a.root/'receipts/transfer.pid';pidfile.parent.mkdir(exist_ok=True)
        pidfile.write_text(str(proc.pid)+'\n')
        while proc.poll() is None:
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if shutil.disk_usage(self.a.root).free<26*2**30:
                    os.killpg(proc.pid,signal.SIGTERM)
                    try:proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
                    raise RuntimeError('receiver disk floor: partial transfer preserved for resume')
        if proc.returncode:raise subprocess.CalledProcessError(proc.returncode,proc.args)
    def room(self,needed):
        if shutil.disk_usage(self.a.root).free<25*2**30+needed:
            raise RuntimeError(f'receiver must keep 25 GiB free; needs {needed/2**30:.1f} GiB more')
    def sources(self,version):
        if not HEX.fullmatch(version):raise ValueError('invalid source version')
        remote=self.a.remote_root+'/farm/sources/versions/'+version
        final=self.a.root/'sources/versions'/version
        receipt_path=self.a.root/'receipts/sources'/(version+'.json')
        previous=json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        if (final/'source-version.json').exists() and previous.get('mode')!='catalog':
            validate_sources(final,version)
            atomic(self.a.root/'receipts/sources'/(version+'.json'),dict(version=version,verified=True,mode='full',raw_available=True,at=time.time()))
            return
        manifest=self.json(remote+'/source-version.json')
        temporary=final.with_name('.bulk-'+version)
        have=sum(p.stat().st_size for p in temporary.rglob('*') if p.is_file()) if temporary.exists() else 0
        self.room(max(0,sum(r['bytes'] for r in manifest['files'].values())-have))
        extra=['--checksum']
        if self.a.link_dest:extra+=['--link-dest='+str(self.a.link_dest.resolve())]
        self.sync(remote+'/',temporary,extra)
        validate_sources(temporary,version)
        with locked(self.a.root/'locks'/('source-'+version+'.lock')):
            if not final.exists():temporary.rename(final)
            else:
                validate_sources(final,version,raw=False)
                if not (final/'raw').exists():(temporary/'raw').rename(final/'raw')
                validate_sources(final,version)
                shutil.rmtree(temporary)
        atomic(self.a.root/'receipts/sources'/(version+'.json'),dict(version=version,verified=True,mode='full',raw_available=True,at=time.time()))
        print(json.dumps(dict(source_version=version,verified=True,root=str(final))),flush=True)
    def catalog(self,version):
        if not HEX.fullmatch(version):raise ValueError('invalid source version')
        remote=self.a.remote_root+'/farm/sources/versions/'+version
        final=self.a.root/'sources/versions'/version
        temporary=final.with_name('.catalog-'+version)
        manifest=self.json(remote+'/source-version.json')
        self.room(sum(v['bytes'] for p,v in manifest['files'].items() if not p.startswith('raw/')))
        extra=['--checksum','--exclude=/raw/']
        if self.a.link_dest:extra+=['--link-dest='+str(self.a.link_dest.resolve())]
        self.sync(remote+'/',temporary,extra)
        validate_sources(temporary,version,raw=False)
        with locked(self.a.root/'locks'/('source-'+version+'.lock')):
            if not final.exists():temporary.rename(final)
            else:validate_sources(final,version,raw=False)
        receipt=dict(version=version,verified=True,mode='catalog',raw_available=False,
                     raw_origin=self.a.host+':'+remote+'/raw/',at=time.time())
        atomic(self.a.root/'receipts/sources'/(version+'.json'),receipt)
        print(json.dumps(receipt),flush=True)

    def selected_sync(self,source,dest,files):
        with tempfile.NamedTemporaryFile('w') as listing:
            for name in sorted(files):
                if '\n' in name or name.startswith('/') or '..' in Path(name).parts:
                    raise ValueError('invalid rsync source name')
                listing.write(name+'\n')
            listing.flush()
            self.sync(source,dest,['--files-from='+listing.name])

    def products(self):
        build=(json.loads(self.a.build_file.read_text()) if self.a.build_file
               else self.json(self.a.remote_root+'/state/build.json'))
        generation=build['generation'];revision=build['revision']
        if not all(HEX.fullmatch(build[k]) for k in ('generation','revision','source_version')):raise ValueError('invalid build identity')
        profile=self.a.root/'sources/versions'/build['source_version']
        if not (profile/'source-version.json').exists() or not (self.a.root/'receipts/sources'/(build['source_version']+'.json')).exists():
            raise RuntimeError('pull a verified source catalog or full profile first')
        receipt=json.loads((self.a.root/'receipts/sources'/(build['source_version']+'.json')).read_text())
        if not receipt.get('verified'):raise ValueError('unverified source catalog')
        # Carry both base and bound metadata, replacing only host-specific data_root.
        meta=self.json(self.a.remote_root+'/farm/revisions/'+revision+'/revision.json')
        commit=meta['commit']
        if not REV.fullmatch(commit) or hashlib.sha256((commit+':'+build['source_version']).encode()).hexdigest()!=revision:
            raise ValueError('revision/source binding mismatch')
        base=self.a.root/'revisions'/commit
        if not (base/'revision.json').exists():
            self.sync(self.a.remote_root+'/farm/revisions/'+commit+'/',base)
        base_meta=json.loads((base/'revision.json').read_text())
        if hashlib.sha256((base_meta['generation']+':'+build['source_version']).encode()).hexdigest()!=generation:
            raise ValueError('generator/source binding mismatch')
        bound=self.a.root/'revisions'/revision;bound.mkdir(parents=True,exist_ok=True)
        if not (bound/'repo').exists():(bound/'repo').symlink_to('../'+commit+'/repo',target_is_directory=True)
        atomic(bound/'revision.json',{**meta,'data_root':str(profile),
                                     'bulk_raw_available':receipt.get('raw_available',True)})
        # Import the validator from exactly the producing revision.
        sys.path.insert(0,str(base/'repo/scripts/dev'))
        from shared_tiles import Farm
        final=self.a.root/'products'/generation;final.mkdir(parents=True,exist_ok=True)
        global VALIDATOR
        VALIDATOR=Farm
        # One batched pass at a time; per-tile locks guard each adoption.
        with locked(self.a.root/'locks'/('bulk-pull-batch-'+generation+'.lock')):
            while True:
                batch=self.plan(generation)
                if not batch:break
                self.pull_batch(generation,final,batch)
                if self.a.tiles:break

    def plan(self,generation):
        """One remote call: the reachable payload graph of up to --batch unacknowledged tiles."""
        code='''import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1]);from transfer import publication
root=Path(sys.argv[2]);done={p.stem for p in Path(sys.argv[3]).glob('*.json')}
wanted=json.loads(sys.argv[5]) if sys.argv[5]!='-' else None
ready=sorted(p.parent.name for p in root.glob('t0_*/ready.json'))
todo=[t for t in (wanted if wanted is not None else ready) if (root/t/'ready.json').exists() and (wanted is not None or t not in done)][:int(sys.argv[4])]
print(json.dumps(dict(ready=len(ready),done=len(done),batch={t:publication(root,t) for t in todo})))
'''
        products=self.a.remote_root+'/farm/products/'+generation
        result=json.loads(self.remote(code,self.a.remote_root+'/ops',products,
                                      self.a.remote_root+'/farm/exported/'+generation,self.a.batch,
                                      json.dumps(self.a.tiles) if self.a.tiles else '-'))
        print(json.dumps(dict(event='plan',ready=result['ready'],acknowledged=result['done'],batch=len(result['batch']))),flush=True)
        return result['batch']

    def pull_batch(self,generation,final,batch):
        import concurrent.futures
        started=time.monotonic()
        self.room(sum(size for files in batch.values() for size in files.values()))
        products=self.a.remote_root+'/farm/products/'+generation
        shared=sorted({n.removeprefix('shared/') for files in batch.values() for n in files if n.startswith('shared/')})
        tiles=sorted(batch)
        root=self.a.root/'imports'/generation/('batch-'+str(os.getpid()))
        if root.exists():shutil.rmtree(root)
        root.mkdir(parents=True)
        listing=[[n for n in files if not n.startswith('shared/')] for files in batch.values()]
        chunks=[[] for _ in range(self.a.streams)]
        for k,names in enumerate(listing):chunks[k%self.a.streams].extend(names)
        jobs=[(products+'/shared/',final/'shared',shared)]+[(products+'/',root,c) for c in chunks if c]
        with concurrent.futures.ThreadPoolExecutor(len(jobs)) as pool:
            for done in [pool.submit(self.selected_sync,*job) for job in jobs]:done.result()
        for name in shared:
            link=root/'shared'/name;link.parent.mkdir(parents=True,exist_ok=True)
            os.link(final/'shared'/name,link)
        transferred=time.monotonic()
        acks,errors=[],[]
        import multiprocessing
        with concurrent.futures.ProcessPoolExecutor(self.a.validators,mp_context=multiprocessing.get_context('fork')) as pool:
            for ack,error in pool.map(adopt,[root]*len(tiles),[final]*len(tiles),[generation]*len(tiles),tiles):
                (acks if ack else errors).append(ack or error)
        shutil.rmtree(root)
        # Record the receiver's verified payload identities on the producer, in one call.
        code='''import json,sys
from pathlib import Path
root=Path(sys.argv[1])
for row in json.load(sys.stdin):
    p=root/'farm/exported'/row['generation']/(row['tile']+'.json')
    p.parent.mkdir(parents=True,exist_ok=True)
    t=p.with_suffix('.tmp');t.write_text(json.dumps(row)+'\\n');t.replace(p)
'''
        if acks:self.remote(code,self.a.remote_root,input=json.dumps(acks).encode())
        if self.a.prune:
            for ack in acks:
                command=shlex.join(['python3',self.a.remote_root+'/ops/transfer.py','prune',self.a.remote_root,generation,ack['tile']])
                subprocess.run(['ssh',self.a.host,command],check=True)
        seconds=time.monotonic()-started
        print(json.dumps(dict(event='batch',tiles=len(tiles),verified=len(acks),failed=len(errors),errors=errors[:5],
                              bytes=sum(size for files in batch.values() for size in files.values()),
                              transfer_s=round(transferred-started,1),seconds=round(seconds,1),
                              tiles_per_min=round(len(acks)/seconds*60,1))),flush=True)


def main():
    if len(sys.argv)>1 and sys.argv[1]=='prune':
        prune(Path(sys.argv[2]),sys.argv[3],sys.argv[4]);return
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host',default='root@185.20.139.132')
    p.add_argument('--remote-root',default='/root/turku3d-bulk-gen2')
    p.add_argument('--root',type=Path,required=True,help='receiving farm root')
    source_mode=p.add_mutually_exclusive_group()
    source_mode.add_argument('--catalog',help='inventory and non-raw overlays only; imports can serve, raw inputs required for rebuilds')
    source_mode.add_argument('--sources',help='pull and verify this immutable source version')
    p.add_argument('--link-dest',type=Path,help='reuse matching local files via rsync checksum + hard links')
    p.add_argument('--build-file',type=Path,help='saved immutable build descriptor; safe across host revision changes')
    p.add_argument('--tiles',nargs='+')
    p.add_argument('--prune',action='store_true',help='remove host tile only after verified publication and acknowledgement')
    p.add_argument('--batch',type=int,default=200,help='tiles per remote publication call and rsync batch')
    p.add_argument('--streams',type=int,default=3,help='parallel rsync streams per batch')
    p.add_argument('--validators',type=int,default=4,help='local validation processes')
    a=p.parse_args();a.root=a.root.resolve();a.root.mkdir(parents=True,exist_ok=True)
    pull=Pull(a)
    if a.sources:pull.sources(a.sources)
    elif a.catalog:pull.catalog(a.catalog)
    else:pull.products()

if __name__=='__main__':main()
