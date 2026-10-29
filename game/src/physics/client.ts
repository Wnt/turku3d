// Main-thread side of the physics worker.
import { ActorCache } from './actorExchange';
import type { PoleState, VehicleState } from './protocol';
import { SharedMailbox } from './exchange';
import type { LiveInput } from './protocol';
import { loading, loadBytes } from '../core/loading';
import { CharacterInterpolation, VehicleInterpolation } from '../actors/interpolation';
import type { V3 } from '../core/origin';
import type { CharacterInput, FromWorker, PoleSpec, RayFilter, RayHit, RemoteBody, TileStats, ToWorker, VehicleInput, VehicleSpec } from './protocol';

type StateMsg = Extract<FromWorker, { type: 'state' }>;

export class PhysicsClient {
  private worker: Worker;
  private nextId = 1;
  private pending = new Map<number, (m: FromWorker) => void>();
  private tileWaiters = new Map<string, { resolve: (s: TileStats) => void; reject: (e: Error) => void }>();

  private seq = 0;

  private jump = 0;
  private stateMailbox = globalThis.crossOriginIsolated ? SharedMailbox.create<StateMsg>(4 * 1024 * 1024) : null;
  private inputMailbox = this.stateMailbox ? SharedMailbox.create<LiveInput>(2048) : null;
  readonly transport = this.stateMailbox ? 'shared' : 'message';
  get stateBytes() { return this.stateMailbox?.publishedBytes ?? null; }
  readonly characterRender = new CharacterInterpolation();
  readonly vehicleRender = new VehicleInterpolation();
  private state: StateMsg | null = null;
  private vehicles = new ActorCache<VehicleState>();
  private poles = new ActorCache<PoleState>();
  get latest(): StateMsg | null {
    const m = this.stateMailbox?.read();
    if (m) this.acceptState(m);
    return this.state;
  }
  private acceptState(m: StateMsg) {
    this.state = { ...m, vehicles: this.vehicles.apply(m.vehicles, m.removedVehicles, m.actorsDelta ?? false),
      poles: this.poles.apply(m.poles, m.removedPoles, m.actorsDelta ?? false) };
    const now = performance.now() / 1000;
    const published = m.publishedAt === undefined ? now : (m.publishedAt - performance.timeOrigin) / 1000;
    this.characterRender.push(m.character, m.previousCharacter, m.tick, m.remainder, published);
    this.vehicleRender.push(m.vehicle, m.previousVehicle, m.tick, m.remainder, published);
  }
  readonly loaded = new Map<string, TileStats>();
  /** The newest explicit placement of the character (`actor` 'character') or a vehicle (its id): a teleport, a reset
   *  or a loaded game. `seq` counts them; safe reset (gameplay/safeReset.ts) restarts sampling after a teleport. */
  placement: { seq: number; actor: string; pos: V3 } = { seq: 0, actor: '', pos: { x: 0, y: 0, z: 0 } };
  onError: (e: string) => void = (e) => console.error('[physics]', e);
  ready: Promise<string>;
  colReader = '';
  private isReady = false;
  private queue: { m: ToWorker; transfer: Transferable[] }[] = [];

