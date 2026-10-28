// Physics Web Worker: owns the Rapier world. Protocol in ./protocol.ts.
import * as RAPIER from '@dimforge/rapier3d';
import { PhysicsWorld } from './world';
import { ActorUpdates } from './actorExchange';
import type { VehicleState, PoleState } from './protocol';
import { DT } from './constants';
import { colReaderName } from './colTile';
import { ColFormatError } from '../shared/col/colFormat';
import { PhysicsClock, SharedMailbox } from './exchange';
import type { LiveInput, PhysicsState } from './protocol';
import type { FromWorker, ToWorker } from './protocol';

// typed locally (the DOM lib is global in this project; avoid pulling the WebWorker lib in)
const ctx = self as unknown as { postMessage(m: FromWorker, transfer: Transferable[]): void; onmessage: ((ev: MessageEvent<ToWorker>) => void) | null };
let pw: PhysicsWorld | null = null;
const post = (m: FromWorker, transfer: Transferable[] = []) => ctx.postMessage(m, transfer);

// Tile loads are async (decode); serialise all messages so a step never sees a half-built tile.
let chain: Promise<void> = Promise.resolve();
ctx.onmessage = (ev: MessageEvent<ToWorker>) => {
  const msg = ev.data;
  chain = chain.then(() => handle(msg)).catch((e) => {
    const error = String((e as Error)?.stack ?? e);
    post(msg.type === 'step' ? { type: 'stepError', seq: msg.seq, error } : { type: 'error', error });
  });
};

let inputMailbox: SharedMailbox<LiveInput> | null = null;
let stateMailbox: SharedMailbox<PhysicsState> | null = null;
let input: LiveInput | null = null;
let jump = 0, stateSeq = 0;
let statePending = false;
let revision = 0, acknowledged = 0;
const vehicles = new ActorUpdates<VehicleState>(), poleUpdates = new ActorUpdates<PoleState>();
const clock = new PhysicsClock();
const localBreaks = new Set<string>();
function snapshot(seq: number, dt: number, character: LiveInput['character'], vehicle: LiveInput['vehicle']) {
  if (!pw) return;
  const t0 = performance.now();
  const substeps = pw.step(dt, character, vehicle);
  const poles = pw.poleStates(true);
  revision++;
  const vehicleDelta = vehicles.sample(pw.vehicleStates(), revision, acknowledged);
  // The renderer may skip snapshots. Keep locally-caused break edges until its stable-ID dedup consumes them.
  for (const p of poles) { if (p.brokeNow) localBreaks.add(p.id); p.brokeNow = localBreaks.has(p.id); }
  const poleDelta = poleUpdates.sample(poles, revision, acknowledged);
  const state: PhysicsState = { type: 'state', seq, revision, actorsDelta: true, removedVehicles: vehicleDelta.removed, removedPoles: poleDelta.removed, substeps, tick: pw.tick, remainder: pw.remainder,
    previousCharacter: pw.previousCharacter, previousVehicle: pw.previousVehicle,
    stepMs: performance.now() - t0, character: pw.characterState(), vehicle: pw.vehicleState(),
    vehicles: vehicleDelta.updates, poles: poleDelta.updates, publishedAt: performance.timeOrigin + performance.now() };
  if (stateMailbox) stateMailbox.write(state);
  else if (!statePending) { statePending = true; post(state); }
}
let nextWake = performance.now() + DT * 1000;
function schedule() {
  setTimeout(() => {
    // Join the same chain as tile decode and actor commands. Only one timer may be queued.
    chain = chain.then(() => {
      input = inputMailbox?.read() ?? input;
      acknowledged = Math.max(acknowledged, input?.acknowledged ?? 0);
      const now = performance.now();
      nextWake = now + DT * 1000;
      const running = !!input?.running && performance.timeOrigin + now - input.at < 500;
      const dt = clock.advance(now, running);
      if (input && dt > 0) {
        snapshot(++stateSeq, dt, { ...input.character, jump: input.jump !== jump }, input.vehicle);
        jump = input.jump;
      }
    }).catch(e => post({ type: 'error', error: String(e) })).finally(schedule);
  }, Math.max(0, nextWake - performance.now()));
}
schedule();

// Messages that arrive while the module (and its wasm, via top-level await) is still evaluating can be
// dropped by the browser, so the worker announces itself with 'ready' and the client queues until then.
pw = new PhysicsWorld(RAPIER);
post({ type: 'ready', rapier: RAPIER.version(), colReader: colReaderName,
  wasmBytes: performance.getEntriesByType('resource').filter(e => e.name.includes('.wasm'))
    .reduce((sum, e) => sum + (e as PerformanceResourceTiming).decodedBodySize, 0) });

async function handle(msg: ToWorker) {
  if (msg.type === 'init') { pw = new PhysicsWorld(RAPIER); post({ type: 'ready', rapier: RAPIER.version(), colReader: colReaderName }); return; }
  if (!pw) throw new Error(`physics worker not initialised (got ${msg.type})`);
  switch (msg.type) {
    case 'exchange': inputMailbox = new SharedMailbox(msg.input); stateMailbox = new SharedMailbox(msg.state); break;
    case 'input': input = msg.input; break;
    case 'stateAck': statePending = false; acknowledged = Math.max(acknowledged, msg.revision); break;
    case 'loadTile':
      try { post({ type: 'tileLoaded', stats: await pw.loadTile(msg.id, msg.buffer) }); }
      catch (e) { post({ type: 'tileError', id: msg.id, error: String((e as Error).message ?? e), permanent: e instanceof ColFormatError }); }
      break;
    case 'checkTile':
      try { post({ type: 'checkResult', id: msg.id, tris: (await pw.checkTile(msg.buffer)).tris, error: null }); }
      catch (e) { post({ type: 'checkResult', id: msg.id, tris: 0, error: String((e as Error).message ?? e) }); }
      break;
    case 'unloadTile':
      pw.unloadTile(msg.id);
      post({ type: 'tileUnloaded', id: msg.id });
      break;
    case 'knownTiles': pw.edges.setKnown(msg.add, msg.remove); break;
    case 'spawnCharacter': pw.spawnCharacter(msg.pos); break;
    case 'setCharacterEnabled': pw.setCharacterEnabled(msg.enabled, msg.pos); break;
    case 'removeVehicle': pw.removeVehicle(msg.id); break;
    case 'spawnVehicle': pw.spawnVehicle(msg.spec.pos, msg.spec.yaw, msg.spec); break;
    case 'driveVehicle': pw.driveVehicle(msg.id); break;
    case 'resetVehicle': pw.resetVehicle(msg.pos, msg.yaw, msg.id); break;
    case 'remotes': pw.setRemotes(msg.bodies); break;
    case 'poles': pw.setPoles(msg.add, msg.remove); break;
    case 'breakPole': pw.breakPole(msg.id); break;
    case 'step': {
      snapshot(msg.seq, msg.dt, msg.character, msg.vehicle);
      break;
    }
    case 'cameraCast':
      post({ type: 'cameraCastResult', id: msg.id, distance: pw.cameraCast(msg.origin, msg.dir, msg.distance) });
      break;
    case 'raycast':
      post({ type: 'raycastResult', id: msg.id, hit: pw.raycast(msg.origin, msg.dir, msg.maxToi, msg.filter) });
      break;
    case 'stats':
      post({ type: 'statsResult', id: msg.id, tiles: pw.tileStats(), ...pw.totals() });
      break;
  }
}
