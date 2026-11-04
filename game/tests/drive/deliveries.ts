// Delivery bots for multiplayer play-testing (tools/drivetest/bots.sh): N protocol v2 clients (mpbot.ts Bot) on a
// live server that each drive pizzeria → a delivery address from the address pool → back to the pizzeria, forever,
// with the autopilot's lane router and follower on the real vehicle physics. Every fourth trip goes to the black
// market or the dealer instead. A stuck bot re-plans from where it is; after repeated failures it respawns at the
// pizzeria. Scores are never sent: bots carry pizzas on the roof (`carrying`) but report no deliveries.
// A status JSON (bots connected, deliveries, stuck count) is rewritten every 5 s.
import fs from 'node:fs';
import path from 'node:path';
import * as RAPIER from '@dimforge/rapier3d';
import { PhysicsWorld } from '../../src/physics/world';
import { COLLISION_TILE } from '../../src/core/origin';
import { DriveFollower } from '../../src/gameplay/autopilot/follow';
import { LaneRouter, tilesBetween } from '../../src/gameplay/autopilot/lanes';
import { makePath, sampleAt } from '../../src/gameplay/autopilot/path';
import { vehicleGeometry, vehiclePose } from '../../src/gameplay/autopilot/autopilot';
import { DEALER, MARKET, PIZZERIA, PIZZERIA_VEHICLE_SPOT } from '../../src/gameplay/content/locations';
import { Bot } from './mpbot';
// @ts-expect-error plain ESM tool
import { TileCache, farm } from '../../../tools/drivetest/fetch.mjs';

export interface DeliveryOptions { url: string; origin: string; dir: string; status: string; bots: number; stop: string }
interface Stop { id: string; label: string; x: number; z: number }

const KEEP_CELLS_M = 400;

class DeliveryBot extends Bot {
  deliveries = 0; stuck = 0; respawns = 0; trips = 0;
  leg: Stop | null = null;
  private loaded = new Set<string>();
  private planning = false;
  private failures = 0;
  started = Date.now();
  /** Last time the car moved (watchdog: a minute without moving respawns it at the pizzeria). */
  movedAt = Date.now();

  constructor(index: number, private readonly cache: InstanceType<typeof TileCache>, private readonly router: LaneRouter,
    private readonly stops: Stop[]) { super(index); }

  private async ensureCells(points: { x: number; z: number }[]) {
    const want = new Set<string>();
    for (const q of points) {
      const i = Math.floor(q.x / COLLISION_TILE), j = Math.floor(q.z / COLLISION_TILE);
      for (let a = -1; a <= 1; a++) for (let b = -1; b <= 1; b++) want.add(`c_${i + a}_${j + b}`);
    }
    // Queued, not built in place: the worker's budgeted install queue (physics/tileInstall.ts) keeps every bot's
    // 60 Hz loop and pose stream running while cells build (tick pumps 2 ms per bot).
    for (const id of want) if (!this.loaded.has(id)) {
      this.loaded.add(id);
      const b = await this.cache.cell(id);
      if (b && this.pw) this.pw.requestTile(id, b, () => {});
    }
    // Drop cells far from the car (bounded memory on endless trips).
    const v = this.pw!.vehicleState();
    if (v) for (const id of [...this.loaded]) {
      const [i, j] = id.slice(2).split('_').map(Number);
      if (!want.has(id) && Math.hypot((i + 0.5) * COLLISION_TILE - v.pos.x, (j + 0.5) * COLLISION_TILE - v.pos.z) > KEEP_CELLS_M) { this.pw!.unloadTile(id); this.loaded.delete(id); }
    }
  }

  /** At the pizzeria kerb, facing along its street. */
  async spawnAtPizzeria() {
    if (!this.pw) this.pw = new PhysicsWorld(RAPIER);
    // Kerb slots 6 m apart along the street (a respawn takes the next one), a second row 3 m out past five.
    const slot = (this.index + this.respawns) % 10, along = 6 * (slot % 5), out = slot >= 5 ? 3 : 0;
    const cy = Math.cos(PIZZERIA_VEHICLE_SPOT.yaw), sy = Math.sin(PIZZERIA_VEHICLE_SPOT.yaw);
    const at = { x: PIZZERIA_VEHICLE_SPOT.x + along * cy + out * sy, z: PIZZERIA_VEHICLE_SPOT.z - along * sy + out * cy };
    await this.ensureCells([at]);
    for (let k = 0; k < 400 && !this.ready(at.x, at.z, 0, 0); k++) { this.pw.pumpInstalls(20); await new Promise(r => setTimeout(r, 5)); }
    this.pw.world.step();
    const hit = this.pw.raycast({ x: at.x, y: 40, z: at.z }, { x: 0, y: -1, z: 0 }, 80, 'solid');
    this.pw.spawnVehicle({ x: at.x, y: (hit?.point.y ?? 5) + 0.9, z: at.z }, PIZZERIA_VEHICLE_SPOT.yaw);
    for (let k = 0; k < 30; k++) this.pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
    this.follower = null; this.leg = null; this.state = 'parked'; this.movedAt = Date.now();
  }

