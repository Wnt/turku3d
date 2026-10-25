// Main-thread side of the physics worker.
import { CharacterInterpolation, VehicleInterpolation } from '../actors/interpolation';
import type { V3 } from '../core/origin';
import type { CharacterInput, FromWorker, RayFilter, RayHit, TileStats, ToWorker, VehicleInput } from './protocol';

type StateMsg = Extract<FromWorker, { type: 'state' }>;

export class PhysicsClient {
  private worker: Worker;
  private nextId = 1;
  private pending = new Map<number, (m: FromWorker) => void>();
  private tileWaiters = new Map<string, { resolve: (s: TileStats) => void; reject: (e: Error) => void }>();
  private inFlight = false;
  private seq = 0;
  private pendingDt = 0;
  private jumpPending = false;
  readonly characterRender = new CharacterInterpolation();
  readonly vehicleRender = new VehicleInterpolation();
  latest: StateMsg | null = null;
  readonly loaded = new Map<string, TileStats>();
  onError: (e: string) => void = (e) => console.error('[physics]', e);
  ready: Promise<string>;
  colReader = '';
  private isReady = false;
  private queue: { m: ToWorker; transfer: Transferable[] }[] = [];

  constructor() {
    this.worker = new Worker(new URL('./worker.ts', import.meta.url), { type: 'module', name: 'physics' });
    let resolveReady!: (v: string) => void;
    this.ready = new Promise((r) => (resolveReady = r));
    this.worker.onmessage = (ev: MessageEvent<FromWorker>) => {
      const m = ev.data;
      switch (m.type) {
        case 'ready': this.colReader = m.colReader; this.isReady = true; for (const q of this.queue.splice(0)) this.worker.postMessage(q.m, q.transfer); resolveReady(m.rapier); break;
        case 'state':
          this.latest = m; this.inFlight = false;
          this.characterRender.push(m.character, m.previousCharacter, m.tick, m.remainder, performance.now() / 1000);
          this.vehicleRender.push(m.vehicle, m.previousVehicle, m.tick, m.remainder, performance.now() / 1000);
          break;
        case 'tileLoaded': this.loaded.set(m.stats.id, m.stats); this.tileWaiters.get(m.stats.id)?.resolve(m.stats); this.tileWaiters.delete(m.stats.id); break;
        case 'tileError': this.tileWaiters.get(m.id)?.reject(Object.assign(new Error(m.error), { permanent: m.permanent })); this.tileWaiters.delete(m.id); break;
        case 'tileUnloaded': this.loaded.delete(m.id); break;
        case 'cameraCastResult': case 'raycastResult': case 'statsResult': { const cb = this.pending.get(m.id); this.pending.delete(m.id); cb?.(m); break; }
        case 'stepError':
          if (m.seq === this.seq) { this.inFlight = false; this.pendingDt = 0; }
          this.onError(`step ${m.seq}: ${m.error}`);
          break;
        case 'error': this.onError(m.error); break;
      }
    };
    this.worker.onerror = (e) => {
      this.inFlight = false;
      const error = new Error(e.message || 'worker failed to load');
      for (const waiter of this.tileWaiters.values()) waiter.reject(error);
      this.tileWaiters.clear();
      this.onError(error.message);
    };
  }

  private send(m: ToWorker, transfer: Transferable[] = []) {
    if (this.isReady) this.worker.postMessage(m, transfer);
    else this.queue.push({ m, transfer });
  }

  /** Transfers the buffer (it is detached afterwards). */
  loadTile(id: string, buffer: ArrayBuffer): Promise<TileStats> {
    const p = new Promise<TileStats>((resolve, reject) => this.tileWaiters.set(id, { resolve, reject }));
    this.send({ type: 'loadTile', id, buffer }, [buffer]);
    return p;
  }
  unloadTile(id: string) { this.send({ type: 'unloadTile', id }); this.loaded.delete(id); }
  spawnCharacter(pos: V3) { this.send({ type: 'spawnCharacter', pos }); }
  setCharacterEnabled(enabled: boolean, pos?: V3) { this.send({ type: 'setCharacterEnabled', enabled, pos }); }
  spawnVehicle(pos: V3, yaw: number) { this.send({ type: 'spawnVehicle', pos, yaw }); }
  resetVehicle(pos: V3, yaw: number) { this.send({ type: 'resetVehicle', pos, yaw }); }

  /** Queues dt; sends one step at a time (single in flight) so the worker never falls behind. */
  step(dt: number, character: CharacterInput, vehicle: VehicleInput | null) {
    this.pendingDt += dt;
    this.jumpPending ||= character.jump;
    if (this.inFlight) return;
    this.inFlight = true;
    this.send({ type: 'step', seq: ++this.seq, dt: this.pendingDt, character: { ...character, jump: this.jumpPending }, vehicle });
    this.pendingDt = 0;
    this.jumpPending = false;
  }

  cameraCast(origin: V3, dir: V3, distance: number): Promise<number> {
    const id = this.nextId++;
    return new Promise(resolve => {
      this.pending.set(id, m => resolve(m.type === 'cameraCastResult' ? m.distance : 0));
      this.send({ type: 'cameraCast', id, origin, dir, distance });
    });
  }

  raycast(origin: V3, dir: V3, maxToi: number, filter: RayFilter = 'solid'): Promise<RayHit | null> {
    const id = this.nextId++;
    return new Promise((resolve) => {
      this.pending.set(id, (m) => resolve(m.type === 'raycastResult' ? m.hit : null));
      this.send({ type: 'raycast', id, origin, dir, maxToi, filter });
    });
  }
  stats(): Promise<Extract<FromWorker, { type: 'statsResult' }>> {
    const id = this.nextId++;
    return new Promise((resolve) => {
      this.pending.set(id, (m) => resolve(m as Extract<FromWorker, { type: 'statsResult' }>));
      this.send({ type: 'stats', id });
    });
  }
  totals() {
    let tris = 0, bytes = 0, curbTris = 0;
    for (const s of this.loaded.values()) { tris += s.tris; bytes += s.bytes; curbTris += s.curbTris ?? 0; }
    return { tiles: this.loaded.size, tris, colBytes: bytes, estWasmBytes: (tris + curbTris) * 135 };
  }
}
