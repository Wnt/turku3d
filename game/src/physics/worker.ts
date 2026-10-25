// Physics Web Worker: owns the Rapier world. Protocol in ./protocol.ts.
import * as RAPIER from '@dimforge/rapier3d';
import { PhysicsWorld } from './world';
import { colReaderName } from './colTile';
import type { FromWorker, ToWorker } from './protocol';

// typed locally (the DOM lib is global in this project; avoid pulling the WebWorker lib in)
const ctx = self as unknown as { postMessage(m: FromWorker, transfer: Transferable[]): void; onmessage: ((ev: MessageEvent<ToWorker>) => void) | null };
let pw: PhysicsWorld | null = null;
const post = (m: FromWorker, transfer: Transferable[] = []) => ctx.postMessage(m, transfer);

// Tile loads are async (decode); serialise all messages so a step never sees a half-built tile.
let chain: Promise<void> = Promise.resolve();
ctx.onmessage = (ev: MessageEvent<ToWorker>) => {
  const msg = ev.data;
  chain = chain.then(() => handle(msg)).catch((e) => post({ type: 'error', error: String((e as Error)?.stack ?? e) }));
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
      catch (e) { post({ type: 'tileError', id: msg.id, error: String((e as Error).message ?? e) }); }
      break;
    case 'unloadTile':
      pw.unloadTile(msg.id);
      post({ type: 'tileUnloaded', id: msg.id });
      break;
    case 'spawnCharacter': pw.spawnCharacter(msg.pos); break;
    case 'setCharacterEnabled': pw.setCharacterEnabled(msg.enabled, msg.pos); break;
    case 'spawnVehicle': pw.spawnVehicle(msg.pos, msg.yaw); break;
    case 'resetVehicle': pw.resetVehicle(msg.pos, msg.yaw); break;
    case 'step': {
      const t0 = performance.now();
      const substeps = pw.step(msg.dt, msg.character, msg.vehicle);
      post({ type: 'state', seq: msg.seq, substeps, tick: pw.tick, remainder: pw.remainder, previousCharacter: pw.previousCharacter, stepMs: performance.now() - t0, character: pw.characterState(), vehicle: pw.vehicleState() });
      break;
    }
    case 'raycast':
      post({ type: 'raycastResult', id: msg.id, hit: pw.raycast(msg.origin, msg.dir, msg.maxToi, msg.filter) });
      break;
    case 'stats':
      post({ type: 'statsResult', id: msg.id, tiles: pw.tileStats(), ...pw.totals() });
      break;
  }
}
