// dist/tiles/manifest.json (docs/specs/tiles.md §5).
import { E0, N0, COLLISION_TILE, VISUAL_L0 } from '../core/origin';

export interface ManifestEntry { url: string; sha256: string; bytes: number; tris: number; h: [number, number]; prims?: number; flags?: number; level?: number }
export interface Manifest {
  format: string; version: number; built: string; code: string;
  origin_en: [number, number, number]; crs: string;
  grid: { visual_l0: number; collision: number; levels: number };
  materials_version: number; col_version: [number, number];
  visual: Record<string, ManifestEntry>; collision: Record<string, ManifestEntry>;
  spawn?: { position: [number, number, number]; headingDeg: number; vehicle: { position: [number, number, number]; yaw: number } };
  baseUrl: string;
}

export async function loadManifest(url: string): Promise<Manifest> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`manifest ${url}: HTTP ${res.status}`);
  const m = (await res.json()) as Manifest;
  if (m.format !== 'turku3d-manifest' || m.version !== 1) throw new Error(`manifest ${url}: unsupported format ${m.format} v${m.version}`);
  if (m.origin_en[0] !== E0 || m.origin_en[1] !== N0) throw new Error(`manifest origin ${m.origin_en} != engine origin ${E0},${N0}`);
  if (m.grid.collision !== COLLISION_TILE || m.grid.visual_l0 !== VISUAL_L0) throw new Error(`manifest grid ${JSON.stringify(m.grid)} != engine grid`);
  if (m.col_version[0] !== 1) throw new Error(`manifest col_version ${m.col_version} unsupported`);
  m.baseUrl = new URL('.', new URL(url, location.href)).href;
  return m;
}
