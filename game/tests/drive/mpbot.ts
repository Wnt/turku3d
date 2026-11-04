// Multiplayer bot clients for tools/drivetest/mp.mjs: each is a protocol v2 client (server/PROTOCOL.md) that drives
// its own car with the autopilot's follower (gameplay/autopilot/follow.ts) on the real vehicle physics
// (physics/world.ts) over the pinned tiles' collision. Like the game, it sends its chassis pose every 55 ms and puts
// the other players' cars into its physics as kinematic bodies (net/remotes.ts), so bots and browser players can
// collide. Every 250 ms it logs its own pose and the latest pose it holds of every other player.
import fs from 'node:fs';
import path from 'node:path';
import * as RAPIER from '@dimforge/rapier3d';
// The server's own pinned ws (npm ci --prefix server); the game has no ws dependency.
// @ts-expect-error untyped path import
import { WebSocket } from '../../../server/node_modules/ws/wrapper.mjs';
import { PhysicsWorld } from '../../src/physics/world';
import { COLLISION_TILE } from '../../src/core/origin';
import { DriveFollower } from '../../src/gameplay/autopilot/follow';
import { LaneRouter, tilesBetween } from '../../src/gameplay/autopilot/lanes';
import { makePath, sampleAt, type Path } from '../../src/gameplay/autopilot/path';
import { vehicleGeometry, vehiclePose } from '../../src/gameplay/autopilot/autopilot';
import type { RemoteBody } from '../../src/physics/protocol';
// @ts-expect-error plain ESM tool
import { TileCache, farm } from '../../../tools/drivetest/fetch.mjs';

/** `go`: a file whose appearance starts the drives (the browsers join first); `seconds` counts from then. */
export interface BotOptions { url: string; origin: string; dir: string; out: string; bots: number; seconds: number; go?: string }

const VEHICLE_POSE_HEIGHT = 0.6;
const POSE_MS = 55;

interface Remote { nick: string; pose: { E: number; N: number; H: number; yaw: number; rot?: { x: number; y: number; z: number; w: number }; mode: string }; at: number }

class Bot {
  ws!: WebSocket;
  nick = '';
  id = '';
  spawn = { E: 0, N: 0, yaw: 0 };
  readonly remotes = new Map<string, Remote>();
  readonly nicks = new Map<string, string>();
  pw: PhysicsWorld | null = null;
  follower: DriveFollower | null = null;
  path: Path | null = null;
  state = 'joining';
  alive = true;
  lastPose = 0;
  constructor(readonly index: number) {}

  async connect(url: string, origin: string) {
    this.ws = new WebSocket(url, { origin });
    await new Promise<void>((resolve, reject) => { this.ws.once('open', () => resolve()); this.ws.once('error', reject); });
    const welcome = new Promise<void>(resolve => this.ws.on('message', raw => {
      const msg = JSON.parse(String(raw));
      if (msg.type === 'welcome') {
        this.id = msg.you.id; this.nick = msg.you.nick; this.spawn = msg.you.spawn;
        for (const p of msg.players ?? []) { this.nicks.set(p.id, p.nick); if (p.pose && p.id !== this.id) this.remotes.set(p.id, { nick: p.nick, pose: p.pose, at: Date.now() }); }
        resolve();
      } else if (msg.type === 'players') {
        const ids = new Set<string>();
        for (const p of msg.players ?? []) { ids.add(p.id); this.nicks.set(p.id, p.nick); }
        for (const id of [...this.remotes.keys()]) if (!ids.has(id)) this.remotes.delete(id);
      } else if (msg.type === 'poses') {
        for (const p of msg.poses ?? []) if (p.id !== this.id) this.remotes.set(p.id, { nick: this.nicks.get(p.id) ?? p.id, pose: p, at: Date.now() });
      }
    }));
    this.ws.send(JSON.stringify({ v: 2, type: 'hello' }));
    await welcome;
  }

