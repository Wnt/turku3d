"""Local distant-city scenery server: catalogue, descriptors, trees and LOD derivatives, plus its own health."""
import argparse
import http.server
import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlparse
from scenery import Scenery, serve

HEALTH = {'/health', '/coarse/health', '/coarse/scenery/health'}


class Activity:
    """Request outcomes of the scenery routes since start, for health."""

    def __init__(self):
        self.started = time.time()
        self.lock = threading.Lock()
        self.served = self.errors = 0
        self.last_served = self.last_error = None

    def record(self, status):
        with self.lock:
            if status >= 500:
                self.errors += 1
                self.last_error = dict(t=time.time(), status=status)
            elif status < 400:
                self.served += 1
                self.last_served = time.time()


def health(scenery, activity, regional=None):
    """Real state of inputs and cache; ready only when every configured input exists and the cache is writable."""
    # Names and booleans only: the reply is public through city.lab, so no server paths.
    inputs = dict(zip(('preferred', 'sources') if len(scenery.roots) == 2 else ('sources',),
                      (root.is_dir() for root in scenery.roots)))
    if regional:
        inputs['regional'] = Path(regional).is_file()
    follow = [name for name, _ in scenery.chain.roots()] if scenery.chain else None
    cache_ok = scenery.cache.is_dir() and os.access(scenery.cache, os.W_OK)
    now = time.time()
    with activity.lock:
        served = dict(count=activity.served, last=activity.last_served,
                      age_s=round(now - activity.last_served, 1) if activity.last_served else None)
        errors = dict(count=activity.errors, last=activity.last_error)
    with scenery.lock:
        pending = len(scenery.pending)
    ok = cache_ok and all(inputs.values())
    return dict(state='ready' if ok else 'degraded', service='scenery', recipe=scenery.recipe,
                uptime_s=round(now - activity.started), inputs=inputs,
                cache=dict(writable=cache_ok, pending_builds=pending), **({'follow': follow} if follow is not None else {}),
                served=served, errors=errors)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _send(self, status, data, mime, cache='no-cache'):
        if self.path.startswith('/coarse/scenery') and urlparse(self.path).path not in HEALTH:
            activity.record(status)
        try:
            self._write_response(status, data, mime, cache)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Camera moves cancel obsolete requests; do not block serving on traceback output.

    def _write_response(self, status, data, mime, cache):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', cache)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status, data):
        self._send(status, json.dumps(data).encode(), 'application/json')

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in HEALTH:
            state = health(scenery, activity, args.regional if args.survey else None)
            self._json(200 if state['state'] == 'ready' else 503, state)
        elif not serve(self, scenery, url):
            self._json(404, {'state': 'missing'})

    def log_message(self, *args):
        pass

class Server(http.server.ThreadingHTTPServer):
    request_queue_size = 128

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--preferred-source', help='Prepared native full tiles; retained sources fill remaining cells')
    parser.add_argument('--cache', required=True)
    parser.add_argument('--follow', help='farm alias family to prefer, newest first (train: trainN ... train1), '
                        'over --preferred-source, which must be their base generation\'s products')
    parser.add_argument('--farm-root', default=str(Path.home() / 'turku3d-local/loops/tiles/farm'))
    parser.add_argument('--farm-url', default='http://192.168.1.114:7950')
    parser.add_argument('--survey', action='store_true', help='Fill missing far tiles directly from cached survey inputs')
    parser.add_argument('--regional', help='Immutable public regional.npz terrain grid')
    parser.add_argument('--lod-buildings', action='store_true', help='Distant buildings as LoD2 block parents (gen2arch)')
    parser.add_argument('--chm', help='City lidar canopy height model (pipeline/turku3d/coarse/chm.py merge); read once at start')
    args = parser.parse_args()
    survey = None
    if args.survey:
        from turku3d.coarse.horizon import SurveyScenery
        survey = SurveyScenery(args.regional, chm=args.chm)
    chain = None
    if args.follow:
        if not args.preferred_source:
            parser.error('--follow needs --preferred-source (the base generation the trains migrate)')
        from scenery import FarmChain
        chain = FarmChain(args.farm_root, args.farm_url, args.follow, Path(args.preferred_source).name)
    scenery = Scenery(args.source, args.cache, survey=survey, preferred=args.preferred_source, lod=args.lod_buildings,
                      chain=chain)
    scenery.cache.mkdir(parents=True, exist_ok=True)
    activity = Activity()
    Server(('127.0.0.1', args.port), Handler).serve_forever()
