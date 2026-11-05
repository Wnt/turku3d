// Ambient actors for the game's camera/replay mode. Uses the gameplay traffic solver, road heights,
// pedestrian population and fleet renderer; the driver only supplies scene time, never actor poses.
import * as THREE from 'three/webgpu';
import { ParkedCarVisuals, type TrafficInstance } from '../actors/car';
import { CHASSIS_LIFT, PAINT_WEIGHTS, TRAFFIC_CAPS, TRAFFIC_MODELS, TRAFFIC_PAINTS } from '../traffic/constants';
import { FLOW_STEP, TrafficFlow, interpolateFlowPose } from '../traffic/flow';
import { TrafficNetwork, type LaneHeight } from '../traffic/network';
import { hash01, hash32, pick } from '../traffic/hash';
import { roadHash } from '../traffic/gate';
import { trafficRoads } from '../traffic/roads';
import { buildTileTraffic } from '../traffic/tileNetwork';
import { vehicleLights } from '../traffic/lights';
import { inView, mayCreate, mustRemove } from '../traffic/ring';
import { TrafficSignals } from '../traffic/signals';
import { tileBounds } from '../render/lights/tileLights';
import type { QualityTier } from '../render/quality';
import type { CoarseTiles } from './coarseTiles';
import type { Manifest } from './manifest';
import { Walkers, walkerCap } from './walkers';
import { populationFocus } from './populationFocus';
import { FLEET_BY_ID, fleetModel } from '../actors/fleet/catalog';
import { sampleWheelGround } from '../vehicle/wheelGround';
import type { GroundIndex } from './groundIndex';

export class ScenePopulation {
  readonly root = new THREE.Group();
  readonly walkers: Walkers;
  readonly visuals: ParkedCarVisuals;
  private network = new TrafficNetwork();
  private flow = new TrafficFlow(this.network);
  private signals: TrafficSignals;
  private graphKey = '';
  private graphs: ReturnType<CoarseTiles['roadGraphs']> = [];
  private seconds = 0;
  private pedestriansAt = -Infinity;
  private origin = 120;
  private utc = 0;
  private epoch = 0;
  private selected = new Set<string>();
  private first = true;
  private cars: (TrafficInstance & { id: string })[] = [];
  private cap: number;
  /** Lane fallback heights by half-metre point, with the version of the collision cell they came from. */
  private heightCache = new Map<string, { version: number; y: number | null }>();
  private forward = new THREE.Vector3();

