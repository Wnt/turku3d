// The KTX2 textures embedded in public/assets/nature/nature.glb, transcoded per mip to RGBA8 with three's basis
// transcoder (Node: the CommonJS build three ships inside its ESM package).
import { expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

export interface Mip { width: number; height: number; rgba: Uint8Array }
type File = { isValid(): boolean; startTranscoding(): boolean; getLevels(): number; getImageLevelInfo(l: number, a: number, f: number): { origWidth: number; origHeight: number };
  getImageTranscodedSizeInBytes(l: number, a: number, f: number, t: number): number;
  transcodeImage(dst: Uint8Array, l: number, a: number, f: number, t: number, g: number, c: number, s: number): boolean; close(): void; delete(): void };

/** The nature.glb glTF JSON and binary chunk. */
export function natureGlb() {
  const glb = readFileSync(new URL('../../public/assets/nature/nature.glb', import.meta.url));
  const jsonLength = glb.readUInt32LE(12);
  return { json: JSON.parse(glb.subarray(20, 20 + jsonLength).toString('utf8')), bin: glb.subarray(20 + jsonLength + 8) };
}

/** Mips 0 to `levels - 1` of a nature.glb material's base colour or normal texture as RGBA8. */
export async function natureTextureMips(materialName: string, slot: 'baseColor' | 'normal', levels = 8): Promise<Mip[]> {
  const { json, bin } = natureGlb();
  const material = json.materials.find((m: { name: string }) => m.name === materialName);
  const ref = slot === 'baseColor' ? material.pbrMetallicRoughness.baseColorTexture : material.normalTexture;
  const texture = json.textures[ref.index];
  const image = json.images[texture.extensions.KHR_texture_basisu.source], view = json.bufferViews[image.bufferView];
  const ktx = new Uint8Array(bin.buffer, bin.byteOffset + (view.byteOffset ?? 0), view.byteLength);
  const script = new URL('../../node_modules/three/examples/jsm/libs/basis/basis_transcoder.js', import.meta.url);
  const source = readFileSync(script, 'utf8'), filename = fileURLToPath(script);
  const module = { exports: {} as unknown };
  new Function('module', 'exports', 'require', '__filename', '__dirname', source)(module, module.exports, createRequire(script),
    filename, filename.slice(0, filename.lastIndexOf('/')));
  const basis = await (module.exports as (o: object) => Promise<{ initializeBasis(): void; KTX2File: new (d: Uint8Array) => File }>)(
    { wasmBinary: readFileSync(new URL('../../node_modules/three/examples/jsm/libs/basis/basis_transcoder.wasm', import.meta.url)) });
  basis.initializeBasis();
  const file = new basis.KTX2File(ktx);
  expect(!!file.isValid() && !!file.startTranscoding()).toBe(true);
  const mips: Mip[] = [];
  for (let level = 0; level < Math.min(levels, file.getLevels()); level++) {
    const info = file.getImageLevelInfo(level, 0, 0);
    const rgba = new Uint8Array(file.getImageTranscodedSizeInBytes(level, 0, 0, 13));
    file.transcodeImage(rgba, level, 0, 0, 13, 0, -1, -1);
    mips.push({ width: info.origWidth, height: info.origHeight, rgba });
  }
  file.close(); file.delete();
  return mips;
}
