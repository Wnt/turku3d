import type { LocationWorld, RefreshReport } from '../world/location/locationWorld';
import type { LocationEntity, LocationManifest } from '../world/location/manifest';
import type * as THREE from 'three/webgpu';

/** window.__dev.world: the drawn location of the world pipeline (docs/design/world-pipeline.md, section 8). */
export interface WorldDev {
  /** Re-reads the manifest and redraws the entities whose content changed; resolves when they are drawn. */
  refresh(): Promise<RefreshReport>;
  manifest(): LocationManifest;
  entity(id: string): { row: LocationEntity; object: THREE.Object3D } | null;
  /** Drawn entities, triangles, instances and mesh bytes by kind. */
  stats(): ReturnType<LocationWorld['census']>;
  /** The last automatic refresh (the dev server announced a new manifest revision). */
  last: RefreshReport | null;
}

/** The dev hooks of a location; on a dev server the location also follows its manifest (`/world/events/NAME`). */
export function watchLocation(world: LocationWorld): WorldDev {
  const dev: WorldDev = { refresh: () => world.refresh(), manifest: () => world.manifest, entity: id => world.entity(id),
    stats: () => world.census(), last: null };
  if (import.meta.env.DEV) {
    const events = new EventSource(`world/events/${world.manifest.name}`);
    events.onmessage = message => {
      const { revision } = JSON.parse(message.data) as { revision: number };
      if (revision !== world.manifest.revision) void world.refresh().then(report => { dev.last = report; }, error => console.warn(`[world] refresh failed: ${error}`));
    };
  }
  return dev;
}