  /** `ground` indexes the collision cells this mode decodes (there is no physics worker): wheels, lane fallbacks and
   *  walkers sample it with a grid lookup, never the visual meshes. */
  constructor(scene: THREE.Scene, private source: CoarseTiles, private camera: THREE.PerspectiveCamera,
    private manifest: Manifest, private ground: GroundIndex, private options: { quality: QualityTier; traffic: number | null; walkers: number | null;
      density: number; hour: () => number; night: () => number; prepare?: (model: THREE.Object3D) => Promise<void> }) {
    this.root.name = 'scene-population'; scene.add(this.root);
    this.cap = options.traffic ?? TRAFFIC_CAPS[options.quality];
    // Fleet designs compile before they draw (as play.ts's parked cars): unprepared, every design built its programs
    // inside a frame (replay qj4rp2: 'Finnish fleet' 112 + glass 7 in-frame builds).
    this.visuals = new ParkedCarVisuals(this.root, options.prepare); this.visuals.reserveTraffic(this.cap);
    this.signals = new TrafficSignals(this.root);
    this.network.density = { hourAt: () => options.hour(), multiplier: options.density };
    this.walkers = new Walkers(this.root, { cap: walkerCap(options.quality, options.walkers),
      now: () => this.utc, birthHour: () => options.hour(), graphs: () => this.graphs,
      raycast: async (origin, direction, distance) => {
        if (direction.y > -0.999) return null;
        const y = this.ground.ground(origin.x, origin.y, origin.z, distance);
        return y === null ? null : { toi: origin.y - y, point: { x: origin.x, y, z: origin.z }, normal: { x: 0, y: 1, z: 0 }, material: 0, tile: null };
      } });
  }
  async load() {
    await Promise.all([this.visuals.load({ ...this.manifest, vehicles: [] }),
      this.walkers.load(`${import.meta.env.BASE_URL}assets/walkers/walkers.bin`)]);
    // Walker variants are hidden until someone walks; their VAT programs compile now, not in their first frame.
    await this.options.prepare?.(this.root);
  }
  private height: LaneHeight = (lane, s) => {
    let i = 1; while (i < lane.cum.length - 1 && lane.cum[i] < s) i++;
    const f = Math.max(0, Math.min(1, (s - lane.cum[i - 1]) / Math.max(.01, lane.cum[i] - lane.cum[i - 1])));
    if (lane.ys && Number.isFinite(lane.ys[i - 1]) && Number.isFinite(lane.ys[i])) return lane.ys[i - 1] + (lane.ys[i] - lane.ys[i - 1]) * f;
    const x = lane.points[(i - 1) * 2] + (lane.points[i * 2] - lane.points[(i - 1) * 2]) * f;
    const z = lane.points[(i - 1) * 2 + 1] + (lane.points[i * 2 + 1] - lane.points[(i - 1) * 2 + 1]) * f;
    const key = `${Math.round(x * 2)},${Math.round(z * 2)}`, version = this.ground.version(x, z);
    let cached = this.heightCache.get(key);
    if (!cached || cached.version !== version) {
      cached = { version, y: version < 0 ? null : this.ground.ground(x, 400, z, 800) };
      this.heightCache.set(key, cached);
    }
    return cached.y;
  };
  private sync() {
    // Only fully resident road generations participate. Canonical order removes fetch-order randomness.
    const wanted = new Set(this.source.viewStatus(this.camera.position.x, this.camera.position.z).near.map(e => e.id));
    const entries = [...this.source.entries].filter(([id, e]) => wanted.has(id) && e.model && !e.preview && e.pass === 'full' && e.roads)
      .sort(([a], [b]) => a.localeCompare(b));
    const key = entries.map(([id, e]) => `${id}:${roadHash(e.roads!)}`).join('|');
    if (key === this.graphKey) return;
    const live = new Set(entries.map(([id]) => id));
    for (const id of this.network.tiles.keys()) if (!live.has(id)) { this.network.removeTile(id); this.signals.removeTile(id); }
    for (const [id, e] of entries) {
      const hash = roadHash(e.roads!);
      if (this.network.tiles.get(id)?.hash === hash) continue;
      this.network.setTile(buildTileTraffic(id, trafficRoads(e.roads!), tileBounds(id)!, hash)); this.signals.setTile(id, e.roads!);
    }
    this.graphs = entries.map(([, e]) => e.roads!); this.graphKey = key;
    if (this.heightCache.size > 200_000) this.heightCache.clear();
  }
  reset(epochMs: number, seed: number) {
    this.sync(); this.seconds = 0; this.pedestriansAt = -Infinity; this.epoch = this.utc = epochMs;
    this.origin = 120 + (seed >>> 0) % 3600;
    this.flow = new TrafficFlow(this.network, this.origin - 90);
    this.flow.update(this.origin);
    this.selected.clear(); this.first = true; this.cars = [];
    this.visuals.setTraffic([]); this.visuals.update([]);
    this.walkers.reset();
  }
  async prepare(epochMs: number, seed: number) {
    this.reset(epochMs, seed);
    // Resolve ground sampling before starting the replay, then reset only cosmetic history. Warmup speed
    // and promise completion order must not determine which pedestrians have faded into frame zero.
    for (let i = 0; i < 2000; i++) {
      this.walkers.update(0, this.camera, 0);
      if (!this.walkers.snapshot().heightsPending) { this.walkers.reset(); return; }
      await new Promise<void>(resolve => setTimeout(resolve, 0));
    }
    throw Error('Scene population ground sampling did not settle');
  }
  update(seconds: number) {
    this.sync();
    // Cosmetic fades/turns and crossing users follow fixed steps too, independent of screenshot latency.
    while (this.seconds < seconds - 1e-9) {
      const dt = Math.min(1 / 60, seconds - this.seconds); this.seconds += dt;
      this.utc = this.epoch + this.seconds * 1000;
      this.walkers.update(dt, this.camera, this.seconds * 1000);
      // Crossing users at 4 Hz of scene time, as in play (TrafficClient.updatePedestrians); drivers extrapolate.
      if (this.seconds - this.pedestriansAt >= 0.25 - 1e-9) {
        this.pedestriansAt = this.seconds;
        this.flow.setPedestrians(this.walkers.trafficObstacles(this.seconds * 1000), this.origin + this.seconds);
      }
      if (this.cap) this.flow.update(this.origin + this.seconds);
    }
    this.walkers.update(0, this.camera, this.seconds * 1000);
    this.draw();
  }
  private draw() {
    const time = this.origin + this.seconds;
    this.camera.getWorldDirection(this.forward);
    const flat = Math.hypot(this.forward.x, this.forward.z) || 1;
    const view = { ...populationFocus(this.camera.position, this.forward, this.graphs), fx: this.forward.x / flat, fz: this.forward.z / flat,
      halfFov: Math.atan(Math.tan(this.camera.fov * Math.PI / 360) * this.camera.aspect) };
    const candidates: { key: string; distance: number; instance: TrafficInstance & { id: string } }[] = [];
    for (const car of this.cap ? this.flow.cars.values() : []) {
      const p = interpolateFlowPose(car, (time - this.flow.tick * FLOW_STEP) / FLOW_STEP, this.height);
      if (!Number.isFinite(p.y)) continue;
      const distance = Math.hypot(p.x - view.x, p.z - view.z), visible = inView(view, p.x, p.z);
      if (this.selected.has(car.key) ? mustRemove(distance, visible) : !mayCreate(distance, visible, this.first)) continue;
      const yaw = Math.atan2(-p.hz, p.hx), sy = Math.sin(yaw / 2), cy = Math.cos(yaw / 2), sp = Math.sin(p.pitch / 2), cp = Math.cos(p.pitch / 2);
      const seed = String(hash32('appearance', car.key));
      candidates.push({ key: car.key, distance: distance * (visible ? 1 : 4), instance: { id: car.key,
        seed, model: fleetModel(TRAFFIC_MODELS[car.model], seed).id,
        color: TRAFFIC_PAINTS[pick(hash01('paint', car.key), PAINT_WEIGHTS)], x: p.x, y: p.y + CHASSIS_LIFT, z: p.z,
        q: { x: sy * sp, y: sy * cp, z: cy * sp, w: cy * cp }, ...vehicleLights(car, time) } });
    }
    candidates.sort((a, b) => Number(this.selected.has(b.key)) - Number(this.selected.has(a.key)) || a.distance - b.distance || a.key.localeCompare(b.key));
    const drawn = candidates.slice(0, this.cap); this.selected = new Set(drawn.map(c => c.key)); this.first = false;
    this.cars = drawn.map(c => c.instance);
    for (const car of this.cars) if (Math.hypot(car.x - this.camera.position.x, car.z - this.camera.position.z) < 100) {
      const model = FLEET_BY_ID.get(car.model)!;
      const yaw = 2 * Math.atan2(car.q.y, car.q.w);
      const ground = sampleWheelGround({ x: car.x, y: car.y - CHASSIS_LIFT, z: car.z }, yaw,
        { wheelbase: model.wheelbase, track: model.width - .15, radius: model.wheel }, (x, top, z, depth) => this.ground.ground(x, top, z, depth));
      if (ground) { car.y = ground.pos.y; car.q = ground.rot; car.wheelY = ground.wheelY; }
    }
    this.visuals.setView(this.camera, this.options.night()); this.visuals.setTraffic(this.cars); this.visuals.update([]);
    this.signals.update(time);
  }
  snapshot() {
    return { seconds: this.seconds, ready: !!this.graphKey && !this.walkers.snapshot().heightsPending,
      roads: this.graphKey, flowHash: hash32(JSON.stringify(this.flow.checkpoint())),
      traffic: this.cars, fleet: this.visuals.snapshot(), walkers: this.walkers.snapshot(), people: this.walkers.debugPoses() };
  }
}
