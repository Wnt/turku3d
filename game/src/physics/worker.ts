// Physics Web Worker: owns the Rapier world. Protocol in ./protocol.ts.
import * as RAPIER from '@dimforge/rapier3d';
import { PhysicsWorld } from './world';
import { ActorUpdates } from './actorExchange';
import type { VehicleState, PoleState, DebrisState } from './protocol';
import { DT } from './constants';
import { colReaderName, decoderReady } from './colTile';
import { ColFormatError } from '../shared/col/colFormat';
import { PhysicsClock, SharedMailbox } from './exchange';
import type { LiveInput, PhysicsState, PhysStall } from './protocol';
import type { FromWorker, ToWorker } from './protocol';

// typed locally (the DOM lib is global in this project; avoid pulling the WebWorker lib in)
const ctx = self as unknown as { postMessage(m: FromWorker, transfer: Transferable[]): void; onmessage: ((ev: MessageEvent<ToWorker>) => void) | null };
let pw: PhysicsWorld | null = null;
const post = (m: FromWorker, transfer: Transferable[] = []) => ctx.postMessage(m, transfer);

// Serialise all messages with the step timer. Cell installs are queued and built in budgeted pieces between steps
// (tileInstall.ts), so no message holds a step for longer than one piece.
let chain: Promise<void> = Promise.resolve();
ctx.onmessage = (ev: MessageEvent<ToWorker>) => {
  const msg = ev.data;
  chain = chain.then(() => { const t0 = performance.now(); handle(msg); busy(msg.type, performance.now() - t0); }).catch((e) => {
    const error = String((e as Error)?.stack ?? e);
    post(msg.type === 'step' ? { type: 'stepError', seq: msg.seq, error } : { type: 'error', error });
  });
};

/** Worker time per physics tick for queued cell installs (ms): while an actor moves, while one is held at missing
 *  collision, and while nothing runs (menu, pause, start-up loading). A piece is ~1-2 ms. */
const INSTALL_BUDGET = { running: 2, blocked: 8, idle: 12 } as const;
/** Worker time per idle tick for the ambient traffic warm-up (TrafficSim.idle). */
const TRAFFIC_IDLE_MS = 6;
/** Stall reports: a tick gap longer than GAP_MS, a snapshot step longer than STEP_MS, one install piece longer
 *  than PIECE_MS. Kept until acknowledged (at most MAX_STALLS). */
const STALL = { gapMs: 50, stepMs: 12, pieceMs: 6, max: 32 } as const;
let stalls: PhysStall[] = [], stallId = 0;
const busySince: Record<string, number> = {};
function busy(kind: string, ms: number) { if (ms > 0.05) busySince[kind] = (busySince[kind] ?? 0) + ms; }
function stall(event: Omit<PhysStall, 'id' | 'revision' | 'wall'>) {
  stalls.push({ ...event, ms: +event.ms.toFixed(1), id: ++stallId, revision: 0, wall: performance.timeOrigin + performance.now() });
  if (stalls.length > STALL.max) stalls = stalls.slice(-STALL.max);
}
function pendingStalls(): PhysStall[] | undefined {
  for (const s of stalls) if (!s.revision) s.revision = revision;
  stalls = stalls.filter(s => s.revision > acknowledged);
  return stalls.length ? stalls : undefined;
}

