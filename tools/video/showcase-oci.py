"""Isolated live first/full capture on the deployed OCI source; no public output changes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = Path('/srv/turku3d/work/coarse')
OUT = Path(tempfile.mkdtemp(prefix='.build-probe-showcase-', dir=ROOT))
TILE = 't0_1_0'
state = {'setup':'isolated deployed generator; shared input/stage caches', 'tile':TILE, 'output':str(OUT), 'pid':os.getpid(), 'started':None, 'passes':{}}
lock = threading.Lock()

def build():
    with (OUT/'capture-worker.log').open('w') as log:
        process = subprocess.Popen([sys.executable,'-m','turku3d.coarse','worker'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True)
        try:
            for kind in ('first','full'):
                start=time.monotonic()
                process.stdin.write(json.dumps({'tile':TILE,'out':str(OUT),'first_pass':kind=='first'})+'\n');process.stdin.flush()
                response=json.loads(process.stdout.readline())
                state['passes'][kind]={'seconds':round(time.monotonic()-start,3),'response':response}
                if response.get('code') != 0:state['error']=response;break
                name='collision.json' if kind=='first' else 'manifest.json'
                manifest=json.loads((OUT/name).read_text())
                (OUT/f'capture-{kind}.json').write_text(json.dumps(manifest))
                state['passes'][kind]['buildings']=manifest.get('buildings')
                state['passes'][kind]['collision_cells']=len(manifest.get('collision',{}))
                print(json.dumps(state),flush=True)
        except Exception as exc:state['error']=str(exc)
        finally:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            (OUT/'capture-result.json').write_text(json.dumps(state,indent=2))

def start():
    with lock:
        if state['started'] is None:
            state['started']=time.time()
            threading.Thread(target=build,daemon=True).start()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route=urlparse(self.path).path
        if route=='/capture/stop':
            self.send(200,b'{}','application/json');threading.Thread(target=self.server.shutdown,daemon=True).start();return
        if route=='/capture/status':return self.send(200,json.dumps(state).encode(),'application/json')
        if route.startswith('/coarse/shared/'):
            file=ROOT/'shared'/Path(route).name
        elif route.startswith(f'/coarse/{TILE}/'):
            name=route.split(f'/coarse/{TILE}/',1)[1]
            file=(OUT/name).resolve()
            if not file.is_relative_to(OUT):return self.send(403,b'')
            if name in ('collision.json','manifest.json'):
                start()
                if not file.exists():return self.send(202,b'{"state":"building"}','application/json')
        else:return self.send(404,b'')
        if not file.is_file():return self.send(404,b'')
        kind='application/json' if file.suffix=='.json' else 'application/octet-stream'
        self.send(200,file.read_bytes(),kind)
    def send(self,status,body,kind='application/octet-stream'):
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-cache');self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass
print(json.dumps({'ready':True,**state}),flush=True)
ThreadingHTTPServer(('127.0.0.1',5196),Handler).serve_forever()
