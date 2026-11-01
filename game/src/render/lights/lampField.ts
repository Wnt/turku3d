// Per-tile street lights: rule lamps (lampRule.ts) placed against the tile's surfaces (tileScan.ts), plus the
// tunnel and bridge lamps its generator already built. Tiles are processed in time slices on the main thread.
import type * as THREE from 'three/webgpu';
import type { RoadGraphJson } from '../../gameplay/roadGraph';
import { LAMP_RULE, kerbPoint, lampStations, owns, positionHash, tileBounds, type Bounds, type Station } from './lampRule';
import { scanTile, type LightPoint, type ScanResult } from './tileScan';
import { POST_SHAPE } from './lampPosts';

/** One light: a corona sprite, and for rule lamps a post below it. */
export interface Lamp {
  /** World position at 0.1 m; the identity used for buffer slots and fade continuity. */
  key: string;
  /** Post base (rule lamps) or the light point itself. */
  x: number; y: number; z: number;
  yaw: number; scale: number; post: boolean;
  light: SpriteLight;
}

/** Corona record. Colour is linear HDR. `day` 1 keeps a light on in daylight (tunnels), 0 follows the night level. */
export interface SpriteLight { x: number; y: number; z: number; radius: number; r: number; g: number; b: number; day: number }

export const LIGHT_TYPES = {
  /** Warm-white LED, the common Turku street light today. */
  led: { radius: 0.85, color: [1.0, 0.86, 0.68], intensity: 3.2 },
  /** Older high-pressure sodium heads. */
  sodium: { radius: 0.85, color: [1.0, 0.62, 0.28], intensity: 3.0 },
  tunnel: { radius: 0.5, color: [1.0, 0.86, 0.55], intensity: 1.6 },
} as const;
/** Share of rule lamps drawn as sodium, chosen by position hash. */
const SODIUM_SHARE = 0.2;
/** A bridge deck within this distance of a generator-built bridge lamp is already lit. */
const LIT_BRIDGE = 30;
const POST_SCALE = [0, 0.75, 1, 1.15];

function sprite(p: LightPoint, type: keyof typeof LIGHT_TYPES, day: number): SpriteLight {
  const t = LIGHT_TYPES[type];
  return { x: p.x, y: p.y, z: p.z, radius: t.radius, r: t.color[0] * t.intensity, g: t.color[1] * t.intensity, b: t.color[2] * t.intensity, day };
}

const lampKey = (x: number, z: number) => `${Math.round(x * 10)},${Math.round(z * 10)}`;

interface Candidate { side: 1 | -1; retreat: number; probe: number }

/** Kerb points to test for each station, in preference order (preferred side first, then retreat toward the centre). */
export function lampProbes(stations: readonly Station[]) {
  const points: { x: number; z: number }[] = [];
  const candidates: Candidate[][] = stations.map(station => {
    const list: Candidate[] = [];
    for (const side of station.sides) for (const retreat of LAMP_RULE.retreat) {
      const point = kerbPoint(station, side, retreat);
      if (retreat > 0 && point.offset <= LAMP_RULE.minOffset && list.some(c => c.side === side && c.retreat > 0)) continue;
      list.push({ side, retreat, probe: points.length });
      points.push(point);
    }
    return list;
  });
  return { points, candidates };
}

/** Surface height of a valid kerb point, or null when it is blocked. */
function standHeight(station: Station, probe: ScanResult['probes'][number]): number | null {
  if (probe.building || probe.tunnel) return null;
  if (station.deckY !== null) {
    let best: number | null = null;
    for (const y of probe.decks) if (y > station.deckY - 1.5 && y < station.deckY + 1 && (best === null || y > best)) best = y;
    return best;
  }
  if (probe.ground === -Infinity || probe.water > probe.ground + 0.05) return null;
  // Under a bridge deck: a post would pierce it.
  if (probe.decks.some(y => y > probe.ground + 1.5 && y < probe.ground + 20)) return null;
  return probe.ground;
}

