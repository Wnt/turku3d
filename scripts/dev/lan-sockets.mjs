// Ten production sockets follow the same directed road routes as ten-drivers.py.
import fs from 'node:fs';
import { setTimeout as delay } from 'node:timers/promises';
import WebSocket from '../../server/node_modules/ws/wrapper.mjs';

const [routeFile, output, secondsArg = '180', speedArg = '50', url = 'wss://turku.lab.madekivi.fi/'] = process.argv.slice(2);
if (!routeFile || !output) throw Error('Usage: lan-sockets.mjs ROUTES OUTPUT [seconds speed-kmh websocket-url]');
const routes = JSON.parse(fs.readFileSync(routeFile, 'utf8'));
const seconds = Number(secondsArg), speed = Number(speedArg) / 3.6;
const clients = [], errors = [], latencies = [];
let startedAt;
let timer, pingTimer, sent = 0, received = 0, started;
const percentile = q => latencies.length ? latencies[Math.min(latencies.length - 1, Math.floor(latencies.length * q))] : null;
const hardStop = setTimeout(() => {
  errors.push('socket deadline exceeded');
  for (const ws of clients) ws.terminate();
}, (seconds + 25) * 1000);

function position(route, distance) {
  for (const [start, end, a, b] of route.segments) {
    if (distance > end) continue;
    const fraction = Math.max(0, (distance - start) / (end - start));
    return { x: a[0] + (b[0] - a[0]) * fraction, z: a[1] + (b[1] - a[1]) * fraction,
      dx: (b[0] - a[0]) / (end - start), dz: (b[1] - a[1]) / (end - start) };
  }
  const b = route.segments.at(-1)[3];
  return { x: b[0], z: b[1], dx: 0, dz: 0 };
}

try {
  if (routes.length !== 10) throw Error('Exactly ten real routes required');
  await Promise.all(routes.map(() => new Promise((resolve, reject) => {
    const ws = new WebSocket(url, { origin: 'https://wnt.github.io', handshakeTimeout: 10000 });
    clients.push(ws);
    ws.on('open', () => ws.send(JSON.stringify({ v: 2, type: 'hello' })));
    ws.on('error', error => { errors.push(error.message); reject(error); });
    ws.on('close', () => { if (!started || performance.now() - started < seconds * 1000) errors.push('premature close'); });
    ws.on('message', data => {
      const message = JSON.parse(data.toString());
      if (message.type === 'welcome') resolve();
      if (message.type === 'poses') received += message.poses.length;
      if (message.type === 'error') errors.push(message.code);
    });
    ws.on('pong', data => {
      const sentAt = Number(data.toString());
      if (Number.isFinite(sentAt)) latencies.push(performance.now() - sentAt);
    });
  })));
  await delay(1100); // The hello also consumes the production token budget.
  started = performance.now();
  startedAt = Date.now() / 1000;
  timer = setInterval(() => {
    const distance = (performance.now() - started) / 1000 * speed;
    clients.forEach((ws, i) => {
      if (ws.readyState !== WebSocket.OPEN) return;
      const p = position(routes[i], distance);
      ws.send(JSON.stringify({ v: 2, type: 'pose', t: Date.now(), E: p.x, N: -p.z,
        H: 4, yaw: Math.atan2(p.dz, p.dx), vE: p.dx * speed, vN: -p.dz * speed, vH: 0,
        mode: 'car', vehicle: 'player', carrying: false }));
      sent++;
    });
  }, 50);
  pingTimer = setInterval(() => {
    for (const ws of clients) if (ws.readyState === WebSocket.OPEN) ws.ping(String(performance.now()));
  }, 1000);
  await delay(seconds * 1000);
} catch (error) {
  errors.push(error.message);
} finally {
  clearInterval(timer); clearInterval(pingTimer); clearTimeout(hardStop);
  latencies.sort((a, b) => a - b);
  const connected = clients.filter(ws => ws.readyState === WebSocket.OPEN).length;
  const result = { drivers: 10, real_routes: true, seconds, requested_hz: 20, sent,
    started_at: startedAt, ended_at: Date.now() / 1000,
    received_peer_poses: received, connected_at_end: connected,
    ping_rtt_ms: { count: latencies.length, p50: percentile(.5), p95: percentile(.95), max: latencies.at(-1) ?? null },
    errors, passed: connected === 10 && errors.length === 0 && latencies.length >= seconds * 5 && percentile(.95) < 250 };
  fs.writeFileSync(output, JSON.stringify(result, null, 2) + '\n');
  console.log(JSON.stringify(result));
  for (const ws of clients) ws.close(1000, 'LAN smoke complete');
  setTimeout(() => { for (const ws of clients) ws.terminate(); }, 1000).unref();
  if (!result.passed) process.exitCode = 1;
}
