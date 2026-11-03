#!/usr/bin/env python3
"""Serve the point-cloud product (build_copc.py --out) over HTTP with single-range requests, as COPC readers need.

  serve.py --root /bulk/turku3d/lidarpc/copc --host 0.0.0.0 --port 7960

GET/HEAD only, files under --root only. `Range: bytes=a-b` answers 206 with Content-Range; COPC files are immutable
per name and recipe (index.json is not, so it is never cached). CORS is open so a page can also read it directly.
"""
import argparse
import os
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

RANGE = re.compile(r'^bytes=(\d*)-(\d*)$')
TYPES = {'.json': 'application/json', '.laz': 'application/octet-stream'}


def byte_range(header: str | None, size: int) -> tuple[int, int] | None | bool:
    """(start, end inclusive) for one satisfiable range; None without a Range header; False if unsatisfiable."""
    if not header:
        return None
    m = RANGE.match(header.strip())
    if not m or (not m.group(1) and not m.group(2)):
        return False
    if not m.group(1):  # suffix range: the last N bytes
        n = int(m.group(2))
        return (max(0, size - n), size - 1) if n > 0 and size > 0 else False
    start = int(m.group(1))
    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    return (start, end) if start < size and start <= end else False


class Handler(BaseHTTPRequestHandler):
    root: Path
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):  # quiet: thousands of range reads per session
        pass

    def resolve(self) -> Path | None:
        path = unquote(urlsplit(self.path).path).lstrip('/')
        path = re.sub(r'^pointcloud/', '', path)  # the game's same-origin prefix, when a proxy keeps it
        target = (self.root / path).resolve()
        return target if target.is_file() and self.root in target.parents else None

    def common(self, status, length, kind, extra=()):
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(length))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Expose-Headers', 'Content-Range, Content-Length, Accept-Ranges')
        self.send_header('Cross-Origin-Resource-Policy', 'cross-origin')
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Range')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_HEAD(self):
        self.serve(body=False)

    def do_GET(self):
        self.serve(body=True)

    def serve(self, body: bool):
        target = self.resolve()
        if target is None:
            self.common(HTTPStatus.NOT_FOUND, 0, 'text/plain')
            return
        size = target.stat().st_size
        kind = TYPES.get(target.suffix, 'application/octet-stream')
        cache = ('Cache-Control', 'no-store' if target.suffix == '.json' else 'public, max-age=86400')
        span = byte_range(self.headers.get('Range'), size)
        if span is False:
            self.common(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, 0, kind, [('Content-Range', f'bytes */{size}')])
            return
        start, end = span if span else (0, size - 1)
        length = max(0, end - start + 1)
        extra = [cache] + ([('Content-Range', f'bytes {start}-{end}/{size}')] if span else [])
        self.common(HTTPStatus.PARTIAL_CONTENT if span else HTTPStatus.OK, length, kind, extra)
        if not body or length == 0:
            return
        with open(target, 'rb') as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(left, 1 << 20))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', required=True)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=7960)
    args = ap.parse_args()
    Handler.root = Path(args.root).resolve()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f'point cloud {Handler.root} on {args.host}:{args.port}', flush=True)
    os.nice(5)
    server.serve_forever()


if __name__ == '__main__':
    main()
