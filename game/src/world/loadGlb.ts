import { FileLoader, LoaderUtils } from 'three/webgpu';
import type { GLTFLoader, GLTF, GLTFLoaderPlugin } from 'three/addons/loaders/GLTFLoader.js';
import { streamingAllowance, streamingBudget } from '../render/preparationBudget';
import { afterRender } from '../render/afterRender';
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
type PostTask = (callback: () => void, options: { priority: 'user-blocking' }) => Promise<void>;
const postTask = (globalThis as { scheduler?: { postTask?: PostTask } }).scheduler?.postTask?.bind((globalThis as { scheduler?: object }).scheduler);
/** The admission loop's hop: ahead of ordinary tasks where the Prioritized Task Scheduling API exists, so the time
 *  between an admission and the next is that admission's own work, not other streaming work queued meanwhile. */
const admissionTask = postTask ? () => postTask(() => {}, { priority: 'user-blocking' }) : nextTask;
const nextWindow = () => new Promise<void>(resolve => { afterRender(resolve); setTimeout(resolve, 100); });

/** Lower first: seconds until the player reaches the tile that wants the glTF (coarseTiles arrivalRank). */
export type ParseRank = () => number;
/** One glTF parse with admissions waiting: small glTFs' bursts (one admission each, UNPACED_STEPS) and big glTFs'
 *  continuations (one each). */
interface Parse { small: boolean; rank: ParseRank; seq: number; turns: (() => void)[]; stats?: { hops: number } }
const waiting = new Set<Parse>();
let parseSeq = 0, admitting = false;
const noRank: ParseRank = () => Infinity;
/** The parse to admit next: a small glTF's burst before a big glTF's continuation (a building model frees its read
 *  slot in a burst or two), then the tile the player reaches first, then the older. One FIFO across all big parses
 *  gave each of a dozen tile GLBs parsing at once the same share: at ~one admission per frame a 270-continuation tile
 *  GLB took 25-29 s, the tile under the car as long as the far ones (M1 Max drive 20261108-155752-17lruw: 8 of 79
 *  tiles reached a full model in 62 s). */
function nextParse() {
  let pick: Parse | undefined, best = Infinity;
  for (const parse of waiting) {
    const rank = parse.rank();
    const better = !pick || (parse.small !== pick.small ? parse.small : rank < best || (rank === best && parse.seq < pick.seq));
    if (better) { pick = parse; best = rank; }
  }
  return pick;
}
function admit(parse: Parse): Promise<void> {
  const turn = new Promise<void>(resolve => { parse.turns.push(resolve); });
  waiting.add(parse);
  if (!admitting) void runAdmissions();
  return turn;
}
/** The admissions' own work this frame against the streaming allowance's per-budget limit; a window without frame
 *  reports (a stalled loop) closes after STALE_WINDOW_MS. */
const STALE_WINDOW_MS = 16;
let spent = 0, windowFrame = NaN, windowAt = -Infinity;
function room() {
  const now = performance.now(), frame = streamingAllowance.frameStamp;
  if (frame !== windowFrame || now - windowAt > STALE_WINDOW_MS) { windowFrame = frame; windowAt = now; spent = 0; streamingAllowance.opened(); }
  return spent < streamingAllowance.limit;
}
/** One continuation's work as the admissions measure it (its hop included, shared by the turns let in together). */
let turnMs = 0.5;
/** Hops and the continuations they let in (diagnostics and tests). */
export const admissionCounters = { hops: 0, turns: 0 };
/** Admits the best parse's waiting continuations in one task, as many as the window's room fits at the measured cost
 *  of one: their synchronous work (the geometries and meshes they complete) runs before the hop returns, and the hop's
 *  time counts against the frame's streaming limit. One continuation per hop let ~54 a second through on the M1 Max:
 *  a hop that waits behind a rendered frame charged that frame to one continuation, so a 270-continuation tile GLB
 *  parsed in 5-22 s (Jonni's drive 20261108-174404-q0e3zr: 7 of 120 tiles full in 80 s). Counting wall time from
 *  the window's start charged them with every other budget's work too (43 a second, 20261108-155752-17lruw). */
