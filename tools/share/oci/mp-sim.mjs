// Multiplayer load on a published host: N drivers join one private room over the public WebSocket path (TLS, Caddy,
// the host's own server), each acquires its starter car and sends driven poses at --hz while circling near the spawn,
// so every driver is within 250 m of every other and receives all their poses. Reports join times, the pose stream
// each driver receives (messages/s, poses/s, delivery latency: all drivers run in this process, so a pose's `t` is
// this process's clock) and every close or error. A private room (?room=) keeps players' rooms untouched.
//   node mp-sim.mjs [--base https://city.madekivi.fi] [--n 16] [--secs 120] [--hz 20] [--room oci-load]
import { createRequire } from 'node:module';
import path from 'node:path';

const require = createRequire(path.join(path.dirname(new URL(import.meta.url).pathname), '../../../server/package.json'));
const { WebSocket } = require('ws');
const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const BASE = new URL(arg('base', 'https://city.madekivi.fi'));
const N = Number(arg('n', 16)), SECS = Number(arg('secs', 120)), HZ = Number(arg('hz', 20)), ROOM = arg('room', 'oci-load');
const url = `${BASE.protocol === 'http:' ? 'ws:' : 'wss:'}//${BASE.host}/?room=${ROOM}`;
const now = () => performance.now();
const wheels = Array.from({ length: 4 }, (_, i) => ({ pos: { x: i < 2 ? 1.35 : -1.35, y: -.4, z: i % 2 ? .8 : -.8 },
  steer: 0, rotation: 0, contact: true, suspension: .2 }));
// Driver i circles (radius 20..95 m) around the Turku spawn at 12 m/s.
function pose(i, t) {
  const r = 20 + i * 5, w = 12 / r, a = w * t / 1000 + i;
  return { t, E: 173 + r * Math.cos(a), N: -252 + r * Math.sin(a), H: 4, yaw: (a + Math.PI / 2) % (2 * Math.PI) - Math.PI,
    vE: -12 * Math.sin(a), vN: 12 * Math.cos(a), vH: 0, mode: 'car', vehicle: 'player', carrying: false,
    rot: { x: 0, y: 0, z: 0, w: 1 }, wheels: wheels.map(x => ({ ...x, rotation: t / 330 })) };
}
const pct = (xs, q) => { const s = [...xs].sort((a, b) => a - b); return s.length ? +s[Math.min(s.length - 1, Math.floor(q * s.length))].toFixed(1) : null; };

const drivers = [], t0 = now();
let measuring = false, tMeasure = 0;
async function join(i) {
  const d = { i, types: {}, msgs: 0, poses: 0, lat: [], closes: [], errors: [], joinMs: null, sent: 0, seen: new Set(), bytes: 0 };
  drivers.push(d);
  const ws = d.ws = new WebSocket(url, { origin: BASE.origin, perMessageDeflate: false });
  ws.on('error', e => d.errors.push(e.message));
  ws.on('unexpected-response', (_, res) => d.errors.push(`upgrade ${res.statusCode}`));
  ws.on('close', (code, why) => d.closes.push(`${((now() - t0) / 1000).toFixed(1)}s ${code} ${why}`));
  ws.on('message', raw => {
    const m = JSON.parse(raw); d.bytes += raw.length;
    if (m.type === 'welcome' && d.joinMs == null) {
      d.joinMs = now() - d.start; d.id = m.you.id;
      // Stand at the car first: acquisition needs the player within 7 m (server/PROTOCOL.md, shared vehicles).
      const p = pose(i, now());
      ws.send(JSON.stringify({ v: 2, type: 'pose', ...p }));
      ws.send(JSON.stringify({ v: 2, type: 'vehicle', action: 'acquire', request: 'acquire-0', id: `${d.id}:car0`, localId: 'car0',
        half: { x: 2.2, y: 0.75, z: 0.9 }, pose: p }));
    }
    d.types[m.type] = (d.types[m.type] ?? 0) + 1;
    if (m.type === 'vehicle_result' && !m.ok) d.errors.push(`acquire denied ${m.reason ?? ''}`);
    if (m.type === 'error') d.errors.push(`error ${m.code}`);
    if (!measuring) return;
    d.msgs++;
    if (m.type === 'poses') for (const p of m.poses) { d.poses++; d.seen.add(p.id); d.lat.push(now() - p.t); }
  });
  d.start = now();
  await new Promise((ok, ko) => { ws.once('open', ok); ws.once('error', ko); ws.once('unexpected-response', () => ko(new Error('upgrade refused'))); });
  ws.send(JSON.stringify({ v: 2, type: 'hello', bot: true }));
}
await Promise.all(Array.from({ length: N }, (_, i) => join(i).catch(e => drivers[i]?.errors.push(e.message))));
await new Promise(r => setTimeout(r, 1500));  // hello + acquire spend the rate budget; measure steady state
const timer = setInterval(() => {
  const t = now();
  for (const d of drivers) {
    if (d.ws.readyState !== WebSocket.OPEN || !d.id) continue;
    const p = pose(d.i, t), id = `${d.id}:car0`;
    d.ws.send(JSON.stringify({ v: 2, type: 'pose', ...p, vehicleId: id, vehicles: [{ id, epoch: 1, pose: p }] })); d.sent++;
  }
}, 1000 / HZ);
measuring = true; tMeasure = now();
await new Promise(r => setTimeout(r, SECS * 1000));
clearInterval(timer);
const secs = (now() - tMeasure) / 1000, lat = drivers.flatMap(d => d.lat);
const result = {
  url, drivers: N, hz: HZ, seconds: +secs.toFixed(1),
  joined: drivers.filter(d => d.joinMs != null).length, openAtEnd: drivers.filter(d => d.ws.readyState === WebSocket.OPEN).length,
  joinMs: { p50: pct(drivers.map(d => d.joinMs).filter(x => x != null), .5), max: pct(drivers.map(d => d.joinMs).filter(x => x != null), 1) },
  peersSeen: { min: Math.min(...drivers.map(d => d.seen.size)), expected: N - 1 },
  posesPerSecPerDriver: { min: +Math.min(...drivers.map(d => d.poses / secs)).toFixed(1), p50: pct(drivers.map(d => d.poses / secs), .5),
    expected: (N - 1) * Math.min(HZ, 20) },
  messagesPerSecPerDriver: pct(drivers.map(d => d.msgs / secs), .5),
  downKBpsPerDriver: pct(drivers.map(d => d.bytes / secs / 1000), .5),
  latencyMs: { p50: pct(lat, .5), p95: pct(lat, .95), p99: pct(lat, .99), max: pct(lat, 1) },
  types: drivers[0]?.types,
  closes: drivers.flatMap(d => d.closes.map(c => `#${d.i} ${c}`)), errors: drivers.flatMap(d => d.errors.map(e => `#${d.i} ${e}`)),
};
for (const d of drivers) d.ws.close(1000);
console.log(JSON.stringify(result, null, 1));
setTimeout(() => process.exit(0), 500);