  /** Physics around both routes and the car at the spawn's nearest lane, facing along it. */
  async prepare(cache: InstanceType<typeof TileCache>, router: LaneRouter, legs: { x: number; z: number }[]) {
    const start = { x: this.spawn.E + 4 * (this.index % 3), z: -this.spawn.N + 3 * Math.floor(this.index / 3) };
    const routes: Path[] = [];
    let from: { x: number; z: number; hx?: number; hz?: number } = start;
    for (const leg of legs) {
      const r = router.route(from, leg);
      if (!r) throw new Error(`bot ${this.index}: no route to ${leg.x.toFixed(0)},${leg.z.toFixed(0)}`);
      routes.push(r.path); from = { x: leg.x, z: leg.z };
    }
    // One path: out, then across (the follower slows at the joint only as far as the curvature asks).
    const points = routes.flatMap((p, k) => Array.from({ length: p.cum.length }, (_, i) => ({ x: p.points[2 * i], z: p.points[2 * i + 1], ...(p.ys ? { y: p.ys[i] } : {}) })).slice(k ? 1 : 0));
    this.path = makePath(points);
    const pw = new PhysicsWorld(RAPIER);
    const cells = new Set<string>();
    for (let s = 0; s <= this.path.length; s += 10) {
      const q = sampleAt(this.path, s), i = Math.floor(q.x / COLLISION_TILE), j = Math.floor(q.z / COLLISION_TILE);
      for (let a = -1; a <= 1; a++) for (let b = -1; b <= 1; b++) cells.add(`c_${i + a}_${j + b}`);
    }
    for (const id of cells) { const b = await cache.cell(id); if (b) await pw.loadTile(id, b); }
    pw.world.step();
    const q = sampleAt(this.path, 0);
    const hit = pw.raycast({ x: q.x, y: (q.y ?? 30) + 3, z: q.z }, { x: 0, y: -1, z: 0 }, 12, 'solid');
    pw.spawnVehicle({ x: q.x, y: (hit?.point.y ?? q.y ?? 0) + 0.9, z: q.z }, Math.atan2(-q.hz, q.hx));
    for (let k = 0; k < 30; k++) pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
    this.pw = pw;
    this.follower = new DriveFollower(this.path, vehicleGeometry('car'), { speed: 12, style: 'normal' });
    this.state = 'waiting';
  }

  step(dt: number) {
    if (!this.pw || !this.follower || !this.alive) return;
    // Other players' cars as kinematic bodies, like net/remotes.ts.
    const bodies: RemoteBody[] = [];
    for (const [id, r] of this.remotes) if (r.pose.mode !== 'foot') bodies.push({ id, pos: { x: r.pose.E, y: r.pose.H + VEHICLE_POSE_HEIGHT, z: -r.pose.N }, yaw: r.pose.yaw, rot: r.pose.rot, mode: r.pose.mode as 'car' });
    this.pw.setRemotes(bodies);
    const v = this.pw.vehicleState()!;
    const step = this.follower.step(vehiclePose(v), dt);
    if (step.status === 'arrived') this.state = 'arrived';
    else if (this.follower.recoveries > 4) this.state = 'failed';
    this.pw.fixedStep(null, this.state === 'driving' ? step.command : { throttle: 0, steer: 0, brake: 0, handbrake: true });
  }

  sendPose(now: number) {
    if (!this.pw || !this.alive || this.ws.readyState !== WebSocket.OPEN || now - this.lastPose < POSE_MS) return;
    this.lastPose = now;
    const v = this.pw.vehicleState()!;
    const { x, y, z, w } = v.rot, fx = 1 - 2 * (y * y + z * z), fz = 2 * (x * z - w * y);
    const yaw = Math.atan2(-fz, fx) - Math.PI / 2;
    this.ws.send(JSON.stringify({ v: 2, type: 'pose', t: now, E: v.pos.x, N: -v.pos.z, H: v.pos.y - VEHICLE_POSE_HEIGHT,
      yaw: Math.atan2(Math.sin(yaw), Math.cos(yaw)), vE: v.linvel.x, vN: -v.linvel.z, vH: v.linvel.y, mode: 'car', vehicle: 'player',
      carrying: false, rot: v.rot, wheels: v.wheels.map(wh => ({ pos: wh.pos, steer: wh.steer, rotation: wh.rotation, contact: wh.contact, suspension: wh.suspension })) }));
  }