async function runAdmissions() {
  admitting = true;
  try {
    while (waiting.size) {
      while (!room()) await nextWindow();
      const parse = nextParse();
      if (!parse) break;
      const count = Math.max(1, Math.min(parse.turns.length, Math.floor((streamingAllowance.limit - spent) / turnMs)));
      const turns = parse.turns.splice(0, count);
      if (!parse.turns.length) waiting.delete(parse);
      admissionCounters.hops++; admissionCounters.turns += count;
      if (parse.stats) parse.stats.hops++;
      const started = performance.now();
      for (const turn of turns) turn();
      await admissionTask();
      const elapsed = performance.now() - started;
      spent += elapsed;
      // A hop that waited behind a frame says little about the work: one such sample moves the estimate only so far.
      turnMs += (Math.min(elapsed / count, 4) - turnMs) * 0.25;
    }
  } finally { admitting = false; }
}

/** A glTF with at most this many accessors and meshes is admitted per burst, ahead of big glTFs' continuations: the
 *  continuations one worker answer resolves wait for one admission together (about half a millisecond of work for a
 *  building model). Paced one continuation per task in one queue with the big tile GLBs, building models (23 steps
 *  each) took 168 ms p50, 1.1 s p90 to parse on the Air at 23 m/s, at ~300 admissions a second, about five per frame
 *  (fastdrive glb.load trace, 2026-11-08); one admission each in that queue still waited 117 ms p50 behind the tiles'
 *  hundreds. Model reads hold the six read slots, so a tile's buildings waited seconds. */
export const UNPACED_STEPS = 32;

/** Feed decoded JSON and its immutable binary chunk to the same GLTF plugins without base64 or a second fetch. */
export function parseDecodedGlb(loader: GLTFLoader, decoded: Decoded, path: string, admissions?: { count: number; hops: number }, rank: ParseRank = noRank): Promise<GLTF> {
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
      const loadAccessor = parser.loadAccessor.bind(parser), loadMesh = parser.loadMesh.bind(parser);
      const small = (json.accessors?.length ?? 0) + (json.meshes?.length ?? 0) <= UNPACED_STEPS;
      const parse: Parse = { small, rank, seq: parseSeq++, turns: [], stats: admissions };
      if (small) {
        // A small glTF: the continuations one worker answer resolves wait for one admission together, so the burst
        // still runs inside a frame's streaming window; the gate closes after that task for the next answer.
        let gate: Promise<void> | null = null;
        const together = <T>(value: T) => {
          if (!gate) {
            if (admissions) admissions.count++;
            const opened = gate = admit(parse);
            void opened.then(nextTask).then(() => { if (gate === opened) gate = null; });
          }
          return gate.then(() => value);
        };
        parser.loadAccessor = index => loadAccessor(index).then(together);
        parser.loadMesh = index => loadMesh(index).then(together);
        return;
      }
      const paced = <T>(value: T) => { if (admissions) admissions.count++; return admit(parse).then(() => value); };
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
export async function loadGlb(loader: GLTFLoader, url: string, rank?: ParseRank): Promise<GLTF> {
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
  if (!started) return parseDecodedGlb(loader, decoded, LoaderUtils.extractUrlBase(url), undefined, rank);
  // Install traces split a GLB read into fetch, worker decode, the wait for a window and the paced parse.
  const parsing = performance.now(), admissions = { count: 0, hops: 0 };
  const gltf = await parseDecodedGlb(loader, decoded, LoaderUtils.extractUrlBase(url), admissions, rank);
  streamTrace.event('glb.load', undefined, { ms: performance.now() - started, fetchMs: fetched - started, decodeMs: decodedAt - fetched,
    paceMs: parsing - decodedAt, parseMs: performance.now() - parsing, admits: admissions.count, hops: admissions.hops, bytes, tile: !!key });
  return gltf;
}
