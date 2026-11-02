import { TextureLoader, SRGBColorSpace, type Texture } from 'three/webgpu';
// Decoder for public/assets/walkers/walkers.bin (written by scripts/bake-walkers.mjs; layout documented there).

/** Body regions the renderer tints per instance, in the bake's order. */
export const REGIONS = ['skin', 'top', 'bottom', 'shoes', 'hair', 'inner', 'legwear', 'eyes'] as const;

interface WalkerClip { row: number; frames: number }
export interface WalkerVariant {
  id: string;
  vertices: number;
  /** First VAT row of this variant; clip rows are relative to it. */
  rowBase: number;
  walk: WalkerClip; idle: WalkerClip;
  height: number;
  /** Ground distance of one walk loop at the authored cadence (feet do not slide at this ratio). */
  metresPerLoop: number;
  indices: Uint16Array;
  farIndices: Uint16Array;
  uv: Float32Array;
  regions: Uint8Array;
}
export interface WalkerAsset {
  fps: number;
  atlas?: string;
  albedo?: Texture;
  /** RGBA half floats: xyz model-space position, w spherical Fibonacci normal index. */
  vat: { width: number; height: number; data: Uint16Array; directions: number };
  variants: WalkerVariant[];
}

interface Section { offset: number; length: number }
interface Meta {
  format: string; version: number; fps: number; atlas?: string;
  vat: Section & { width: number; height: number; format: string; normal: { encoding: string; directions: number } };
  regions: string[];
  variants: { id: string; vertices: number; rowBase: number; height: number; metresPerLoop: number;
    clips: { id: string; row: number; frames: number }[]; indices: Section; farIndices?: Section; uv?: Section; regionsOffset: Section }[];
}

export function parseWalkerAsset(buffer: ArrayBuffer): WalkerAsset {
  const head = new Uint32Array(buffer, 0, 3);
  if (head[0] !== 0x4b4c5754 || head[1] !== 2) throw new Error('walkers.bin: not a turku3d walker asset v2');
  const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 12, head[2]))) as Meta;
  if (meta.format !== 'turku3d-walkers' || meta.vat.format !== 'rgba16float' || meta.vat.normal.encoding !== 'spherical-fibonacci'
    || meta.regions.join() !== REGIONS.join()) throw new Error(`walkers.bin: unsupported ${meta.format} layout`);
  const base = 12 + head[2];
  const clip = (v: Meta['variants'][number], id: string) => {
    const c = v.clips.find(c => c.id === id);
    if (!c) throw new Error(`walkers.bin: ${v.id} has no ${id} clip`);
    return { row: c.row, frames: c.frames };
  };
  return {
    fps: meta.fps, atlas: meta.atlas,
    vat: { width: meta.vat.width, height: meta.vat.height, directions: meta.vat.normal.directions,
      data: new Uint16Array(buffer, base + meta.vat.offset, meta.vat.length) },
    variants: meta.variants.map(v => ({
      id: v.id, vertices: v.vertices, rowBase: v.rowBase, walk: clip(v, 'walk'), idle: clip(v, 'idle'),
      height: v.height, metresPerLoop: v.metresPerLoop,
      indices: new Uint16Array(buffer, base + v.indices.offset, v.indices.length),
      farIndices: new Uint16Array(buffer, base + (v.farIndices ?? v.indices).offset, (v.farIndices ?? v.indices).length),
      uv: v.uv ? new Float32Array(buffer, base + v.uv.offset, v.uv.length) : new Float32Array(v.vertices * 2),
      regions: new Uint8Array(buffer, base + v.regionsOffset.offset, v.regionsOffset.length),
    })),
  };
}

export async function loadWalkerAsset(url: string): Promise<WalkerAsset> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`walkers ${url}: HTTP ${res.status}`);
  const asset = parseWalkerAsset(await res.arrayBuffer());
  if (asset.atlas) {
    asset.albedo = await new TextureLoader().loadAsync(new URL(asset.atlas, new URL(url, location.href)).href);
    asset.albedo.colorSpace = SRGBColorSpace;
    asset.albedo.flipY = false; // glTF UVs are already top-down, as is our atlas packer.
    asset.albedo.anisotropy = 4;
  }
  return asset;
}

/** f32 constant shared with the bake and the shader: 2 - golden ratio. */
export const GOLDEN_FRACTION = Math.fround(0.3819660112501051);
/** Unit normal of spherical Fibonacci direction `index` (the bake's encoding). */
export function fibonacciDirection(index: number, directions: number): [number, number, number] {
  const z = 1 - (2 * index + 1) / directions, r = Math.sqrt(Math.max(0, 1 - z * z));
  const phi = 2 * Math.PI * ((index * GOLDEN_FRACTION) % 1);
  return [Math.cos(phi) * r, Math.sin(phi) * r, z];
}
