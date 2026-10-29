import { FileLoader, LoaderUtils } from 'three/webgpu';
import type { GLTFLoader, GLTF, GLTFLoaderPlugin } from 'three/addons/loaders/GLTFLoader.js';
import type { decodeGlb } from './decodeGlb';

type Decoded = ReturnType<typeof decodeGlb>;
let worker: Worker | undefined, sequence = 0;
const pending = new Map<number, { resolve: (decoded: Decoded) => void; reject: (error: Error) => void }>();
function decode(buffer: ArrayBuffer): Promise<Decoded> {
  if (!worker) {
    worker = new Worker(new URL('./decodeGlb.worker.ts', import.meta.url), { type: 'module' });
    worker.onmessage = ({ data }) => {
      const job = pending.get(data.id); pending.delete(data.id);
      if (data.error) job?.reject(new Error(data.error)); else job?.resolve(data);
    };
    worker.onerror = event => {
      for (const job of pending.values()) job.reject(new Error(event.message));
      pending.clear(); worker?.terminate(); worker = undefined;
    };
  }
  const id = ++sequence;
  return new Promise((resolve, reject) => { pending.set(id, { resolve, reject }); worker!.postMessage({ id, buffer }, [buffer]); });
}

/** Feed decoded JSON and its immutable binary chunk to the same GLTF plugins without base64 or a second fetch. */
export function parseDecodedGlb(loader: GLTFLoader, decoded: Decoded, path: string): Promise<GLTF> {
  const plugin = (parser: Parameters<Parameters<GLTFLoader['register']>[0]>[0]): GLTFLoaderPlugin => ({
    name: 'TURKU_decoded_glb', beforeRoot: async () => {
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
  const buffer = await new FileLoader(loader.manager).setResponseType('arraybuffer').loadAsync(url) as ArrayBuffer;
  return parseDecodedGlb(loader, await decode(buffer), LoaderUtils.extractUrlBase(url));
}
