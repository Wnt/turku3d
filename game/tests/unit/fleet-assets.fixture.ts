import { beforeAll, afterAll, vi } from 'vitest';
import { readFile } from 'node:fs/promises';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

// Unit tests load the shipped GLBs through the real parser, with local-file transport.
const original = GLTFLoader.prototype.loadAsync;
let restore: (() => void) | undefined;
beforeAll(() => {
  // Fleet bakes run in the streaming window (actors/fleet/authored.ts), which waits for frames.
  if (typeof requestAnimationFrame !== 'function') vi.stubGlobal('requestAnimationFrame', (frame: FrameRequestCallback) => setTimeout(() => frame(performance.now()), 0));
  const spy = vi.spyOn(GLTFLoader.prototype, 'loadAsync').mockImplementation(async function (this: GLTFLoader, url, progress) {
    if (!url.startsWith('/assets/cars/')) return original.call(this, url, progress);
    const bytes = await readFile(new URL(`../../public${url}`, import.meta.url));
    return this.parseAsync(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength), '');
  });
  restore = () => spy.mockRestore();
});
afterAll(() => { restore?.(); vi.unstubAllGlobals(); });
