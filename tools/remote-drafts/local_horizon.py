"""Local distant-city derivatives with nearby tiles from an unchanged tile host."""
import argparse
import http.server
import json
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from scenery import Scenery, serve

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--port', type=int, required=True)
parser.add_argument('--source', required=True)
parser.add_argument('--cache', required=True)
parser.add_argument('--upstream', default='http://192.168.1.114:7835')
args = parser.parse_args()
scenery = Scenery(args.source, args.cache)

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _send(self, status, data, mime, cache='no-cache'):
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
        if serve(self, scenery, urlparse(self.path)):
            return
        self.forward()

    def do_POST(self):
        self.forward(self.rfile.read(int(self.headers.get('Content-Length', 0))))

    def forward(self, body=None):
        request = Request(args.upstream + self.path, data=body, method=self.command)
        if self.headers.get('Content-Type'):
            request.add_header('Content-Type', self.headers['Content-Type'])
        try:
            response = urlopen(request, timeout=60)
        except HTTPError as error:
            response = error
        except OSError:
            self._json(503, {'state': 'offline'})
            return
        with response:
            self._send(response.status, response.read(), response.headers.get('Content-Type', 'application/octet-stream'))

    def log_message(self, *args):
        pass

class Server(http.server.ThreadingHTTPServer):
    request_queue_size = 128

Server(('127.0.0.1', args.port), Handler).serve_forever()