  constructor() {
    const ready = loading.begin('Physics worker · Rapier WASM');
    this.worker = new Worker(new URL('./worker.ts', import.meta.url), { type: 'module', name: 'physics' });
    let resolveReady!: (v: string) => void;
    this.ready = new Promise((r) => (resolveReady = r));
    this.worker.onmessage = (ev: MessageEvent<FromWorker>) => {
      const m = ev.data;
      switch (m.type) {
        case 'ready': ready(`Rapier ${m.rapier} · ${m.wasmBytes ? loadBytes(m.wasmBytes) : 'WASM ready'} · ${m.colReader}`); this.colReader = m.colReader; this.isReady = true; if (this.inputMailbox && this.stateMailbox) this.worker.postMessage({ type: 'exchange', input: this.inputMailbox.buffer, state: this.stateMailbox.buffer }); for (const q of this.queue.splice(0)) this.worker.postMessage(q.m, q.transfer); resolveReady(m.rapier); break;
        case 'state':
          this.acceptState(m);
          this.send({ type: 'stateAck', revision: m.revision ?? 0 });
          break;
        case 'tileLoaded': this.loaded.set(m.stats.id, m.stats); this.tileWaiters.get(m.stats.id)?.resolve(m.stats); this.tileWaiters.delete(m.stats.id); break;
        case 'tileError': this.loaded.delete(m.id); this.tileWaiters.get(m.id)?.reject(Object.assign(new Error(m.error), { permanent: m.permanent })); this.tileWaiters.delete(m.id); break;
        case 'tileUnloaded': this.loaded.delete(m.id); break;
        case 'cameraCastResult': case 'raycastResult': case 'statsResult': case 'checkResult': { const cb = this.pending.get(m.id); this.pending.delete(m.id); cb?.(m); break; }
        case 'stepError':
          this.onError(`step ${m.seq}: ${m.error}`);
          break;
        case 'error': this.onError(m.error); break;
      }
    };
    this.worker.onerror = (e) => {
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
  /** Decodes a .col in the worker without adding it: resolves with its triangle count, rejects when unreadable or
   *  empty. Transfers the buffer. */
  checkTile(buffer: ArrayBuffer): Promise<number> {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, (m) => {
        if (m.type !== 'checkResult') return reject(new Error(`unexpected ${m.type}`));
        if (m.error === null) resolve(m.tris); else reject(new Error(m.error));
      });
      this.send({ type: 'checkTile', id, buffer }, [buffer]);
    });
  }
  unloadTile(id: string) { this.send({ type: 'unloadTile', id }); this.loaded.delete(id); }
  /** Collision tiles that exist but may not be loaded: edge walls stand only where none does (physics/edgeWalls.ts). */
  setKnown(add: string[], remove: string[]) { if (add.length || remove.length) this.send({ type: 'knownTiles', add, remove }); }
  spawnCharacter(pos: V3) { this.send({ type: 'spawnCharacter', pos }); }
  setCharacterEnabled(enabled: boolean, pos?: V3) {
    if (enabled && pos) this.placed('character', pos);
    this.send({ type: 'setCharacterEnabled', enabled, pos });
  }
  spawnVehicle(pos: V3, yaw: number, spec: Partial<VehicleSpec> = {}) { this.send({ type: 'spawnVehicle', spec: { id: spec.id ?? 'player', pos, yaw, ...spec } }); }
  removeVehicle(id: string) { this.send({ type: 'removeVehicle', id }); }
  driveVehicle(id: string | null) { this.send({ type: 'driveVehicle', id }); }
  resetVehicle(pos: V3, yaw: number, id = 'player') { this.placed(id, pos); this.send({ type: 'resetVehicle', id, pos, yaw }); }
  vehicleImpulse(id: string, impulse: V3) { this.send({ type: 'vehicleImpulse', id, impulse }); }
  setRemotes(bodies: RemoteBody[]) { this.send({ type: 'remotes', bodies }); }
  setPoles(add: PoleSpec[], remove: string[]) { if (add.length || remove.length) this.send({ type: 'poles', add, remove }); }
  breakPole(id: string) { this.send({ type: 'breakPole', id }); }
  private placed(actor: string, pos: V3) { this.placement = { seq: this.placement.seq + 1, actor, pos: { ...pos } }; }

  /** Explicit stepping for placement/query barriers and deterministic tools, independent of live clocking. */
  step(dt: number, character: CharacterInput, vehicle: VehicleInput | null) {
    this.send({ type: 'step', seq: ++this.seq, dt, character, vehicle });
  }

  /** Called every render frame, including paused frames. Never waits for a reply. */
  updateInput(character: CharacterInput, vehicle: VehicleInput | null, running: boolean) {
    void this.latest;
    const input: LiveInput = { character: { ...character, jump: false }, vehicle, running,
      jump: this.jump += Number(character.jump && running), at: performance.timeOrigin + performance.now(), acknowledged: this.state?.revision ?? 0 };
    if (this.inputMailbox) this.inputMailbox.write(input);
    else this.send({ type: 'input', input });
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
