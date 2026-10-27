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
  env: { ...process.env, TURKU_MP_PORT: '0', TURKU_MP_SCORES: directory },
  stdio: ['ignore', 'ignore', 'inherit', 'ipc'],
});
const clients = [];
let timer, sent = 0, received = 0, failures = 0;
const deadline = setTimeout(() => { console.error('Load test timed out'); child.kill('SIGTERM'); process.exitCode = 1; }, (seconds + 15) * 1000);
try {
  const [{ port }] = await once(child, 'message');
  await Promise.all(Array.from({ length: 32 }, async () => {
    const ws = new WebSocket(`ws://127.0.0.1:${port}`, { origin: 'https://wnt.github.io' });
    clients.push(ws);
    ws.on('error', () => { failures++; });
    ws.on('message', () => { received++; });
    await once(ws, 'open');
    const welcome = once(ws, 'message');
    ws.send(JSON.stringify({ v: 1, type: 'hello' }));
    const [message] = await welcome;
    if (JSON.parse(message).type !== 'welcome') throw Error('Expected welcome');
  }));
  await delay(1100); // Hello is in the rate budget; measure steady-state pose traffic.
  async function metrics() {
    const response = once(child, 'message'); child.send('metrics'); return (await response)[0];
  }
  const before = await metrics();
  received = 0;
  const start = performance.now();
  timer = setInterval(() => {
    const t = performance.now();
    clients.forEach((ws, i) => {
      if (ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify({ v: 1, type: 'pose', t, E: 173 + i + Math.sin(t / 1000),
        N: -252 + Math.cos(t / 1000), H: 3.7, yaw: 0.1, vE: 1, vN: 0, vH: 0,
        mode: i % 2 ? 'car' : 'foot', carrying: false }));
      sent++;
    });
  }, 50);
  await delay(seconds * 1000);
  clearInterval(timer);
  const after = await metrics();
  const elapsed = (performance.now() - start) / 1000;
  const cpuMicros = after.cpu.user + after.cpu.system - before.cpu.user - before.cpu.system;
  const live = clients.filter(ws => ws.readyState === WebSocket.OPEN).length;
  console.log(JSON.stringify({ clients: 32, connectedAtEnd: live, requestedHz: 20,
    durationSeconds: +elapsed.toFixed(3), sentPoses: sent,
    acceptedPoses: after.stats.poses - before.stats.poses,
    rejected: after.stats.rejected - before.stats.rejected, receivedMessages: received,
    serverCpuPercentOfOneCore: +(cpuMicros / (elapsed * 10_000)).toFixed(2),
    serverCpuMilliseconds: +(cpuMicros / 1000).toFixed(2),
    serverBytesInPerSecond: Math.round((after.stats.bytesIn - before.stats.bytesIn) / elapsed),
    serverBytesOutPerSecond: Math.round((after.stats.bytesOut - before.stats.bytesOut) / elapsed),
    byteAccounting: 'JSON payload, excluding WebSocket/TCP/TLS framing', errors: failures }, null, 2));
  if (live !== 32 || failures || after.stats.poses - before.stats.poses < sent * 0.95) process.exitCode = 1;
} finally {
  clearInterval(timer); clearTimeout(deadline);
  for (const ws of clients) ws.terminate();
  if (child.exitCode === null) {
    const exited = once(child, 'exit'); child.kill('SIGTERM'); await exited;
  }
  rmSync(directory, { recursive: true, force: true });
}
