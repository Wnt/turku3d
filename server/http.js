import http from 'node:http';
import { WebSocketServer, WebSocket } from 'ws';
import { Room, LIMITS, VERSION } from './room.js';

/** Cities with their own room besides Turku's (game/src/core/city.ts): players in different cities never share poses,
 *  vehicles, roads, poles, rounds or a scoreboard. Turku's socket is `/`, another city's `/city/<name>`; its scores are
 *  `/live.json?city=<name>` and `/scores/...?city=<name>`, stored under `<scores>/cities/<name>/`. */
export const CITY_ROOMS = Object.freeze(['espoo', 'amsterdam', 'rotterdam']);

/** Pages and localhost, plus the exact `extra` origins of a share host's own server (TURKU_MP_ORIGINS, index.js). */
export function allowedOrigin(origin, extra = []) {
  if (origin === 'https://wnt.github.io' || (typeof origin === 'string' && extra.includes(origin))) return true;
  if (typeof origin !== 'string' || !/^http:\/\/localhost(?::[0-9]+)?$/.test(origin)) return false;
  try { return new URL(origin).port !== '0'; } catch { return false; }
}

export function createServer({ store, now = Date.now, onConnection = () => {}, origins = [], cities = CITY_ROOMS }) {
  const room = new Room({ store, now });
  const stores = new Map([['turku', store]]);
  const rooms = new Map([['turku', room]]);
  for (const city of cities) {
    const cityStore = store.forCity ? store.forCity(city) : store;
    stores.set(city, cityStore);
    rooms.set(city, new Room({ store: cityStore, now }));
  }
  /** The room of a socket path, or null for an unknown one. */
  const roomAt = path => path === '/' ? room : /^\/city\/[a-z]{2,24}$/.test(path) && path.slice(6) !== 'turku' ? rooms.get(path.slice(6)) ?? null : null;
  const sockets = new WebSocketServer({ noServer: true, maxPayload: LIMITS.messageBytes, perMessageDeflate: false });
  const server = http.createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Vary', 'Origin');
    const answer = (status, value) => { res.writeHead(status); res.end(JSON.stringify(value)); };
    if (req.headers.origin !== undefined) {
      if (!allowedOrigin(req.headers.origin, origins)) { answer(403, { error: 'origin' }); return; }
      res.setHeader('Access-Control-Allow-Origin', req.headers.origin);
      res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
    }
    if (req.method === 'OPTIONS') { answer(204, null); return; }
    if (req.method !== 'GET') { res.setHeader('Allow', 'GET, OPTIONS'); answer(405, { error: 'method' }); return; }
    try {
      for (const r of rooms.values()) r.tick();
      const url = new URL(req.url, 'http://localhost'), path = url.pathname;
      const city = url.searchParams.get('city') ?? 'turku';
      const here = rooms.get(city), hereStore = stores.get(city);
      if (path === '/healthz') {
        answer(200, { ok: true, v: VERSION, hour: room.hour, connections: room.connections.size,
          online: room.livePlayers().length, stats: room.stats,
          cities: Object.fromEntries([...rooms].filter(([name]) => name !== 'turku').map(([name, r]) =>
            [name, { connections: r.connections.size, online: r.livePlayers().length }])) });
      } else if ((path === '/live.json' || path.startsWith('/scores/')) && !here) {
        answer(404, { error: 'city' });
      } else if (path === '/live.json') {
        answer(200, { hour: here.hour, players: here.livePlayers(), scores: here.scores() });
      } else if (path === '/scores/index.json') {
        answer(200, hereStore.index());
      } else if (path.startsWith('/scores/') && path.endsWith('.json')) {
        const record = hereStore.read(decodeURIComponent(path.slice(8, -5)));
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
    if (!allowedOrigin(req.headers.origin, origins)) { refuse(403, 'Forbidden'); return; }
    const target = roomAt(req.url);
    if (!target) { refuse(404, 'Not Found'); return; }
    if (sockets.clients.size >= LIMITS.connections) { refuse(503, 'Service Unavailable'); return; }
    sockets.handleUpgrade(req, socket, head, ws => sockets.emit('connection', ws, target));
  });
  sockets.on('connection', (ws, room) => {
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
  const tick = setInterval(() => { for (const r of rooms.values()) { r.tick(); r.flushPoses(); } }, 50);
  const ping = setInterval(() => {
    for (const ws of sockets.clients) {
      if (!ws.alive) { ws.terminate(); continue; }
      ws.alive = false; ws.ping();
    }
  }, 30_000);
  return {
    server, room, rooms,
    async close() {
      clearInterval(tick); clearInterval(ping);
      for (const ws of sockets.clients) ws.terminate();
      await new Promise(resolve => sockets.close(resolve));
      await new Promise(resolve => server.close(resolve));
    },
  };
}
