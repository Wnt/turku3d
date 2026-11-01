// Per-tile street lights: the generator's light list (tileLights.ts: register street-light posts, lights hung over
// a carriageway, facade lights) plus the tunnel strips and bridge-deck lamps it builds as geometry (tileScan.ts).
// Only the tile's own data is read, so every client places identical lights whatever the load order. Model scans
// run in time slices on the main thread and touch only those light ranges and, for the lamp pools, the buildings.
import type * as THREE from 'three/webgpu';
import { decodeTileLights, lampKey, lightsSignature, owns, tileBounds, type TileLightsField } from './tileLights';
import { scanTile, type Footprint, type LightPoint } from './tileScan';

/** One light: a corona sprite, and for street-light posts the post below it. */
export interface Lamp {
  /** World position at 0.1 m; the identity used for buffer slots and fade continuity. */
  key: string;
  /** Post base (posts) or the light point itself. */
  x: number; y: number; z: number;
  yaw: number; scale: number; post: boolean;
  /** Street light (post or hung over the street), generator bridge or tunnel lamp, or a facade light. */
  kind: LampKind;
  light: SpriteLight;
}

export type LampKind = 'street' | 'bridge' | 'tunnel' | 'wall';

/** Corona record. Colour is linear HDR. `day` 1 keeps a light on in daylight (tunnels), 0 follows the night level. */
export interface SpriteLight { x: number; y: number; z: number; radius: number; r: number; g: number; b: number; day: number }

/** Lights the generator builds as geometry rather than listing them. */
const LIGHT_TYPES = {
  /** Bridge-deck lamps (OSM street_lamp on the deck), warm-white LED. */
  led: { radius: 1.2, color: [1.0, 0.8, 0.58], intensity: 5 },
  tunnel: { radius: 0.5, color: [1.0, 0.86, 0.55], intensity: 1.6 },
} as const;

function sprite(p: LightPoint, type: keyof typeof LIGHT_TYPES, day: number): SpriteLight {
  const t = LIGHT_TYPES[type];
  return { x: p.x, y: p.y, z: p.z, radius: t.radius, r: t.color[0] * t.intensity, g: t.color[1] * t.intensity, b: t.color[2] * t.intensity, day };
}

export interface LampSource { id: string; model: THREE.Object3D; lights: TileLightsField | null }

interface TileLights { lamps: Lamp[]; footprint: Footprint | null }
interface TileJob { model: THREE.Object3D; signature: string; job: Generator<void, TileLights> | null; lamps: Lamp[] | null }

/** Building footprints are kept this far around a tile (metres), so a building reaching over a seam still blocks. */
const FOOTPRINT_MARGIN = 16;

function* tileLamps(source: LampSource): Generator<void, TileLights> {
  const bounds = tileBounds(source.id);
  const listed = decodeTileLights(source.lights);
  const m = FOOTPRINT_MARGIN;
  const scan = yield* scanTile(source.model, undefined,
    bounds ? [bounds[0] - m, bounds[1] - m, bounds[2] + m, bounds[3] + m] : undefined);
  const built: Lamp[] = [];
  for (const p of scan.tunnel) if (!bounds || owns(bounds, p.x, p.z))
    built.push({ key: lampKey(p.x, p.z), x: p.x, y: p.y, z: p.z, yaw: 0, scale: 1, post: false, kind: 'tunnel', light: sprite(p, 'tunnel', 1) });
  for (const p of scan.bridge) if (!bounds || owns(bounds, p.x, p.z))
    built.push({ key: lampKey(p.x, p.z), x: p.x, y: p.y, z: p.z, yaw: 0, scale: 1, post: false, kind: 'bridge', light: sprite(p, 'led', 0) });
  return { lamps: [...listed, ...built], footprint: scan.footprint ?? null };
}

/** Resident tiles' lamps. `sync` follows the drawn drafts; `step` advances pending tiles within a time budget. */
export class LampField {
  private tiles = new Map<string, TileJob>();
  /** Called when a tile's lamps (and its building footprints) are ready, or with null when it left. */
  onTile: (id: string, lamps: Lamp[] | null, footprint?: Footprint | null) => void = () => {};

  sync(sources: Iterable<LampSource>) {
    const seen = new Set<string>();
    for (const source of sources) {
      seen.add(source.id);
      const tile = this.tiles.get(source.id);
      // CoarseTiles parses the tile's manifest again on every poll; only a changed model or light list is new work.
      const signature = lightsSignature(source.lights);
      if (tile && tile.model === source.model && tile.signature === signature) continue;
      this.tiles.set(source.id, { model: source.model, signature, job: tileLamps(source), lamps: tile?.lamps ?? null });
    }
    for (const id of [...this.tiles.keys()]) if (!seen.has(id)) { this.tiles.delete(id); this.onTile(id, null); }
  }

  /** Advance pending tiles until `budgetMs` has elapsed. Returns true while work remains. */
  step(budgetMs: number, now = () => performance.now()): boolean {
    const until = now() + budgetMs;
    for (const [id, tile] of this.tiles) {
      while (tile.job) {
        const next = tile.job.next();
        if (next.done) { tile.job = null; tile.lamps = next.value.lamps; this.onTile(id, next.value.lamps, next.value.footprint); }
        if (now() >= until) return this.pending();
      }
    }
    return false;
  }

  pending() { return [...this.tiles.values()].some(tile => tile.job); }
  lamps(id: string) { return this.tiles.get(id)?.lamps ?? null; }
  get size() { return this.tiles.size; }
}