  /** The next stop: home when away, else an address (every fourth trip the market or the dealer). */
  private next(): Stop {
    const home = { id: 'pizzeria', label: PIZZERIA.label, x: PIZZERIA_VEHICLE_SPOT.x, z: PIZZERIA_VEHICLE_SPOT.z };
    if (this.leg && this.leg.id !== 'pizzeria') return home;
    this.trips++;
    if (this.trips % 4 === 0) return this.trips % 8 === 0 ? { id: 'dealer', label: DEALER.label, x: DEALER.point.x, z: DEALER.point.z } : { id: 'market', label: MARKET.label, x: MARKET.point.x, z: MARKET.point.z };
    return this.stops[(this.index * 7 + this.trips * 13) % this.stops.length];
  }

  async plan(target?: Stop) {
    if (this.planning || !this.pw) return;
    this.planning = true;
    try {
      const v = this.pw.vehicleState()!, pose = vehiclePose(v);
      const stop = target ?? this.next();
      for (const t of tilesBetween(pose, stop, 1)) if (!this.router.has(t)) { const r = await this.cache.roads(t); if (r) this.router.setTile(r); }
      const route = this.router.route({ x: pose.x, z: pose.z, y: pose.y, hx: pose.hx, hz: pose.hz }, stop);
      if (!route) throw new Error(`no route to ${stop.label}`);
      const points = [{ x: pose.x, z: pose.z, y: pose.y }];
      for (let i = 0; i < route.path.cum.length; i++) {
        const x = route.path.points[2 * i], z = route.path.points[2 * i + 1];
        if (points.length === 1 && (x - pose.x) * pose.hx + (z - pose.z) * pose.hz < 4 && i < route.path.cum.length - 1) continue;
        points.push({ x, z, ...(route.path.ys ? { y: route.path.ys[i] } : {}) });
      }
      this.path = makePath(points);
      const samples: { x: number; z: number }[] = [];
      for (let s = 0; s <= this.path.length; s += 20) samples.push(sampleAt(this.path, s));
      await this.ensureCells(samples);
      this.follower = new DriveFollower(this.path, vehicleGeometry('car'), { speed: 12, limit: (s: number) => route.limit(s), style: 'normal' });
      this.leg = stop; this.carrying = stop.id !== 'pizzeria' && stop.id !== 'market' && stop.id !== 'dealer';
      this.state = 'driving'; this.failures = 0;
    } catch {
      this.failures++;
      if (this.failures >= 3) { this.respawns++; await this.spawnAtPizzeria(); this.failures = 0; }
      else this.state = 'replan';
    } finally { this.planning = false; }
  }

  /** Collision resident under (x, z) and `ahead` metres along (hx, hz). */
  ready(x: number, z: number, hx: number, hz: number, ahead = 0) {
    for (const d of ahead ? [0, ahead / 2, ahead] : [0]) {
      const id = `c_${Math.floor((x + hx * d) / COLLISION_TILE)}_${Math.floor((z + hz * d) / COLLISION_TILE)}`;
      if (!this.pw!.hasTile(id)) return false;
    }
    return true;
  }

