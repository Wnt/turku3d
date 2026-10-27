import { randomBytes, randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { hourAt } from './hours.js';
import { nickname } from './nicks.js';
import { validDelivery } from './economy.js';

export const VERSION = 1;
export const LIMITS = Object.freeze({ connections: 32, acceptedPerSecond: 20, disconnectPerSecond: 25,
  messageBytes: 65_536, idleMs: 60_000, helloMs: 5_000, orders: 10_000 });
export const SPAWNS = JSON.parse(readFileSync(new URL('./spawns.json', import.meta.url))).points;
const identifier = value => typeof value === 'string' && /^[A-Za-z0-9_.:-]{1,128}$/.test(value);
const finite = (n, max) => typeof n === 'number' && Number.isFinite(n) && Math.abs(n) <= max;

export class Room {
  constructor({ store, now = Date.now }) {
    this.store = store;
    this.now = now;
    this.hour = hourAt(now());
    this.players = new Map();
    this.sessions = new Map();
    this.connections = new Set();
    this.usedNicks = new Set();
    this.brokenPoles = new Map();
    this.spawnIndex = 0;
    this.warned = false;
    this.lastPruneDay = null;
    this.stats = { rejected: 0, deliveries: 0, poses: 0, bytesIn: 0, bytesOut: 0 };
  }

  connect(send, close) {
    if (this.connections.size >= LIMITS.connections) return null;
    const c = { send, close, player: null, joinedAt: this.now(), arrivals: [], tokens: LIMITS.acceptedPerSecond, refilledAt: this.now() };
    this.connections.add(c);
    return c;
  }

  send(c, type, fields = {}) {
    if (!this.connections.has(c)) return;
    const data = JSON.stringify({ v: VERSION, type, ...fields });
    this.stats.bytesOut += Buffer.byteLength(data);
    c.send(data);
  }

  broadcast(type, fields) {
    for (const c of this.connections) if (c.player) this.send(c, type, fields);
  }

  disconnect(c) {
    if (!this.connections.delete(c)) return;
    if (c.player?.connection === c) {
      c.player.connection = null;
      c.player.online = false;
      c.player.dirty = false;
      this.presence();
    }
  }

  drop(c, code, reason) {
    this.disconnect(c);
    c.close(code, reason);
  }

  reject() { this.stats.rejected++; }

  receive(c, data, binary = false) {
    if (!this.connections.has(c)) return;
    const now = this.now();
    this.tick(now);
    if (!this.connections.has(c)) return;
    this.stats.bytesIn += Buffer.byteLength(data);
    c.arrivals = c.arrivals.filter(t => t > now - 1000);
    c.arrivals.push(now);
    if (c.arrivals.length > LIMITS.disconnectPerSecond) {
      this.reject(); this.drop(c, 1008, 'rate'); return;
    }
    // A replenishing budget tolerates timer/packet jitter at 20 Hz. The separate
    // arrival window still disconnects floods above 25/s, including rejected data.
    c.tokens = Math.min(LIMITS.acceptedPerSecond,
      c.tokens + Math.max(0, now - c.refilledAt) * LIMITS.acceptedPerSecond / 1000);
    c.refilledAt = now;
    if (c.tokens < 1) { this.reject(); return; }
    c.tokens--;
    if (binary || Buffer.byteLength(data) > LIMITS.messageBytes) { this.reject(); return; }
    let m;
    try { m = JSON.parse(data.toString()); } catch { this.reject(); return; }
    if (!m || typeof m !== 'object' || Array.isArray(m)) { this.reject(); return; }
    if (m.v !== VERSION) {
      this.reject(); this.send(c, 'error', { code: 'protocol', reload: true });
      this.drop(c, 1002, 'protocol'); return;
    }
    if (m.type === 'hello' && !c.player) { this.hello(c, m, now); return; }
    if (!c.player) { this.reject(); return; }
    if (m.type === 'pose') { this.pose(c.player, m, now); return; }
    if (m.type === 'delivery') { this.delivery(c.player, m); return; }
    if (m.type === 'world') { this.breakPole(c.player, m, now); return; }
    if (m.type === 'fine') { this.fine(c.player, m); return; }
    this.reject();
  }

  hello(c, m, now) {
    if (m.session !== undefined && (typeof m.session !== 'string' || !/^[a-f0-9]{64}$/.test(m.session))) {
      this.reject(); return;
    }
    let p = this.sessions.get(m.session);
    if (p) {
      if (p.connection) this.drop(p.connection, 4001, 'session replaced');
    } else {
      const nick = nickname(this.usedNicks);
      if (!nick) {
        this.reject(); this.send(c, 'error', { code: 'nicknames', reload: false });
        this.drop(c, 1013, 'hour full'); return;
      }
      this.usedNicks.add(nick);
      p = { id: randomUUID(), session: randomBytes(32).toString('hex'), nick,
        spawn: SPAWNS[this.spawnIndex++ % SPAWNS.length], money: 0, deliveries: 0,
        orders: new Set(), finedPoles: new Set(), pose: null, dirty: false };
      this.players.set(p.id, p);
      this.sessions.set(p.session, p);
    }
    c.player = p;
    p.connection = c;
    p.online = true;
    p.lastPoseAt = now;
    this.welcome(c);
    this.presence();
  }

  welcome(c) {
    const p = c.player;
    this.send(c, 'welcome', { session: p.session, you: { id: p.id, nick: p.nick, spawn: p.spawn },
      hour: this.hour, players: this.livePlayers(), scores: this.scores(), brokenPoles: [...this.brokenPoles.values()] });
  }

  livePlayers() {
    return [...this.players.values()].filter(p => p.online).map(p =>
      ({ id: p.id, nick: p.nick, spawn: p.spawn, pose: p.pose }));
  }

  scores() {
    const sorted = [...this.players.values()].sort((a, b) => b.money - a.money || a.nick.localeCompare(b.nick));
    let rank = 0;
    return sorted.map((p, i) => {
      if (!i || p.money !== sorted[i - 1].money) rank = i + 1;
      return { nick: p.nick, money: p.money, deliveries: p.deliveries, rank, online: p.online };
    });
  }

  presence() {
    this.broadcast('players', { players: this.livePlayers() });
    this.broadcast('scores', { scores: this.scores() });
  }

  pose(p, m, now) {
    if (!finite(m.t, Number.MAX_SAFE_INTEGER) || m.t < 0 || !finite(m.E, 100_000) || !finite(m.N, 100_000)
      || !finite(m.H, 10_000) || !finite(m.yaw, Math.PI * 2)
      || !['vE', 'vN', 'vH'].every(k => finite(m[k], 200))
      || !['foot', 'car', 'scooter', 'truck'].includes(m.mode) || typeof m.carrying !== 'boolean'
      || !(m.mode === 'foot' ? m.vehicle === null : m.mode === 'scooter' || m.mode === 'truck' ? m.vehicle === m.mode
        : m.vehicle === 'player' || (typeof m.vehicle === 'string' && m.vehicle.length <= 64
          && /^[a-z0-9-]+\/[a-z0-9-]+$/.test(m.vehicle)))) {
      this.reject(); return;
    }
    const { t, E, N, H, yaw, vE, vN, vH, mode, vehicle, carrying } = m;
    p.pose = { t, E, N, H, yaw, vE, vN, vH, mode, vehicle, carrying };
    p.dirty = true;
    p.lastPoseAt = now;
    const wasOnline = p.online;
    p.online = true;
    this.stats.poses++;
    if (!wasOnline) this.presence();
  }

  delivery(p, m) {
    if (!identifier(m.orderId) || !identifier(m.addressId) || !validDelivery(m)
      || p.orders.has(m.orderId) || p.orders.size >= LIMITS.orders) { this.reject(); return; }
    p.orders.add(m.orderId);
    p.money += m.payout + m.tip;
    p.deliveries++;
    this.stats.deliveries++;
    this.broadcast('scores', { scores: this.scores() });
  }

  breakPole(p, m, now) {
    if (m.action !== 'break' || !identifier(m.poleId) || !finite(m.t, Number.MAX_SAFE_INTEGER)
      || m.t < 0 || m.by !== p.id || this.brokenPoles.has(m.poleId)) { this.reject(); return; }
    const event = { action: 'break', poleId: m.poleId, by: p.id, t: m.t };
    this.brokenPoles.set(m.poleId, event);
    this.broadcast('world', event);
  }

  fine(p, m) {
    if (m.reason !== 'pole' || !identifier(m.poleId) || m.amount !== 50
      || this.brokenPoles.get(m.poleId)?.by !== p.id || p.finedPoles.has(m.poleId)) { this.reject(); return; }
    p.finedPoles.add(m.poleId);
    p.money -= 50;
    this.broadcast('scores', { scores: this.scores() });
  }

  flushPoses() {
    const dirty = [...this.players.values()].filter(p => p.online && p.dirty);
    if (!dirty.length) return;
    for (const c of this.connections) {
      if (!c.player) continue;
      const poses = dirty.filter(p => p !== c.player).map(p => ({ id: p.id, ...p.pose }));
      if (poses.length) this.send(c, 'poses', { poses });
    }
    for (const p of dirty) p.dirty = false;
  }

  tick(now = this.now()) {
    while (now >= Date.parse(this.hour.end)) {
      this.store.write(this.hour, this.scores().map(({ nick, money, deliveries, rank }) => ({ nick, money, deliveries, rank })));
      const continuing = [...this.players.values()].filter(p => p.connection);
      this.hour = hourAt(Date.parse(this.hour.end));
      this.brokenPoles.clear();
      this.players.clear(); this.sessions.clear(); this.usedNicks.clear();
      for (const p of continuing) {
        p.nick = nickname(this.usedNicks, p.nick);
        this.usedNicks.add(p.nick);
        p.money = 0; p.deliveries = 0;
        p.finedPoles.clear();
        this.players.set(p.id, p); this.sessions.set(p.session, p);
      }
      this.warned = false;
      this.broadcast('hour_reset', { hour: this.hour });
      for (const c of this.connections) if (c.player) this.welcome(c);
      this.presence();
    }
    const remaining = Date.parse(this.hour.end) - now;
    if (!this.warned && remaining <= 10_000) {
      this.warned = true;
      this.broadcast('hour_end_in', { seconds: Math.ceil(remaining / 1000) });
    }
    let changed = false;
    for (const c of this.connections) {
      if (!c.player && now - c.joinedAt >= LIMITS.helloMs) this.drop(c, 1008, 'hello timeout');
    }
    for (const p of this.players.values()) {
      if (p.online && now - p.lastPoseAt >= LIMITS.idleMs) {
        p.online = false; p.dirty = false; changed = true;
      }
    }
    if (changed) this.presence();
    const day = this.hour.id.slice(0, 10);
    if (day !== this.lastPruneDay) { this.store.prune(now); this.lastPruneDay = day; }
  }
}
