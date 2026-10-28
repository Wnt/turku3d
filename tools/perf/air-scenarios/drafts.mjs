// Read-only frozen draft service for reproducible renderer measurements; never builds or fetches city data.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
const root = path.resolve(process.argv[2]);
const port = Number(process.argv[3]);
http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/coarse/status' || url.pathname === '/coarse/want') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ version: 1, state: 'ready', building: [], queued: [] })); return;
  }
  const file = path.resolve(root, '.' + decodeURIComponent(url.pathname.replace(/^\/coarse/, '')));
  if (!file.startsWith(root + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    res.writeHead(url.pathname.endsWith('/manifest.json') ? 202 : 404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ state: 'pending', reason: 'not in frozen draft snapshot' })); return;
  }
  res.writeHead(200, { 'Content-Type': file.endsWith('.json') ? 'application/json' : 'application/octet-stream',
    'Content-Length': fs.statSync(file).size });
  fs.createReadStream(file).pipe(res);
}).listen(port, '127.0.0.1', () => console.log(`Frozen drafts ${root} at ${port}`));
