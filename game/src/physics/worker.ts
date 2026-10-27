// Physics Web Worker: owns the Rapier world. Protocol in ./protocol.ts.
import * as RAPIER from '@dimforge/rapier3d';
import { PhysicsWorld } from './world';
import { colReaderName } from './colTile';
import { ColFormatError } from '../shared/col/colFormat';
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

// Messages that arrive while the module (and its wasm, via top-level await) is still evaluating can be
// dropped by the browser, so the worker announces itself with 'ready' and the client queues until then.
pw = new PhysicsWorld(RAPIER);
post({ type: 'ready', rapier: RAPIER.version(), colReader: colReaderName });

async function handle(msg: ToWorker) {
  if (msg.type === 'init') { pw = new PhysicsWorld(RAPIER); post({ type: 'ready', rapier: RAPIER.version(), colReader: colReaderName }); return; }
  if (!pw) throw new Error(`physics worker not initialised (got ${msg.type})`);
  switch (msg.type) {
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
    case 'spawnCharacter': pw.spawnCharacter(msg.pos); break;
    case 'setCharacterEnabled': pw.setCharacterEnabled(msg.enabled, msg.pos); break;
    case 'spawnVehicle': pw.spawnVehicle(msg.spec.pos, msg.spec.yaw, msg.spec); break;
    case 'driveVehicle': pw.driveVehicle(msg.id); break;
    case 'resetVehicle': pw.resetVehicle(msg.pos, msg.yaw, msg.id); break;
    case 'remotes': pw.setRemotes(msg.bodies); break;
    case 'step': {
      const t0 = performance.now();
      const substeps = pw.step(msg.dt, msg.character, msg.vehicle);
      post({ type: 'state', seq: msg.seq, substeps, tick: pw.tick, remainder: pw.remainder, previousCharacter: pw.previousCharacter, previousVehicle: pw.previousVehicle, stepMs: performance.now() - t0, character: pw.characterState(), vehicle: pw.vehicleState(), vehicles: pw.vehicleStates() });
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