let inputMailbox: SharedMailbox<LiveInput> | null = null;
let stateMailbox: SharedMailbox<PhysicsState> | null = null;
let input: LiveInput | null = null;
let jump = 0, stateSeq = 0;
let statePending = false;
let revision = 0, acknowledged = 0;
const vehicles = new ActorUpdates<VehicleState>(), poleUpdates = new ActorUpdates<PoleState>(), debrisUpdates = new ActorUpdates<DebrisState>();
const clock = new PhysicsClock();
const localBreaks = new Set<string>();
function snapshot(seq: number, dt: number, character: LiveInput['character'], vehicle: LiveInput['vehicle']) {
  if (!pw) return;
  const t0 = performance.now();
  const substeps = pw.step(dt, character, vehicle);
  const stepMs = performance.now() - t0;
  if (stepMs > STALL.stepMs) stall({ cause: 'step', ms: stepMs, substeps, traffic: +pw.lastTrafficMs.toFixed(1) });
  const poles = pw.poleStates(true);
  revision++;
  // The own (driven) car travels once, as `vehicle`; the client adds it to its list (state `vehicleApart`).
  const vehicleStates = pw.vehicleStates(), vehicleId = pw.ownVehicleId;
  const own = vehicleStates.find(v => v.id === vehicleId) ?? null;
  const vehicleDelta = vehicles.sample(vehicleStates, revision, acknowledged, own ? vehicleId : undefined);
  // The renderer may skip snapshots. Keep locally-caused break edges until its stable-ID dedup consumes them.
  for (const p of poles) { if (p.brokeNow) localBreaks.add(p.id); p.brokeNow = localBreaks.has(p.id); }
  const poleDelta = poleUpdates.sample(poles, revision, acknowledged), debrisDelta = debrisUpdates.sample(pw.debrisStates(), revision, acknowledged);
  const state: PhysicsState = { type: 'state', seq, revision, actorsDelta: true, removedVehicles: vehicleDelta.removed, removedPoles: poleDelta.removed, substeps, tick: pw.tick, remainder: pw.remainder,
    previousCharacter: pw.previousCharacter, previousVehicle: pw.previousVehicle, coverageBlock: pw.coverageBlock,
    stepMs: performance.now() - t0, character: pw.characterState(), vehicle: own, vehicleApart: true,
    vehicles: vehicleDelta.updates, poles: poleDelta.updates, publishedAt: performance.timeOrigin + performance.now(),
    ...(debrisDelta.updates.length || debrisDelta.removed.length ? { debris: debrisDelta.updates, removedDebris: debrisDelta.removed } : {}),
    ...(pw.traffic ? { traffic: pw.traffic.publish(), trafficEvents: pw.traffic.pending(acknowledged, revision), trafficStats: pw.traffic.stats,
      trafficTime: pw.traffic.time } : {}), ...(stalls.length ? { stalls: pendingStalls() } : {}) };
  const proxies = pw.remoteProxies();
  if (proxies.length) state.remoteProxies = proxies;
  const remoteCars = pw.remoteCarDraws();
  if (remoteCars.length) state.remoteCars = remoteCars;
  if (stateMailbox) stateMailbox.write(state);
  else if (!statePending) { statePending = true; post(state); }
  // Not in the state: a mailbox keeps only the latest state, and every snapshot has to reach the room.
  const snapshot = pw.traffic?.takeSnapshot();
  if (snapshot) post({ type: 'trafficSnapshot', snapshot });
}
let nextWake = performance.now() + DT * 1000;
let lastTick = performance.now();
function schedule() {
  setTimeout(() => {
    // Join the same chain as actor commands. Only one timer may be queued.
    chain = chain.then(() => {
      input = inputMailbox?.read() ?? input;
      acknowledged = Math.max(acknowledged, input?.acknowledged ?? 0);
      let now = performance.now();
      nextWake = now + DT * 1000;
      const running = !!input?.running && performance.timeOrigin + now - input.at < 500;
      if (pw) {
        const budget = pw.coverageUrgent ? INSTALL_BUDGET.blocked : running ? INSTALL_BUDGET.running : INSTALL_BUDGET.idle;
        const used = pw.pumpInstalls(budget);
        busy('install', used);
        const { slowest, queued } = pw.installState;
        if (slowest.ms > STALL.pieceMs) stall({ cause: 'install', ms: slowest.ms, cell: slowest.key, phase: slowest.phase, queued });
        now = performance.now();
      }
      const gap = now - lastTick;
      if (gap > STALL.gapMs && running) {
        const detail: Record<string, number> = {};
        for (const k in busySince) detail[k] = +busySince[k].toFixed(1);
        stall({ cause: 'gap', ms: gap, busy: detail, queued: pw?.installState.queued ?? 0 });
      }
      for (const k in busySince) delete busySince[k];
      lastTick = now;
      const dt = clock.advance(now, running);
      if (pw?.traffic) {
        pw.traffic.setView(input?.view ?? null);
        pw.trafficTime = pw.traffic.clock(performance.timeOrigin + now, running);
        // Loading and the menu leave the worker idle between install pieces: the traffic warm-up goes on there.
        if (!running) pw.traffic.idle(pw.trafficTime, TRAFFIC_IDLE_MS);
      }
      if (input && dt > 0) {
        const s0 = performance.now();
        snapshot(++stateSeq, dt, { ...input.character, jump: input.jump !== jump }, input.vehicle);
        busy('step', performance.now() - s0);
        jump = input.jump;
      }
    }).catch(e => post({ type: 'error', error: String(e) })).finally(schedule);
  }, Math.max(0, nextWake - performance.now()));
}
schedule();

// Messages that arrive while the module (and its wasm, via top-level await) is still evaluating can be
// dropped by the browser, so the worker announces itself with 'ready' and the client queues until then.
pw = new PhysicsWorld(RAPIER);
await decoderReady;
post({ type: 'ready', rapier: RAPIER.version(), colReader: colReaderName,
  wasmBytes: performance.getEntriesByType('resource').filter(e => e.name.includes('.wasm'))
    .reduce((sum, e) => sum + (e as PerformanceResourceTiming).decodedBodySize, 0) });

