import http from 'node:http';
import { WebSocketServer, WebSocket } from 'ws';
import { Room, LIMITS } from './room.js';

export function allowedOrigin(origin) {
  if (origin === 'https://wnt.github.io') return true;
  if (typeof origin !== 'string' || !/^http:\/\/localhost(?::[0-9]+)?$/.test(origin)) return false;
  try { return new URL(origin).port !== '0'; } catch { return false; }
}

export function createServer({ store, now = Date.now, onConnection = () => {} }) {
  const room = new Room({ store, now });
  const sockets = new WebSocketServer({ noServer: true, maxPayload: LIMITS.messageBytes, perMessageDeflate: false });
  const server = http.createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Vary', 'Origin');
    const answer = (status, value) => { res.writeHead(status); res.end(JSON.stringify(value)); };
    if (req.headers.origin !== undefined) {
      if (!allowedOrigin(req.headers.origin)) { answer(403, { error: 'origin' }); return; }
      res.setHeader('Access-Control-Allow-Origin', req.headers.origin);
      res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
    }
    if (req.method === 'OPTIONS') { answer(204, null); return; }
    if (req.method !== 'GET') { res.setHeader('Allow', 'GET, OPTIONS'); answer(405, { error: 'method' }); return; }
    try {
      room.tick();
      const path = new URL(req.url, 'http://localhost').pathname;
      if (path === '/healthz') {
        answer(200, { ok: true, v: 1, hour: room.hour, connections: room.connections.size,
          online: room.livePlayers().length, stats: room.stats });
      } else if (path === '/live.json') {
        answer(200, { hour: room.hour, players: room.livePlayers(), scores: room.scores() });
      } else if (path === '/scores/index.json') {
        answer(200, store.index());
      } else if (path.startsWith('/scores/') && path.endsWith('.json')) {
        const record = store.read(decodeURIComponent(path.slice(8, -5)));
        answer(record ? 200 : 404, record ?? { error: 'not_found' });
      } else answer(404, { error: 'not_found' });
    } catch (error) {
      if (error instanceof URIError || error instanceof TypeError) answer(400, { error: 'request' });
      else {
        console.error('Score storage failure:', error.code ?? error.name);
        answer(503, { error: 'storage' });
      }
    }
  });
  server.requestTimeout = 10_000;
  server.headersTimeout = 10_000;
  server.keepAliveTimeout = 5000;
  server.on('upgrade', (req, socket, head) => {
    const refuse = (status, reason) => {
      room.reject();
      socket.end(`HTTP/1.1 ${status} ${reason}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
    };
    if (!allowedOrigin(req.headers.origin)) { refuse(403, 'Forbidden'); return; }
    if (req.url !== '/') { refuse(404, 'Not Found'); return; }
    if (sockets.clients.size >= LIMITS.connections) { refuse(503, 'Service Unavailable'); return; }
    sockets.handleUpgrade(req, socket, head, ws => sockets.emit('connection', ws));
  });
  sockets.on('connection', ws => {
    onConnection();
    ws.alive = true;
    ws.on('pong', () => { ws.alive = true; });
    const c = room.connect(data => {
      if (ws.readyState !== WebSocket.OPEN) return;
      if (ws.bufferedAmount > 262_144) { room.disconnect(c); ws.terminate(); return; }
      ws.send(data);
    }, (code, reason) => ws.close(code, reason));
    if (!c) { ws.close(1013, 'room full'); return; }
    ws.on('message', (data, binary) => room.receive(c, data, binary));
    ws.on('close', () => room.disconnect(c));
    ws.on('error', () => { room.reject(); room.disconnect(c); });
  });
  const tick = setInterval(() => { room.tick(); room.flushPoses(); }, 50);
  const ping = setInterval(() => {
    for (const ws of sockets.clients) {
      if (!ws.alive) { ws.terminate(); continue; }
      ws.alive = false; ws.ping();
    }
  }, 30_000);
  return {
    server, room,
    async close() {
      clearInterval(tick); clearInterval(ping);
      for (const ws of sockets.clients) ws.terminate();
      await new Promise(resolve => sockets.close(resolve));
      await new Promise(resolve => server.close(resolve));
    },
  };
}
