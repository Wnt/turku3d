import * as THREE from 'three/webgpu';
import { E0, N0 } from '../core/origin';
import { localFeature } from '../core/localOnly';
import { DriveCapture } from '../telemetry/capture';
import { CarVisual } from '../actors/car';
import type { LightState } from '../vehicle/signals';
import { CAR } from '../physics/constants';
import { BENCH_SETTLE_MS } from './bench';
import type { ClockMode } from '../render/dayClock';

export interface DrivePose {
  t: number; paused: boolean; mode: string; id: string | null; E: number; N: number; H: number;
  feet: number[]; vel: number[]; cam: number[]; car: number[] | null; speed: number | null; lights: number | null;
  input?: Record<string, unknown>;
}
export interface DriveSegment { start: number; end: number; poses: DrivePose[] }
/** Marks delimit precise new captures; pose transitions cover the coordinator's original drive format. */
export function driveSegments(poses: DrivePose[], marks: { k: string; t: number }[] = []): DriveSegment[] {
  if (!poses.length) return [];
  const edges = marks.filter(m => m.k === 'pause' || m.k === 'resume').sort((a, b) => a.t - b.t);
  const segments: DriveSegment[] = [];
  if (edges.length) {
    let start: number | null = !poses[0].paused ? poses[0].t : null;
    for (const edge of edges) {
      if (edge.k === 'resume' && start === null) start = edge.t;
      if (edge.k === 'pause' && start !== null) {
        const selected = poses.filter(p => p.t >= start! && p.t <= edge.t && !p.paused);
        if (selected.length > 1) segments.push({ start: selected[0].t, end: selected.at(-1)!.t, poses: selected });
        start = null;
      }
    }
    if (start !== null) {
      const selected = poses.filter(p => p.t >= start! && !p.paused);
      if (selected.length > 1) segments.push({ start: selected[0].t, end: selected.at(-1)!.t, poses: selected });
    }
  } else {
    let selected: DrivePose[] = [];
    const finish = () => { if (selected.length > 1) segments.push({ start: selected[0].t, end: selected.at(-1)!.t, poses: selected }); selected = []; };
    for (const pose of poses) { if (pose.paused) finish(); else selected.push(pose); }
    finish();
  }
  return segments;
}
const jsonl = (text: string) => text.split('\n').filter(Boolean).map(line => JSON.parse(line));
export async function loadDrive(id: string, segment = 1) {
  if (!localFeature(location, 'capture') || import.meta.env.VITE_PUBLIC_RELEASE) throw Error('Drive bench is local only');
  if (!/^[\w-]{1,80}$/.test(id)) throw Error('Invalid capture id');
  const get = async (name: string) => {
    const response = await fetch(`/__capture/${id}/${name}`);
    if (!response.ok) throw Error(`Capture ${id}/${name}: HTTP ${response.status}`);
    return response.text();
  };
  const [metaText, poseText, markText, statText] = await Promise.all([get('meta.json'), get('poses.jsonl'), get('marks.jsonl'), get('stats.jsonl')]);
  const meta = JSON.parse(metaText), segments = driveSegments(jsonl(poseText), jsonl(markText));
  const selected = segments[segment - 1];
  if (!selected) throw Error(`No segment ${segment}; ${id} has ${segments.length} unpaused segments (numbered from 1)`);
  const stats = jsonl(statText), stat = stats.find((s: any) => s.t >= selected.start) ?? stats[0];
  const settings = { pixelRatio: stat?.pixelRatio ?? meta.pixelRatio ?? null, viewport: stat?.viewport ?? meta.viewport ?? null };
  return { id, segment, meta, settings, ...selected };
}
export type LoadedDrive = Awaited<ReturnType<typeof loadDrive>>;
/** Start the scene at the captured timeline, including when the source was itself a replay. */
export function driveEpoch(drive: Pick<LoadedDrive, 'meta' | 'start'>): number {
  const elapsedMs = drive.start - drive.meta.start.performanceNow;
  return drive.meta.replay?.epochMs != null
    ? drive.meta.replay.epochMs + (drive.meta.replay.seconds ?? 0) * 1000 + elapsedMs
    : drive.meta.start.wall + elapsedMs;
}

export function driveClockMode(meta: Record<string, any>): ClockMode {
  const mode = meta.clockMode;
  if (mode) return mode.kind === 'date' ? { ...mode, date: new Date(mode.date) } : { ...mode };
  if (meta.params?.timeGiven) {
    return meta.params.hour != null ? { kind: 'hour', hour: meta.hour }
      : { kind: 'date', date: new Date(meta.params.time) };
  }
  return { kind: 'cycle', periodMs: (meta.params?.cycleMinutes ?? 30) * 60_000 };
}

