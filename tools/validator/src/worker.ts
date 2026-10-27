import { parentPort } from 'node:worker_threads';
import { validateSerial } from './validate.ts';
parentPort!.on('message', async options => {
  try { parentPort!.postMessage(await validateSerial(options)); }
  catch (error) { parentPort!.postMessage({ error: String(error instanceof Error ? error.stack : error) }); }
});
