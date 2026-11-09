// The autopilot in the play session (play.ts): the lane router fed by the resident draft tiles' road graphs (the
// same data and hash the ambient traffic uses, traffic/gate.ts) and, for tiles not resident yet, the tile's own
// roads.json through the coarse service. Exposed as `__game.autopilot` and the `?autopilot=` URL trigger.
import { draftsUrl } from '../../net/config';
import { roadHash } from '../../traffic/gate';
import { readStreamingJson } from '../../world/streamingJson';
import type { CoarseTiles } from '../../world/coarseTiles';
import type { RoadGraphJson } from '../roadGraph';
import { Autopilot, type AutopilotWorld } from './autopilot';
import { LaneRouter, type TileRoads } from './lanes';

export { Autopilot } from './autopilot';
export type { AutopilotHandle, GoToOptions, AutopilotResult } from './autopilot';
export { vehiclePose } from './autopilot';
export type { Obstacle } from './follow';
export { LaneRouter } from './lanes';

/** One tile's road graph: the resident entry's (what traffic holds), else its manifest's roads.json. */
export function coarseRoads(coarse: CoarseTiles | null): (tile: string) => Promise<TileRoads | null> {
  const cache = new Map<string, Promise<TileRoads | null>>();
  return tile => {
    const e = coarse?.entries.get(tile);
    if (e?.state === 'draft' && e.roads) return Promise.resolve({ tile, json: e.roads, hash: roadHash(e.roads) });
    let job = cache.get(tile);
    if (!job) {
      job = (async () => {
        const endpoint = draftsUrl();
        if (!endpoint) return null;
        const base = new URL(`${tile}/`, new URL(endpoint, location.href));
        const manifest = await fetch(new URL('manifest.json', base)).then(r => r.ok ? r.json() : null).catch(() => null);
        const url = manifest?.roads?.url;
        if (!url) return null;
        const json = await fetch(new URL(url, base)).then(r => r.ok ? readStreamingJson<RoadGraphJson>(r) : null).catch(() => null);
        return json ? { tile, json, hash: roadHash(json) } : null;
      })();
      cache.set(tile, job);
      // A failed fetch may be retried by a later route.
      void job.then(r => { if (!r) cache.delete(tile); });
    }
    return job;
  };
}

/** Parses `?autopilot=x,z[,walk|drive][,speed km/h]` (engine metres) for a scripted start. */
export function autopilotParam(search = globalThis.location?.search ?? ''): { x: number; z: number; mode?: 'walk' | 'drive'; speed?: number } | null {
  const raw = new URLSearchParams(search).get('autopilot');
  if (!raw) return null;
  const [x, z, mode, kmh] = raw.split(',');
  if (!Number.isFinite(+x) || !Number.isFinite(+z)) return null;
  return { x: +x, z: +z, ...(mode === 'walk' || mode === 'drive' ? { mode } : {}), ...(kmh && Number.isFinite(+kmh) ? { speed: +kmh / 3.6 } : {}) };
}

export function createAutopilot(world: Omit<AutopilotWorld, 'lanes'>, coarse: CoarseTiles | null): Autopilot {
  return new Autopilot({ ...world, lanes: new LaneRouter(coarseRoads(coarse)) });
}
