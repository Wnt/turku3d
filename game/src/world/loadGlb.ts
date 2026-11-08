import { FileLoader, LoaderUtils } from 'three/webgpu';
import type { GLTFLoader, GLTF, GLTFLoaderPlugin } from 'three/addons/loaders/GLTFLoader.js';
import { streamingBudget } from '../render/preparationBudget';
import { applyJsonRecords } from './glbRecords';
import type { decodeGlb } from './decodeGlb';
import { AssetCache, sharedAssetKey, sharedBuffers } from './sharedAssets';
import { streamTrace } from '../telemetry/streamTrace';

type Decoded = ReturnType<typeof decodeGlb>;
let worker: Worker | undefined, sequence = 0;
const budget = streamingBudget();
// Cache immutable visual bytes, not mutable glTF objects or their render resources.
// 32 MiB keeps the last few tile GLBs for a quick U-turn (the return cache keeps whole tiles for a minute); the
// census showed 248 MiB here at a 256 MiB cap. A later revisit refetches from the HTTP cache.
export const visualBuffers = new AssetCache<ArrayBuffer>(32 * 1024 ** 2, buffer => buffer.byteLength);
const pending = new Map<number, { objects: Record<string, unknown>[]; resolve: (decoded: Decoded) => void; reject: (error: Error) => void }>();
function decode(buffer: ArrayBuffer): Promise<Decoded> {
  if (!worker) {
    worker = new Worker(new URL('./decodeGlb.worker.ts', import.meta.url), { type: 'module' });
    worker.onmessage = async ({ data }) => {
      const job = pending.get(data.id);
      if (!job) return;
      if (data.error) { pending.delete(data.id); job.reject(new Error(data.error)); return; }
      // Every reply's record application runs inside the frame's streaming window, like streamingJson's.
      const pause = budget.checkpoint();
      if (pause) await pause;
      streamTrace.time('parse', () => applyJsonRecords(job.objects, data.rows));
      if (data.done) { pending.delete(data.id); job.resolve({ json: job.objects[0], binary: data.binary }); }
      else worker?.postMessage({ id: data.id });
    };
    worker.onerror = event => {
      for (const job of pending.values()) job.reject(new Error(event.message));
      pending.clear(); worker?.terminate(); worker = undefined;
    };
  }
  const id = ++sequence;
  return new Promise((resolve, reject) => { pending.set(id, { objects: [{}], resolve, reject }); worker!.postMessage({ id, buffer }, [buffer]); });
}

/** Waits until the frame's streaming window has room (again after every wake: all waiters resume together). */
async function pace() {
  let pause = budget.checkpoint();
  while (pause) { await pause; pause = budget.checkpoint(); }
}

/** A fresh task without timer clamping. */
const taskChannel = new MessageChannel(), taskQueue: (() => void)[] = [];
taskChannel.port1.onmessage = () => taskQueue.shift()?.();
const nextTask = () => new Promise<void>(resolve => { taskQueue.push(resolve); taskChannel.port2.postMessage(0); });
let admissions: Promise<void> = Promise.resolve();
/** Admits one paced glTF continuation at a time, each in its own task: the previous one's synchronous work (the
 *  geometry or mesh it completes) has run and counts against the streaming window before the next is let in.
 *  Checking all waiters at once let them all pass before any of that work ran. */
function admit(): Promise<void> {
  const turn = admissions.then(nextTask).then(pace);
  admissions = turn.catch(() => {});
  return turn;
}

/** A glTF with at most this many accessors and meshes parses in one go once its read is admitted (loadGlb's pace).
 *  Its whole parse is about half a millisecond of main-thread work, yet paced one continuation per task it waited in
 *  the shared admission queue: building models (23 steps each) took 168 ms p50, 1.1 s p90 to parse on the Air at
 *  23 m/s, at ~300 admissions a second, about five per frame (fastdrive glb.load trace, 2026-11-08). Their reads hold
 *  the six model read slots, so a tile's buildings waited seconds. Big tile GLBs (hundreds of steps) stay paced. */
export const UNPACED_STEPS = 32;