  tick(dt: number) {
    if (!this.pw || !this.alive) return;
    this.pw.pumpInstalls(2);
    // Parked until the server seats us in our own car: acquiring needs the car within 7 m of its record.
    if (this.seat !== 'seated') {
      // Refused twice: the record stayed where it was registered; put the car back there to take the seat.
      if (this.seatRefusals >= 2 && this.registeredAt) {
        const r = this.registeredAt; this.pw.resetVehicle({ x: r.x, y: r.y + 0.3, z: r.z }, r.yaw); this.seatRefusals = 0;
        this.follower = null; this.state = 'parked'; this.leg = null;
      }
      this.pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true }); this.movedAt = Date.now(); return;
    }
    // Hold (like the worker's coverage block) until the ground under and ahead of the car is resident.
    const v0 = this.pw.vehicleState();
    if (v0 && this.state === 'driving') {
      const p = vehiclePose(v0);
      if (!this.ready(p.x, p.z, p.hx, p.hz, 30)) { this.pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true }); return; }
    }
    if (this.state !== 'driving' || !this.follower) {
      this.pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
      // A re-plan keeps the current stop; arrival or a fresh spawn picks the next one.
      if (!this.planning && (this.state === 'parked' || this.state === 'arrived')) void this.plan();
      else if (!this.planning && this.state === 'replan') void this.plan(this.leg ?? undefined);
      return;
    }
    this.step(dt);
    if (this.state === 'arrived') {
      if (this.leg && this.leg.id !== 'pizzeria' && this.carrying) this.deliveries++;
      this.carrying = false;
    } else if (this.state === 'failed') {
      // Stuck: re-plan from where it is; the third failure in a row respawns at the pizzeria.
      this.stuck++; this.state = 'replan'; this.failures++;
      if (this.failures >= 3) { this.respawns++; this.failures = 0; void this.spawnAtPizzeria(); }
    }
    // Driven off the world (fell through), or not moved for a minute: back to the pizzeria.
    const v = this.pw.vehicleState();
    if (v && v.speed > 1) this.movedAt = Date.now();
    if (Date.now() - this.movedAt > 60_000 && !this.planning) { this.stuck++; this.respawns++; void this.spawnAtPizzeria(); return; }
    if (v && v.pos.y < -30) { this.respawns++; void this.spawnAtPizzeria(); }
  }
}

export async function runDeliveries(o: DeliveryOptions) {
  const cache = new TileCache(o.dir, farm(path.join(o.dir, 'tile-service.json')));
  const nav = JSON.parse(fs.readFileSync(path.join(o.dir, 'navigation.json'), 'utf8'));
  // Addresses within 2.5 km by car, at their street point (where a driver stops).
  const stops: Stop[] = nav.addresses.addresses.filter((a: { car: number }) => a.car > 150 && a.car < 2500)
    .map((a: { id: string; address: string; street: [number, number] }) => ({ id: a.id, label: a.address, x: a.street[0], z: a.street[1] }));
  const router = new LaneRouter();
  const bots: DeliveryBot[] = [];
  const add = async (i: number) => {
    const bot = new DeliveryBot(i, cache, router, stops);
    await bot.connect(o.url, o.origin);
    bot.ws.on('close', () => { if (bot.alive) setTimeout(() => void bot.connect(o.url, o.origin).catch(() => {}), 3000); });
    await bot.spawnAtPizzeria();
    bots.push(bot);
  };
  for (let i = 0; i < o.bots; i++) { await add(i).catch(e => console.error(`bot ${i}: ${e}`)); await new Promise(r => setTimeout(r, 1500)); }
  const t0 = Date.now();
  let last = performance.now(), acc = 0, lastStatus = 0;
  await new Promise<void>(resolve => {
    const timer = setInterval(() => {
      const now = performance.now();
      acc += Math.min(0.25, (now - last) / 1000); last = now;
      while (acc >= 1 / 60) { for (const b of bots) b.tick(1 / 60); acc -= 1 / 60; }
      for (const b of bots) b.sendPose(now);
      if (Date.now() - lastStatus > 5000) {
        lastStatus = Date.now();
        const status = { t: new Date().toISOString(), uptimeS: Math.round((Date.now() - t0) / 1000), server: o.url,
          connected: bots.filter(b => b.ws.readyState === 1).length, bots: bots.length,
          deliveries: bots.reduce((a, b) => a + b.deliveries, 0), stuck: bots.reduce((a, b) => a + b.stuck, 0),
          respawns: bots.reduce((a, b) => a + b.respawns, 0),
          list: bots.map(b => { const v = b.pw?.vehicleState(); return { nick: b.nick, state: b.state, seat: b.seat, to: b.leg?.label ?? null, carrying: b.carrying,
            deliveries: b.deliveries, stuck: b.stuck, respawns: b.respawns, speedKmh: v ? Math.round(v.speed * 3.6) : null,
            at: v ? [Math.round(v.pos.x), Math.round(v.pos.z)] : null }; }) };
        fs.writeFileSync(o.status + '.tmp', JSON.stringify(status, null, 1)); fs.renameSync(o.status + '.tmp', o.status);
      }
      if (fs.existsSync(o.stop)) { clearInterval(timer); resolve(); }
    }, 8);
  });
  for (const b of bots) b.leave();
  await new Promise(r => setTimeout(r, 1000)); // releases and closes go out
}
