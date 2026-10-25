// Each worker owns its Rapier WASM instance; CPU-bound fixture simulations run in parallel.
import { parentPort, workerData } from 'node:worker_threads';
import { join } from 'node:path';
import { DEFAULT_OPTIONS, validate } from '../src/validate.ts';

const { name, directory, walkers, checks } = workerData;
parentPort!.postMessage(await validate({
  ...DEFAULT_OPTIONS,
  inputs: [join(directory, name)],
  footprints: join(directory, name, 'footprints.geojson'),
  walkers,
  checks,
}));