/** Feed decoded JSON and its immutable binary chunk to the same GLTF plugins without base64 or a second fetch. */
export function parseDecodedGlb(loader: GLTFLoader, decoded: Decoded, path: string, admissions?: { count: number }): Promise<GLTF> {
  const plugin = (parser: Parameters<Parameters<GLTFLoader['register']>[0]>[0]): GLTFLoaderPlugin => ({
    name: 'TURKU_decoded_glb', beforeRoot: async () => {
      // External meshopt buffers share the same immutable bytes across separate glTF parsers.
      const original = parser.loadBuffer.bind(parser);
      parser.loadBuffer = index => {
        const uri = parser.json.buffers?.[index]?.uri;
        const key = uri && sharedAssetKey(new URL(uri, path).href);
        return key ? sharedBuffers.get(key, () => original(index)) : original(index);
      };
      if (decoded.binary) (parser as unknown as { cache: { add(key: string, value: Promise<ArrayBuffer>): void } }).cache.add('buffer:0', Promise.resolve(decoded.binary));
      // Paced continuations: when meshopt's worker answers, everything waiting on that buffer view (accessors,
      // geometries, meshes, nodes, the scene) ran as microtasks of that one message: 180-283 ms frames per streamed tile
      // on the Windows laptop, 127-139 ms on the M1 Max (verify-all r3, LoAF Worker.onmessage in the meshopt decoder).
      // Each resolved accessor and mesh of a big glTF now waits for room in the frame's streaming window, re-checked
      // after waking; a small one (UNPACED_STEPS) runs in one go.
      const json = parser.json as { accessors?: unknown[]; meshes?: unknown[] };
      if ((json.accessors?.length ?? 0) + (json.meshes?.length ?? 0) <= UNPACED_STEPS) return;
      const paced = <T>(value: T) => { if (admissions) admissions.count++; return admit().then(() => value); };
      const loadAccessor = parser.loadAccessor.bind(parser), loadMesh = parser.loadMesh.bind(parser);
      parser.loadAccessor = index => loadAccessor(index).then(paced);
      parser.loadMesh = index => loadMesh(index).then(paced);
    },
  });
  loader.register(plugin);
  try {
    // r186 accepts pre-parsed JSON; DefinitelyTyped exposes only its string/binary inputs.
    return (loader.parseAsync as unknown as (json: object, path: string) => Promise<GLTF>)(decoded.json, path);
  } finally { loader.unregister(plugin); }
}
/** A GLB v2 container whose length field matches its bytes (decodeGlb's header check). */
export function completeGlb(buffer: ArrayBuffer) {
  if (buffer.byteLength < 12) return false;
  const view = new DataView(buffer);
  return view.getUint32(0, true) === 0x46546c67 && view.getUint32(8, true) === buffer.byteLength;
}
/** The GLB bytes at `url`. Tile GLBs are immutable for a year in the HTTP cache, and a response cut short without a
 *  length (a brotli stream through the proxies) is cached as if complete: a Windows client had 385,991 of a shared
 *  model's 1,042,328 bytes cached, so its tile failed every retry and city.lab never left the loading screen. A GLB
 *  shorter than its header says is fetched once more past the HTTP cache, which also replaces the cached copy. */
export async function fetchGlb(url: string, load: () => Promise<ArrayBuffer>, reload = (href: string) => fetch(href, { cache: 'reload' })) {
  const first = await load();
  if (completeGlb(first)) return first;
  const res = await reload(url);
  if (!res.ok) throw new Error(`GLB refetch ${res.status}: ${url}`);
  const again = await res.arrayBuffer();
  console.warn(`[glb] ${url}: ${first.byteLength} cached bytes of ${completeGlb(again) ? again.byteLength : '?'}; refetched past the HTTP cache`);
  return again;
}
export async function loadGlb(loader: GLTFLoader, url: string): Promise<GLTF> {
  const started = streamTrace.on ? performance.now() : 0;
  const load = () => fetchGlb(url, async () => await new FileLoader(loader.manager).setResponseType('arraybuffer').loadAsync(url) as ArrayBuffer);
  const key = /\/coarse\/.*\.([a-f0-9]{64})\.glb(?:[?#].*)?$/.exec(url)?.[1];
  const buffer = key ? await visualBuffers.get(key, load) : await load();
  const fetched = started ? performance.now() : 0, bytes = buffer.byteLength;
  // The worker owns and detaches its input; keep cached originals intact across revisits.
  const decoded = await decode(key ? buffer.slice(0) : buffer);
  const decodedAt = started ? performance.now() : 0;
  // Start the glTF parse inside the frame's streaming window; its continuations pace themselves (parseDecodedGlb).
  await pace();
  if (!started) return parseDecodedGlb(loader, decoded, LoaderUtils.extractUrlBase(url));
  // Install traces split a GLB read into fetch, worker decode, the wait for a window and the paced parse.
  const parsing = performance.now(), admissions = { count: 0 };
  const gltf = await parseDecodedGlb(loader, decoded, LoaderUtils.extractUrlBase(url), admissions);
  streamTrace.event('glb.load', undefined, { ms: performance.now() - started, fetchMs: fetched - started, decodeMs: decodedAt - fetched,
    paceMs: parsing - decodedAt, parseMs: performance.now() - parsing, admits: admissions.count, bytes, tile: !!key });
  return gltf;
}