function handle(msg: ToWorker) {
  if (msg.type === 'init') { pw = new PhysicsWorld(RAPIER); post({ type: 'ready', rapier: RAPIER.version(), colReader: colReaderName }); return; }
  if (!pw) throw new Error(`physics worker not initialised (got ${msg.type})`);
  switch (msg.type) {
    case 'exchange': inputMailbox = new SharedMailbox(msg.input); stateMailbox = new SharedMailbox(msg.state); break;
    case 'input': input = msg.input; break;
    case 'stateAck': statePending = false; acknowledged = Math.max(acknowledged, msg.revision); break;
    case 'loadTile': {
      const id = msg.id;
      pw.requestTile(id, msg.buffer, result => post(result instanceof Error
        ? { type: 'tileError', id, error: String(result.message ?? result), permanent: result instanceof ColFormatError }
        : { type: 'tileLoaded', stats: result }));
      break;
    }
    case 'checkTile': {
      const id = msg.id;
      pw.requestCheck(String(id), msg.buffer, result => post(result instanceof Error
        ? { type: 'checkResult', id, tris: 0, error: String(result.message ?? result) }
        : { type: 'checkResult', id, tris: result.tris, error: null }));
      break;
    }
    case 'unloadTile':
      pw.unloadTile(msg.id);
      post({ type: 'tileUnloaded', id: msg.id });
      break;
    case 'loadingTile': pw.setTileLoading(msg.id, msg.loading); break;
    case 'knownTiles': pw.edges.setKnown(msg.add, msg.remove); break;
    case 'comingTiles': pw.setComing(msg.add, msg.remove); break;
    case 'spawnCharacter': pw.spawnCharacter(msg.pos); break;
    case 'setCharacterEnabled': pw.setCharacterEnabled(msg.enabled, msg.pos); break;
    case 'removeVehicle': pw.removeVehicle(msg.id); break;
    case 'spawnVehicle': pw.spawnVehicle(msg.spec.pos, msg.spec.yaw, msg.spec); break;
    case 'driveVehicle': pw.driveVehicle(msg.id); break;
    case 'resetVehicle': pw.resetVehicle(msg.pos, msg.yaw, msg.id); break;
    case 'vehicleImpulse': pw.vehicleImpulse(msg.id, msg.impulse); break;
    case 'damageVehicle': pw.damageVehicle(msg.id, msg.hit); break;
    case 'scrapeVehicle': pw.scrapeVehicle(msg.id, msg.scrape); break;
    case 'vehicleDamage': pw.setVehicleDamage(msg.id, msg.state); break;
    case 'ownVehicleDamage': pw.ownVehicleDamage(msg.id); break;
    case 'remotes': pw.setRemotes(msg.bodies); break;
    case 'follow': pw.follow(msg.id, msg.state); break;
    case 'poles': pw.setPoles(msg.add, msg.remove); break;
    case 'breakPole': pw.breakPole(msg.id); break;
    case 'spawnDebris': pw.spawnDebris(msg.spec); break;
    case 'traffic': pw.trafficSim().configure(msg.config); break;
    case 'trafficTiles': pw.trafficSim().setTiles(msg.add, msg.remove); break;
    case 'trafficPedestrians': pw.trafficSim().flow.setPedestrians(msg.people, msg.time); break;
    case 'trafficClaim': pw.traffic?.claim(msg.handle); break;
    case 'trafficMirror': pw.traffic?.mirror(msg.snapshot); break;
    case 'step': {
      if (pw.traffic) pw.trafficTime = pw.traffic.clock(performance.timeOrigin + performance.now(), true);
      snapshot(msg.seq, msg.dt, msg.character, msg.vehicle);
      break;
    }
    case 'cameraCast':
      post({ type: 'cameraCastResult', id: msg.id, distance: pw.cameraCast(msg.origin, msg.dir, msg.distance) });
      break;
    case 'raycast': {
      const world = pw;
      post({ type: 'raycastResult', id: msg.id, hits: msg.rays.map(ray => world.raycast(ray.origin, ray.dir, ray.maxToi, ray.filter)) });
      break;
    }
    case 'stats':
      post({ type: 'statsResult', id: msg.id, tiles: pw.tileStats(), ...pw.totals() });
      break;
    case 'trafficProbe':
      post({ type: 'trafficProbeResult', id: msg.id, probe: pw.traffic ? pw.traffic.flow.probe() : null });
      break;
  }
}
