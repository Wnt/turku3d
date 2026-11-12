import type { PreparedWorld } from '../world/preparedWorld';
import type { PreparedScene } from '../world/preparedScene';
import { patchScene, readAppearancePatch } from '../world/preparedAppearance';

type AppearanceReport = Awaited<ReturnType<PreparedWorld['applyAppearance']>>;

/** window.__dev.scene: appearance experiments in the running game (docs/DEVELOPING.md, "Appearance loop"). */
export interface SceneDev {
  /** The drawn scene's id and release (a live patch has a `patched:` release). */
  current(): { id: string; release: string } | null;
  /** A copy of one drawn material record, the starting point of a patch row: `{ material: { ...m, roughness: .6 } }`. */
  material(id: string): PreparedScene['materials'][number];
  /** Applies a `turku-scene-appearance/1` patch: materials, textures and asset models change in place. */
  patch(patch: unknown): Promise<AppearanceReport>;
  /** Re-reads the published scene after `python -m turku3d.scene.appearance` and applies its appearance. */
  reload(): Promise<AppearanceReport>;
  /** Back to the published appearance after live patches (nothing is fetched). */
  restore(): Promise<AppearanceReport>;
}

export function sceneDev(world: () => PreparedWorld | null): SceneDev {
  const active = () => {
    const current = world();
    if (!current) throw new Error('No prepared scene is loaded');
    return current;
  };
  return {
    current: () => { const current = world(); return current ? { id: current.data.id, release: current.data.release } : null; },
    material: id => {
      const found = active().data.materials.find(m => m.id === id);
      if (!found) throw new Error(`No material ${id} in the drawn scene`);
      return structuredClone(found);
    },
    patch: async patch => { const current = active(); return current.applyAppearance(patchScene(current.data, readAppearancePatch(patch))); },
    reload: () => active().reloadAppearance(),
    restore: () => active().restoreAppearance(),
  };
}
