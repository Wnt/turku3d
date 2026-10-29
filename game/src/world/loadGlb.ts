import { FileLoader, LoaderUtils } from 'three/webgpu';
import type { GLTFLoader, GLTF, GLTFLoaderPlugin } from 'three/addons/loaders/GLTFLoader.js';
import { PreparationBudget } from '../render/preparationBudget';
import { applyJsonRecords } from './glbRecords';
import type { decodeGlb } from './decodeGlb';
import { AssetCache, sharedAssetKey, sharedBuffers } from './sharedAssets';

type Decoded = ReturnType<typeof decodeGlb>;
let worker: Worker | undefined, sequence = 0;
const budget = new PreparationBudget();
// Cache immutable visual bytes, not mutable glTF objects or their render resources.
const visualBuffers = new AssetCache<ArrayBuffer>(128 * 1024 ** 2, buffer => buffer.byteLength);
const pending = new Map<number, { objects: Record<string, unknown>[]; resolve: (decoded: Decoded) => void; reject: (error: Error) => void }>();
function decode(buffer: ArrayBuffer): Promise<Decoded> {
  if (!worker) {
    worker = new Worker(new URL('./decodeGlb.worker.ts', import.meta.url), { type: 'module' });
    worker.onmessage = async ({ data }) => {
      const job = pending.get(data.id);
      if (!job) return;
      if (data.error) { pending.delete(data.id); job.reject(new Error(data.error)); return; }
      applyJsonRecords(job.objects, data.rows);
      if (data.done) { pending.delete(data.id); job.resolve({ json: job.objects[0], binary: data.binary }); }
      else {
        const nextFrame = budget.checkpoint();
        if (nextFrame) await nextFrame;
        worker?.postMessage({ id: data.id });
      }
    };
    worker.onerror = event => {
      for (const job of pending.values()) job.reject(new Error(event.message));
      pending.clear(); worker?.terminate(); worker = undefined;
    };
  }
  const id = ++sequence;
  return new Promise((resolve, reject) => { pending.set(id, { objects: [{}], resolve, reject }); worker!.postMessage({ id, buffer }, [buffer]); });
}

/** Feed decoded JSON and its immutable binary chunk to the same GLTF plugins without base64 or a second fetch. */
export function parseDecodedGlb(loader: GLTFLoader, decoded: Decoded, path: string): Promise<GLTF> {
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
    },
  });
  loader.register(plugin);
  try {
    // r186 accepts pre-parsed JSON; DefinitelyTyped exposes only its string/binary inputs.
    return (loader.parseAsync as unknown as (json: object, path: string) => Promise<GLTF>)(decoded.json, path);
  } finally { loader.unregister(plugin); }
}
export async function loadGlb(loader: GLTFLoader, url: string): Promise<GLTF> {
  const load = async () => await new FileLoader(loader.manager).setResponseType('arraybuffer').loadAsync(url) as ArrayBuffer;
  const key = /\/coarse\/t0_-?\d+_-?\d+\/vis\.([a-f0-9]{64})\.glb(?:[?#].*)?$/.exec(url)?.[1];
  const buffer = key ? await visualBuffers.get(key, load) : await load();
  // The worker owns and detaches its input; keep cached originals intact across revisits.
  return parseDecodedGlb(loader, await decode(key ? buffer.slice(0) : buffer), LoaderUtils.extractUrlBase(url));
}
