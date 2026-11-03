// Curated long-range destinations (landmarks.json): the OSM identities that the navigation build
// (pipeline/turku3d/coarse/navigation.py) turns into landmark pool addresses, each with that build's drop-off and
// city-wide route metres as `fallback` (`node game/scripts/landmark-fallbacks.mjs NAVIGATION.json`). This list decides
// which landmarks are offered: a served pool's landmark entries outside it are dropped, curated ones it lacks come from
// their fallback. A tile service without navigation metadata (the shared local farm) still offers every long job,
// independent of which street tiles are resident; drop-offs are ground-checked and routed as their tiles stream in.
import { VISUAL_L0 } from '../../core/origin';
import { addressSlug, type PoolAddress } from './addresses';
import data from './landmarks.json' with { type: 'json' };

export interface Landmark {
  name: string;
  source: [string, number];
  entrance?: [string, number];
  /** Published pool entry from the pizzeria: engine-xz drop-off and street points, route metres. */
  fallback?: { point: number[]; street: number[]; foot: number; car: number };
}

export const LANDMARKS: readonly Landmark[] = data as Landmark[];

/** The served pool with exactly the curated landmarks: a served landmark entry keeps its fresh drop-off and metres when
 *  it is curated and is dropped otherwise; a curated landmark the pool lacks gets its fallback entry. */
export function withLandmarks(pool: readonly PoolAddress[], landmarks: readonly Landmark[] = LANDMARKS): PoolAddress[] {
  const curated = new Set(landmarks.map(l => addressSlug(l.name)));
  const kept = pool.filter(a => !a.landmark || curated.has(a.id));
  const ids = new Set(kept.map(a => a.id));
  const extra = landmarks.flatMap(({ name, fallback: f }): PoolAddress[] => {
    const id = addressSlug(name);
    if (!f || ids.has(id)) return [];
    const [x, z] = f.point;
    return [{ id, address: name, tile: `t0_${Math.floor(x / VISUAL_L0)}_${Math.floor(z / VISUAL_L0)}`, point: { x, z },
      street: { x: f.street[0], z: f.street[1] }, foot: f.foot, car: f.car, landmark: true }];
  });
  return [...kept, ...extra];
}