export function driveQaPose(pose: DrivePose) {
  const e = new THREE.Euler().setFromQuaternion(new THREE.Quaternion().fromArray(pose.cam, 3), 'YXZ');
  return { E: E0 + pose.cam[0], N: N0 - pose.cam[2], H: pose.cam[1], yawDeg: -e.y * 180 / Math.PI,
    pitchDeg: e.x * 180 / Math.PI, rollDeg: -e.z * 180 / Math.PI, fovDeg: pose.cam[7] };
}
const mix = (a: number[], b: number[], t: number) => a.map((x, i) => x + (b[i] - x) * t);
/** Timestamp interpolation, with shortest-arc rotations for both camera and vehicle. */
export function interpolateDrive(a: DrivePose, b: DrivePose, t: number): DrivePose {
  const f = b.t > a.t ? Math.max(0, Math.min(1, (t - a.t) / (b.t - a.t))) : 0;
  const rotation = (x: number[], y: number[], offset: number) => new THREE.Quaternion().fromArray(x, offset)
    .slerp(new THREE.Quaternion().fromArray(y, offset), f).toArray();
  const cam = [...mix(a.cam.slice(0, 3), b.cam.slice(0, 3), f), ...rotation(a.cam, b.cam, 3), a.cam[7] + (b.cam[7] - a.cam[7]) * f];
  const car = a.car && b.car ? [...mix(a.car.slice(0, 3), b.car.slice(0, 3), f), ...rotation(a.car, b.car, 3), ...mix(a.car.slice(7), b.car.slice(7), f)] : a.car;
  const feet = mix(a.feet, b.feet, f), vel = mix(a.vel, b.vel, f);
  return { ...a, t, cam, car, feet, vel, E: feet[0] + E0, N: N0 - feet[2], H: feet[1] };
}
interface DriveHost {
  scene: THREE.Scene; camera: THREE.PerspectiveCamera;
  signature(): string; busy(): boolean; configure(drive: LoadedDrive): void;
  reset(drive: LoadedDrive): Promise<void>; seek(seconds: number): void; begin(): void;
  game: Record<string, any>;
}
export class DriveBench {
  private car: CarVisual;
  private recorder: DriveCapture | null = null;
  private drive!: LoadedDrive;
  private pose!: DrivePose;
  private phase = 'loading';
  private stableSince = 0;
  private settleStart = 0;
  private signature = '';
  private startWall = 0;
  private index = 0;
  private runToken = 0;
  private result: Record<string, any> = {};
  constructor(private host: DriveHost) { this.car = new CarVisual(host.scene); }
  async load(drive: LoadedDrive) {
    const token = ++this.runToken;
    this.phase = 'loading';
    this.result = { phase: this.phase, done: false, replayOf: drive.id, segment: drive.segment, durationMs: drive.end - drive.start,
      run: async (id: string, segment = 1) => {
        if (this.phase !== 'done' && this.phase !== 'error') throw Error('Wait for the current drive bench to finish');
        try { await this.load(await loadDrive(id, segment)); }
        catch (error) { if (this.phase !== 'error') this.fail(error); throw error; }
      } };
    window.__bench = this.result;
    window.__qaError = undefined; window.__qaReady = false;
    try {
      if (this.recorder) { await this.recorder.stop(); this.recorder = null; }
      this.host.configure(drive);
      this.drive = drive; this.index = 0; this.pose = drive.poses[0];
      this.apply(this.pose);
      if (this.pose.car) await this.car.load(`${import.meta.env.BASE_URL}assets/car/sedan.glb`);
      this.apply(this.pose);
      await this.host.reset(drive);
      if (token !== this.runToken) return;
      Object.assign(this.host.game, { state: () => this.state(), orbit: { yaw: 0, pitch: 0 }, input: { actions: {}, lookX: 0, lookY: 0 } });
      this.setPhase('settle'); this.stableSince = this.settleStart = performance.now(); this.signature = '';
      this.host.seek(0);
    } catch (error) { if (token === this.runToken) this.fail(error); throw error; }
  }
  get settled() { return this.phase === 'play' || this.phase === 'drain' || this.phase === 'done'; }
  before() {
    if (this.phase !== 'play') return;
    const elapsed = Math.min(performance.now() - this.startWall, this.drive.end - this.drive.start);
    const t = this.drive.start + elapsed, poses = this.drive.poses;
    while (this.index < poses.length - 2 && poses[this.index + 1].t <= t) this.index++;
    this.pose = interpolateDrive(poses[this.index], poses[this.index + 1], t);
    this.apply(this.pose); this.host.seek(elapsed / 1000);
  }
  after() {
    if (window.__qaError && this.phase !== 'error') { this.fail(Error(window.__qaError)); return; }
    if (this.phase === 'settle') {
      const now = performance.now(), signature = this.host.signature();
      if (signature !== this.signature || this.host.busy()) { this.signature = signature; this.stableSince = now; }
      if (now - this.settleStart > 180000) return this.fail(Error('Drive start tiles did not settle in 180 s'));
      if (now - this.stableSince < BENCH_SETTLE_MS) return;
      this.setPhase('prepare');
      const token = this.runToken;
      // Ground sampling resolves after all starting tiles and models have settled.
      void this.host.reset(this.drive).then(() => {
        if (token !== this.runToken || this.phase !== 'prepare') return;
        this.host.begin();
        this.setPhase('play'); this.startWall = performance.now();
        this.result.settleMs = this.startWall - this.settleStart;
        this.recorder = new DriveCapture(this.host.game, { replayOf: this.drive.id, segment: this.drive.segment,
          sourceStart: this.drive.start, sourceEnd: this.drive.end, viewport: this.drive.settings.viewport ?? [innerWidth, innerHeight],
          browserViewport: [innerWidth, innerHeight], populationFallback: !this.drive.meta.replay });
        this.result.capture = this.recorder.id;
      }).catch(e => { if (token === this.runToken) this.fail(e); });
    }
    if (this.phase === 'play' && performance.now() - this.startWall >= this.drive.end - this.drive.start) {
      this.setPhase('drain'); this.result.measureMs = performance.now() - this.startWall;
      const token = this.runToken;
      void this.recorder!.stop().then(() => {
        if (token !== this.runToken) return;
        this.recorder = null; this.setPhase('done'); this.result.done = true;
        console.info('DRIVE BENCH', JSON.stringify(this.result));
      }).catch(e => { if (token === this.runToken) this.fail(e); });
    }
  }
  private setPhase(phase: string) { this.phase = phase; this.result.phase = phase; }
  private fail(error: unknown) {
    ++this.runToken; this.setPhase('error');
    Object.assign(this.result, { error: String(error), done: false });
    const recorder = this.recorder, result = this.result;
    if (recorder) void recorder.stop().then(() => { if (this.recorder === recorder) this.recorder = null; })
      .catch(saveError => { result.captureError = String(saveError); });
  }
  private apply(pose: DrivePose) {
    const camera = this.host.camera;
    camera.position.fromArray(pose.cam); camera.quaternion.fromArray(pose.cam, 3); camera.fov = pose.cam[7];
    camera.updateProjectionMatrix(); camera.updateMatrixWorld();
    this.car.root.visible = !!pose.car;
    if (pose.car) {
      const c = pose.car;
      this.car.update({ pos: { x: c[0], y: c[1], z: c[2] }, rot: { x: c[3], y: c[4], z: c[5], w: c[6] },
        linvel: { x: c[7], y: c[8], z: c[9] }, speed: pose.speed ?? 0, forwardSpeed: pose.speed ?? 0, curbStrike: null,
        lights: (pose.lights ?? 0) as LightState,
        wheels: CAR.wheels.map((p, i) => ({ pos: { x: p.x, y: p.y - CAR.rest, z: p.z },
          steer: CAR.steered.some(index => index === i) ? Number(pose.input?.steer ?? 0) * CAR.maxSteer : 0,
          rotation: (pose.t - this.drive.start) / 1000 * (pose.speed ?? 0) / CAR.wheelRadius, contact: true, suspension: CAR.rest })) });
    }
  }
  private state() {
    const p = this.pose, c = p.car;
    return { ...p, paused: false, drivenId: p.id, inputSource: 'replay',
      camera: { yaw: 0 }, vehicle: c ? { pos: c.slice(0, 3), rot: { x: c[3], y: c[4], z: c[5], w: c[6] }, linvel: { x: c[7], y: c[8], z: c[9] }, speed: p.speed, lights: p.lights } : null,
      visModels: this.host.game.tiles.residentModels, visTris: this.host.game.tiles.residentTris, tilesSettled: !this.host.busy(), frameMs: null };
  }
}
