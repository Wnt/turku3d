// A stand-in for the coarse tile service in localhost.test.mjs: GET /coarse/status reports `building` from
// <dir>/building.json and `modules` from <dir>/modules.json, POST /coarse/drain is recorded in <dir>/drained, and one
// "build" child (sleep) runs in its process group; every start appends {pid, build} to <dir>/starts.jsonl.
// Usage: node fakeCoarse.mjs <dir> --port <port>
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';

const dir = process.argv[2];
const port = Number(process.argv[process.argv.indexOf('--port') + 1]);
const read = (name, def) => { try { return JSON.parse(fs.readFileSync(path.join(dir, name), 'utf8')); } catch { return def; } };
const build = spawn('sleep', ['300'], { stdio: 'ignore' });
const started = Date.now() / 1000;
http.createServer((req, res) => {
  const json = (status, body) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
  if (req.method === 'POST' && req.url === '/coarse/drain') { fs.writeFileSync(path.join(dir, 'drained'), ''); return json(200, { draining: true }); }
  if (req.url === '/coarse/status') return json(200, { state: 'idle', building: read('building.json', []), modules: read('modules.json', []), started });
  json(404, { error: 'not found' });
}).listen(port, '127.0.0.1', () => fs.appendFileSync(path.join(dir, 'starts.jsonl'), JSON.stringify({ pid: process.pid, build: build.pid }) + '\n'));