  sample() {
    if (!this.pw || !this.alive) return null;
    const v = this.pw.vehicleState()!;
    return { client: 100 + this.index, nick: this.nick, bot: true, wall: Date.now(), mode: 'car',
      self: { x: v.pos.x, y: v.pos.y, z: v.pos.z, speed: v.speed }, impact: v.impact ?? null, connected: this.ws.readyState === WebSocket.OPEN,
      remotes: [...this.remotes.values()].filter(r => Date.now() - r.at < 5000).map(r => ({ nick: r.nick, x: r.pose.E, z: -r.pose.N })),
      trip: { state: this.state, reason: null, s: this.follower?.s ?? 0, length: this.path?.length ?? 0 } };
  }

  leave() { this.alive = false; this.ws.close(); this.pw?.world.free(); this.pw = null; }
}

export async function runBots(o: BotOptions) {
  const cache = new TileCache(o.dir, farm(path.join(o.dir, 'tile-service.json')));
  const out = fs.createWriteStream(o.out, { flags: 'a' });
  const write = (row: unknown) => out.write(JSON.stringify(row) + '\n');
  const bots: Bot[] = [];
  const router = new LaneRouter();
  let goals: { x: number; z: number }[] = [];
  const add = async (index: number) => {
    const bot = new Bot(index);
    await bot.connect(o.url, o.origin);
    if (!goals.length) {
      const c = { x: bot.spawn.E, z: -bot.spawn.N };
      for (const t of tilesBetween({ x: c.x - 400, z: c.z - 400 }, { x: c.x + 400, z: c.z + 400 }, 0)) { const r = await cache.roads(t); if (r) router.setTile(r); }
      goals = [[320, 0], [0, 320], [-320, 0], [0, -320]].map(([dx, dz]) => {
        const near = router.near({ x: c.x + dx, z: c.z + dz }, 120, 1)[0];
        if (!near) return { x: c.x + dx, z: c.z + dz };
        const l = near.lane, i = Math.min(l.points.length / 2 - 1, Math.round(l.points.length / 4));
        return { x: l.points[2 * i], z: l.points[2 * i + 1] };
      });
    }
    await bot.prepare(cache, router, [goals[index % 4], goals[(index + 2) % 4]]);
    write({ event: 'join', t: Date.now(), client: 100 + index, nick: bot.nick, bot: true });
    bots.push(bot);
    return bot;
  };
  for (let i = 0; i < o.bots; i++) await add(i);
  write({ event: 'ready', t: Date.now(), goals });
  // Hold at the start (still sending poses) until the go file appears.
  let t0 = o.go ? Infinity : Date.now();
  if (!o.go) for (const b of bots) b.state = 'driving';
  let last = performance.now(), acc = 0, lastSample = 0, left = false, joined = false;
  await new Promise<void>(resolve => {
    const timer = setInterval(() => {
      const now = performance.now();
      acc += Math.min(0.25, (now - last) / 1000); last = now;
      while (acc >= 1 / 60) { for (const b of bots) b.step(1 / 60); acc -= 1 / 60; }
      for (const b of bots) b.sendPose(now);
      if (Date.now() - lastSample >= 250) { lastSample = Date.now(); for (const b of bots) { const s = b.sample(); if (s) write(s); } }
      if (t0 === Infinity && fs.existsSync(o.go!)) { t0 = Date.now(); for (const b of bots) if (b.state === 'waiting') b.state = 'driving'; write({ event: 'go', t: t0 }); }
      const elapsed = t0 === Infinity ? 0 : (Date.now() - t0) / 1000;
      if (!left && elapsed > o.seconds * 0.25 && bots.length > 1) {
        left = true; const b = bots[bots.length - 1]; b.leave(); write({ event: 'leave', t: Date.now(), client: 100 + b.index, nick: b.nick, bot: true });
      }
      if (!joined && elapsed > o.seconds * 0.3) { joined = true; void add(o.bots).then(b => { b.state = 'driving'; }).catch(e => write({ event: 'error', t: Date.now(), error: String(e) })); }
      if (elapsed > o.seconds) { clearInterval(timer); resolve(); }
    }, 8);
  });
  for (const b of bots) if (b.alive) b.leave();
  await new Promise(r => out.end(r));
}
