// A new tile release under the same /tiles URL (the localhost game's dev server, tools/dev/localhost/README.md),
// applied in place: the open tab keeps the player, the vehicles and the session. Collision tiles whose manifest
// entry changed are fetched again and replace their colliders in the worker; the visual tileset is swapped as a
// whole once the new one has settled (VisualTiles.reload); tiles the new release adds simply become loadable, and
// drafts of tiles it publishes leave once its tileset renders them (CoarseTiles.release). Draft collision entries
// (absolute /coarse/ URLs) are never removed here.
import { loadManifest, type Manifest, type ManifestEntry } from './manifest';
import type { CollisionStreamer } from './collisionStreaming';

interface ReleaseDelta {
  /** Visual tiles added or changed; any entry or a changed tileset reloads the visual tileset. */
  visual: string[];
  /** Collision tiles added or changed. */
  collision: string[];
  /** Collision tiles the new release no longer has. */
  removed: string[];
  tileset: boolean;
}

const changed = (old: Record<string, ManifestEntry>, fresh: Record<string, ManifestEntry>) =>
  Object.keys(fresh).filter(id => old[id]?.sha256 !== fresh[id].sha256).sort();

/** What differs between the loaded manifest and a new release's (drafts in `old.collision` are not compared). */
export function releaseDelta(old: Manifest, fresh: Manifest): ReleaseDelta {
  const published = Object.fromEntries(Object.entries(old.collision).filter(([, e]) => !/^[a-z]+:\/\//.test(e.url)));
  return {
    visual: changed(old.visual, fresh.visual),
    collision: changed(published, fresh.collision),
    removed: Object.keys(published).filter(id => !fresh.collision[id]).sort(),
    tileset: old.tileset?.sha256 !== fresh.tileset?.sha256,
  };
}

interface ReleaseHost {
  /** The live manifest object (CollisionStreamer, SafeReset and CoarseTiles hold it): updated in place. */
  manifest: Manifest;
  manifestUrl: string;
  tilesetUrl: string;
  tiles: { reload(url: string): Promise<void> };
  streamer: Pick<CollisionStreamer, 'refresh'>;
  unloadCollision: (id: string) => void;
  /** Draft tiles: those the new tileset renders leave. */
  coarse?: { release(published: Iterable<string>): void } | null;
  load?: (url: string) => Promise<Manifest>;
}

/** Applies the release now under `manifestUrl`; resolves with the delta once the visual tileset has swapped. */
export async function swapRelease(host: ReleaseHost): Promise<ReleaseDelta> {
  const fresh = await (host.load ?? (url => loadManifest(url, { cache: 'no-store' })))(host.manifestUrl);
  const { manifest } = host;
  const delta = releaseDelta(manifest, fresh);
  for (const id of delta.removed) { delete manifest.collision[id]; host.unloadCollision(id); }
  for (const id of delta.collision) manifest.collision[id] = fresh.collision[id];
  // Everything else follows the new release; the collision record (shared) and the base URL stay.
  Object.assign(manifest, { ...fresh, collision: manifest.collision, baseUrl: manifest.baseUrl });
  host.streamer.refresh(delta.collision);
  if (delta.visual.length || delta.tileset) await host.tiles.reload(host.tilesetUrl);
  host.coarse?.release(Object.keys(fresh.visual));
  return delta;
}
