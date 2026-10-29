// Isolated server child: CPU excludes fake clients. No production traffic/data.
import { fork } from 'node:child_process';
import { once } from 'node:events';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { WebSocket } from 'ws';

const seconds = Number(process.argv[2] ?? 10);
if (!Number.isFinite(seconds) || seconds < 1 || seconds > 300) throw Error('Usage: node server/tools/load.mjs [1..300 seconds]');
const directory = mkdtempSync(join(tmpdir(), 'turku-mp-load-'));
const child = fork(fileURLToPath(new URL('../index.js', import.meta.url)), [], {
  env: { ...process.env, TURKU_MP_PORT: String(Number(process.env.TURKU3D_PORT_BASE ?? 5400) + 4), TURKU_MP_SCORES: directory },
  stdio: ['ignore', 'ignore', 'inherit', 'ipc'],
});
const clients = [], identities = [];
const drivers = 16;
const wheels = Array.from({ length: 4 }, (_, i) => ({ pos: { x: i < 2 ? 1.35 : -1.35, y: -.4, z: i % 2 ? .8 : -.8 },
  steer: 0, rotation: 0, contact: true, suspension: .2 }));
const vehiclePose = (i, t) => ({ t, E: 173 + (i % 4) * 10 + Math.sin(t / 1000), N: -252 - Math.floor(i / 4) * 10,
  H: 4, yaw: -Math.PI / 2, vE: Math.cos(t / 1000), vN: 0, vH: 0, mode: 'car', vehicle: 'player', carrying: false,
  rot: { x: 0, y: 0, z: 0, w: 1 }, wheels: wheels.map(w => ({ ...w, rotation: t / 330 })) });
let timer, sent = 0, received = 0, failures = 0;
const deadline = setTimeout(() => { console.error('Load test timed out'); child.kill('SIGTERM'); process.exitCode = 1; }, (seconds + 15) * 1000);
try {
  const [{ port }] = await once(child, 'message');
  await Promise.all(Array.from({ length: 32 }, async (_, i) => {
    const ws = new WebSocket(`ws://127.0.0.1:${port}`, { origin: 'https://wnt.github.io' });
    clients.push(ws);
    ws.on('error', () => { failures++; });
    ws.on('message', () => { received++; });
    await once(ws, 'open');
    const welcome = once(ws, 'message');
    ws.send(JSON.stringify({ v: 2, type: 'hello' }));
    const [message] = await welcome;
    const joined = JSON.parse(message);
    if (joined.type !== 'welcome') throw Error('Expected welcome');
    identities[i] = joined.you.id;
    ws.loadIndex = i;
  }));
  for (const ws of clients) {
    const i = ws.loadIndex, p = vehiclePose(i, 0);
    ws.send(JSON.stringify({ v: 2, type: 'pose', ...p, ...(i >= drivers ? { E: 2000, mode: 'foot', vehicle: null } : {}) }));
    if (i >= drivers) continue;
    for (let j = 0; j < 8; j++) ws.send(JSON.stringify({ v: 2, type: 'vehicle', action: j ? 'register' : 'acquire',
      request: `register-${j}`, id: `${identities[i]}:car${j}`, localId: `car${j}`,
      pose: { ...p, ...(j ? { E: p.E + 80, N: p.N + j * 6, vE: 0 } : {}) } }));
  }
  await delay(1100); // Hello is in the rate budget; measure steady-state pose traffic.
  async function metrics() {
    const response = once(child, 'message'); child.send('metrics'); return (await response)[0];
  }
  const before = await metrics();
  if (before.vehicles !== 128 || before.stats.rejected) throw Error('Load setup did not register all 128 vehicles cleanly');
  received = 0;
  const start = performance.now();
  timer = setInterval(() => {
    const t = performance.now();
    clients.forEach(ws => {
      const i = ws.loadIndex;
      if (ws.readyState !== WebSocket.OPEN || i >= drivers) return;
      const p = vehiclePose(i, t), id = `${identities[i]}:car0`;
      ws.send(JSON.stringify({ v: 2, type: 'pose', ...p, wheels: undefined, rot: undefined, vehicleId: id,
        vehicles: [{ id, epoch: 1, pose: p }] }));
      sent++;
    });
  }, 50);
  await delay(seconds * 1000);
  clearInterval(timer);
  const after = await metrics();
  const elapsed = (performance.now() - start) / 1000;
  const cpuMicros = after.cpu.user + after.cpu.system - before.cpu.user - before.cpu.system;
  const live = clients.filter(ws => ws.readyState === WebSocket.OPEN).length;
  console.log(JSON.stringify({ clients: 32, drivers, registryVehicles: before.vehicles, restingVehicles: 112, connectedAtEnd: live, requestedHz: 20,
    durationSeconds: +elapsed.toFixed(3), sentPoses: sent,
    acceptedPoses: after.stats.poses - before.stats.poses,
    rejected: after.stats.rejected - before.stats.rejected, receivedMessages: received,
    serverCpuPercentOfOneCore: +(cpuMicros / (elapsed * 10_000)).toFixed(2),
    serverCpuMilliseconds: +(cpuMicros / 1000).toFixed(2),
    serverBytesInPerSecond: Math.round((after.stats.bytesIn - before.stats.bytesIn) / elapsed),
    serverBytesOutPerSecond: Math.round((after.stats.bytesOut - before.stats.bytesOut) / elapsed),
    byteAccounting: 'JSON payload, excluding WebSocket/TCP/TLS framing', errors: failures }, null, 2));
  if (live !== 32 || failures || after.stats.rejected !== before.stats.rejected || after.stats.poses - before.stats.poses < sent * 0.95) process.exitCode = 1;
} finally {
  clearInterval(timer); clearTimeout(deadline);
  for (const ws of clients) ws.terminate();
  if (child.exitCode === null) {
    const exited = once(child, 'exit'); child.kill('SIGTERM'); await exited;
  }
  rmSync(directory, { recursive: true, force: true });
}
