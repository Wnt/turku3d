#!/usr/bin/env node
// What WebGPU gives Chrome on this host, and how fast the building blocks of a GPU-driven renderer run there.
// Evidence for docs/design/renderer-path.md. No dependencies (Node 22+ WebSocket).
//
//   python3 ~/turku3d/tools/host-lease/session.py run mac "$TURKU3D_SANDBOX" --kind func --window-size 1920,1080 \
//     --load-timeout 300 -- node tools/render-spike/measure.mjs [--only adapter,raster,three,shared,field] [--out FILE]
//
// Opens spike.html in the leased browser (BROWSER_CDP), which runs the parts named by --only and returns one JSON:
//   adapter  features, limits and WGSL language features of the adapter, and a device with every limit raised
//   raster   compute culling of cluster records into an indirect draw of instanced 128-triangle clusters that pull
//            their vertices from storage buffers, into a 1920x1080 r32uint visibility buffer, and its resolve
//   three    CPU time of three.js r186 WebGPURenderer.render() per mesh, and per BatchedMesh instance
//   shared   raw passes on three's GPUDevice that write and depth-test against three's own render targets
//   field    sphere tracing of a four-level distance-field clipmap in a compute pass (primary and diffuse rays)
// three.js comes from game/node_modules of this checkout or of the integration checkout.
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const opt = (name, fallback) => argv.includes(name) ? argv[argv.indexOf(name) + 1] : fallback;
const cdp = opt('--cdp', process.env.BROWSER_CDP);
if (!cdp) throw new Error('give --cdp http://127.0.0.1:PORT (or BROWSER_CDP from host-lease/session.py)');
const only = opt('--only', 'adapter,raster,three,shared,field');
const port = Number(opt('--port', Number(process.env.TURKU3D_PORT_BASE ?? 5170) + 8));
const three = [path.join(here, '../../game/node_modules/three/build'),
  path.join(os.homedir(), 'turku3d/wt/loop-integration/repo/game/node_modules/three/build')].find(dir => fs.existsSync(dir));

const types = { '.html': 'text/html', '.js': 'text/javascript' };
const server = http.createServer((request, response) => {
  const name = new URL(request.url, 'http://localhost').pathname;
  const file = name.startsWith('/three/') && three ? path.join(three, path.basename(name))
    : path.join(here, name === '/' ? 'spike.html' : path.basename(name));
  if (!fs.existsSync(file)) { response.writeHead(404).end(); return; }
  response.writeHead(200, { 'content-type': types[path.extname(file)] ?? 'application/octet-stream' }).end(fs.readFileSync(file));
});
await new Promise((resolve, reject) => server.once('error', reject).listen(port, '127.0.0.1', resolve));

const version = await (await fetch(`${cdp}/json/version`)).json();
const socket = new WebSocket(version.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
let next = 0;
const waiting = new Map();
const log = [];
socket.onmessage = event => {
  const message = JSON.parse(event.data);
  if (message.method === 'Runtime.consoleAPICalled') log.push(message.params.args.map(a => a.value ?? a.description).join(' ').slice(0, 400));
  if (message.method === 'Runtime.exceptionThrown') log.push(`exception: ${message.params.exceptionDetails.exception?.description ?? message.params.exceptionDetails.text}`.slice(0, 600));
  const done = waiting.get(message.id);
  if (!done) return;
  waiting.delete(message.id);
  message.error ? done.reject(new Error(message.error.message)) : done.resolve(message.result);
};
const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
  waiting.set(++next, { resolve, reject });
  socket.send(JSON.stringify({ id: next, method, params, sessionId }));
});

const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
let result = { error: 'the page did not finish' };
try {
  await send('Runtime.enable', {}, sessionId);
  await send('Page.navigate', { url: `http://localhost:${port}/?only=${only}` }, sessionId);
  const deadline = Date.now() + Number(opt('--timeout', 280)) * 1000;
  while (Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, 1000));
    const state = await send('Runtime.evaluate', { expression: 'window.__done ? JSON.stringify(window.__result) : (window.__stage ?? "")', returnByValue: true }, sessionId);
    const value = state.result?.value ?? '';
    if (value.startsWith('{')) { result = JSON.parse(value); break; }
    if (value !== result.stage) { result.stage = value; console.error(`stage: ${value}`); }
  }
} finally {
  await send('Target.closeTarget', { targetId }).catch(() => {});
  socket.close();
  server.closeAllConnections();
  server.close();
}
result.browser = version.Browser;
result.host = { cpu: os.cpus()[0].model, cores: os.cpus().length, memory_gb: Math.round(os.totalmem() / 2 ** 30) };
if (log.length) result.console = log.slice(0, 40);
const text = JSON.stringify(result, null, 1);
const out = opt('--out');
if (out) { fs.mkdirSync(path.dirname(out), { recursive: true }); fs.writeFileSync(out, text); }
console.log(text);
