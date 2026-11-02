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


def validate_sources(directory,expected):
    manifest=json.loads((directory/'source-version.json').read_text())
    if manifest['version']!=expected:raise ValueError('source version mismatch')
    content=json.dumps(manifest['files'],sort_keys=True,separators=(',',':')).encode()
    if hashlib.sha256(content).hexdigest()!=expected:raise ValueError('source inventory hash mismatch')
    for relative,row in manifest['files'].items():
        path=directory/relative
        if not path.resolve().is_relative_to(directory.resolve()):raise ValueError('source outside profile')
        if path.stat().st_size!=row['bytes'] or digest(path)!=row['sha256']:
            raise ValueError('source hash mismatch: '+relative)
    return manifest


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
        shutil.rmtree(product)


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
        if (final/'source-version.json').exists():
            validate_sources(final,version)
            atomic(self.a.root/'receipts/sources'/(version+'.json'),dict(version=version,verified=True,at=time.time()))
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
            else:validate_sources(final,version)
        atomic(self.a.root/'receipts/sources'/(version+'.json'),dict(version=version,verified=True,at=time.time()))
        print(json.dumps(dict(source_version=version,verified=True,root=str(final))),flush=True)
    def products(self):
        build=self.json(self.a.remote_root+'/state/build.json')
        generation=build['generation'];revision=build['revision']
        if not all(HEX.fullmatch(build[k]) for k in ('generation','revision','source_version')):raise ValueError('invalid build identity')
        profile=self.a.root/'sources/versions'/build['source_version']
        if not (profile/'source-version.json').exists() or not (self.a.root/'receipts/sources'/(build['source_version']+'.json')).exists():
            raise RuntimeError('pull the frozen sources first; farm must be able to rebuild this generation')
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
        atomic(bound/'revision.json',{**meta,'data_root':str(profile)})
        # Import the validator from exactly the producing revision.
        sys.path.insert(0,str(base/'repo/scripts/dev'))
        from shared_tiles import Farm
        if self.a.tiles:tiles=self.a.tiles
        else:
            tiles=json.loads(self.remote('import json,sys;from pathlib import Path;print(json.dumps([p.parent.name for p in Path(sys.argv[1]).glob("t0_*/ready.json")]))',self.a.remote_root+'/farm/products/'+generation))
        final=self.a.root/'products'/generation;final.mkdir(parents=True,exist_ok=True)
        with locked(self.a.root/'locks'/('bulk-pull-'+generation+'.lock')):
            for tile in tiles:
                if not TILE.fullmatch(tile):raise ValueError('invalid tile')
                temporary=self.a.root/'imports'/generation/tile
                target=final/tile
                source=self.a.remote_root+'/farm/products/'+generation+'/'+tile
                self.room(2*2**30)
                self.sync(source+'/',temporary/tile,['--exclude=.texture-cache','--exclude=.rsync-partial'])
                # Copy shared objects before publishing the tile. They are immutable hashes.
                self.sync(self.a.remote_root+'/farm/products/'+generation+'/shared/',final/'shared',['--ignore-existing'])
                if (temporary/'shared').exists():shutil.rmtree(temporary/'shared')
                shutil.copytree(final/'shared',temporary/'shared',copy_function=os.link)
                manifest=json.loads((temporary/tile/'manifest.json').read_text())
                Farm.validate(temporary,tile,manifest)
                if manifest.get('pass_kind')!='full':raise ValueError('not a full-pass product')
                ack=verified_ack(temporary/tile,generation,tile,target)
                with locked(self.a.root/'locks'/(generation+'-'+tile+'.lock')):
                    if target.exists():
                        Farm.validate(final,tile,json.loads((target/'manifest.json').read_text()))
                        if digest(target/'manifest.json')!=ack['manifest_sha256']:
                            raise ValueError('existing farm product differs; refusing to replace '+tile)
                    else:(temporary/tile).rename(target)
                shutil.rmtree(temporary)
                # Record the receiver's verified payload identity on the producer.
                code='''import json,sys,os
from pathlib import Path
root=Path(sys.argv[1]);row=json.load(sys.stdin)
p=root/'farm/exported'/row['generation']/(row['tile']+'.json')
p.parent.mkdir(parents=True,exist_ok=True)
t=p.with_suffix('.tmp');t.write_text(json.dumps(row)+'\\n');t.replace(p)
'''
                self.remote(code,self.a.remote_root,input=json.dumps(ack).encode())
                if self.a.prune:
                    command=shlex.join(['python3',self.a.remote_root+'/transfer.py','prune',self.a.remote_root,generation,tile])
                    subprocess.run(['ssh',self.a.host,command],check=True)
                print(json.dumps(ack),flush=True)


def main():
    if len(sys.argv)>1 and sys.argv[1]=='prune':
        prune(Path(sys.argv[2]),sys.argv[3],sys.argv[4]);return
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host',default='root@185.20.139.132')
    p.add_argument('--remote-root',default='/root/turku3d-bulk-gen2')
    p.add_argument('--root',type=Path,required=True,help='receiving farm root')
    p.add_argument('--sources',help='pull and verify this immutable source version')
    p.add_argument('--link-dest',type=Path,help='reuse matching local files via rsync checksum + hard links')
    p.add_argument('--tiles',nargs='+')
    p.add_argument('--prune',action='store_true',help='remove host tile only after verified publication and acknowledgement')
    a=p.parse_args();a.root=a.root.resolve();a.root.mkdir(parents=True,exist_ok=True)
    pull=Pull(a)
    if a.sources:pull.sources(a.sources)
    else:pull.products()

if __name__=='__main__':main()