/** Final lamps of one tile; deterministic for a given roads.json and model. */
export function placeLamps(stations: readonly Station[], candidates: readonly Candidate[][], scan: ScanResult, bounds: Bounds): Lamp[] {
  const lamps: Lamp[] = [];
  const bridge = scan.bridge.filter(p => owns(bounds, p.x, p.z));
  for (const p of scan.tunnel) if (owns(bounds, p.x, p.z))
    lamps.push({ key: lampKey(p.x, p.z), x: p.x, y: p.y, z: p.z, yaw: 0, scale: 1, post: false, light: sprite(p, 'tunnel', 1) });
  for (const p of bridge)
    lamps.push({ key: lampKey(p.x, p.z), x: p.x, y: p.y, z: p.z, yaw: 0, scale: 1, post: false, light: sprite(p, 'led', 0) });
  const placed: Lamp[] = [];
  const order = stations.map((_, i) => i).sort((a, b) => stations[b].cls - stations[a].cls || a - b);
  for (const i of order) {
    const station = stations[i];
    if (!owns(bounds, station.x, station.z)) continue;
    if (station.deckY !== null && bridge.some(p => Math.hypot(p.x - station.x, p.z - station.z) < LIT_BRIDGE)) continue;
    for (const c of candidates[i]) {
      const y = standHeight(station, scan.probes[c.probe]);
      if (y === null) continue;
      const { x, z } = kerbPoint(station, c.side, c.retreat);
      if (placed.some(l => Math.hypot(l.x - x, l.z - z) < LAMP_RULE.minSeparation)) break;
      // The arm reaches back toward the centreline: local +X maps to (cos yaw, -sin yaw).
      const ax = station.tz * c.side, az = -station.tx * c.side;
      const yaw = Math.atan2(-az, ax), scale = POST_SCALE[station.cls];
      const head = { x: x + ax * POST_SHAPE.arm * scale, y: y + POST_SHAPE.light * scale, z: z + az * POST_SHAPE.arm * scale };
      const type = positionHash(x, z) / 0x100000000 < SODIUM_SHARE ? 'sodium' : 'led';
      const lamp: Lamp = { key: lampKey(x, z), x, y, z, yaw, scale, post: true, light: sprite(head, type, 0) };
      placed.push(lamp);
      break;
    }
  }
  return lamps.concat(placed);
}

export interface LampSource { id: string; model: THREE.Object3D; roads: RoadGraphJson | null }

interface TileJob { model: THREE.Object3D; roads: RoadGraphJson | null; job: Generator<void, Lamp[]> | null; lamps: Lamp[] | null }

function* tileLamps(source: LampSource): Generator<void, Lamp[]> {
  const bounds = tileBounds(source.id) ?? source.roads?.bbox;
  if (!bounds) return [];
  const stations = source.roads ? lampStations(source.roads, bounds) : [];
  const { points, candidates } = lampProbes(stations);
  const scan = yield* scanTile(source.model, points);
  return placeLamps(stations, candidates, scan, bounds);
}

/** Resident tiles' lamps. `sync` follows the drawn drafts; `step` advances pending tiles within a time budget. */
export class LampField {
  private tiles = new Map<string, TileJob>();
  /** Called when a tile's lamps are ready (or null when it left). */
  onTile: (id: string, lamps: Lamp[] | null) => void = () => {};

  sync(sources: Iterable<LampSource>) {
    const seen = new Set<string>();
    for (const source of sources) {
      seen.add(source.id);
      const tile = this.tiles.get(source.id);
      if (tile && tile.model === source.model && tile.roads === source.roads) continue;
      this.tiles.set(source.id, { model: source.model, roads: source.roads, job: tileLamps(source), lamps: tile?.lamps ?? null });
    }
    for (const id of [...this.tiles.keys()]) if (!seen.has(id)) { this.tiles.delete(id); this.onTile(id, null); }
  }

  /** Advance pending tiles until `budgetMs` has elapsed. Returns true while work remains. */
  step(budgetMs: number, now = () => performance.now()): boolean {
    const until = now() + budgetMs;
    for (const [id, tile] of this.tiles) {
      while (tile.job) {
        const next = tile.job.next();
        if (next.done) { tile.job = null; tile.lamps = next.value; this.onTile(id, next.value); }
        if (now() >= until) return this.pending();
      }
    }
    return false;
  }

  pending() { return [...this.tiles.values()].some(tile => tile.job); }
  lamps(id: string) { return this.tiles.get(id)?.lamps ?? null; }
  get size() { return this.tiles.size; }
}
