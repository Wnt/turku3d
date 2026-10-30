// Play state carried across a code landing on the localhost game (Vite dev server only; tools/dev/localhost/README.md).
// A hot swap of the play session (play.ts, accepted by main.ts) hands the old session's HotPlay to the new one through
// `import.meta.hot.data`; the physics worker, tiles and renderer stay alive, so the character and every vehicle keep
// their place and only the session's own state is restored here. A change that cannot swap (main.ts, world, render
// or physics modules, the save format) reloads the page instead: the autosave slot is written first and the resume
// marker makes the next load continue from it without the start menu.
import type { KeyValueStore, SaveGame } from '../core/save';
import type { GameAudio } from '../audio';

/** sessionStorage key: the next page load continues from the autosave slot. */
export const RESUME_KEY = 'turku3d.resume';

export interface HotPlay {
  /** Live audio graph and music clock survive a play-session module swap. */
  audio?: GameAudio;
  /** The running game as a save slot (`SaveGameController.capture()`): missions, money, permit, pose, own vehicle,
   *  clock. Null while the start menu was open: the new session shows the menu again. */
  save: SaveGame | null;
  mode: 'foot' | 'car';
  /** The driven vehicle's physics id in a car. */
  drivenId: string | null;
  camera: { yaw: number; pitch: number; distance: number };
  hudVisible: boolean;
  /** Reconnect the live room after the session swaps; the world keeps its actor placements. */
  multiplayer?: boolean;
}

/** What a new session restores from a HotPlay. */
interface HotTarget {
  missions: { restore(text: string | null): void; begin(): void };
  setClock(date: Date): void;
  camera: { yaw: number; pitch: number; distance: number };
}

/** Restores missions (money, permit, order, vehicle ownership), the clock and the camera; returns the player mode. */
export function applyHot(hot: HotPlay, target: HotTarget): { mode: 'foot' | 'car'; drivenId: string | null } {
  if (hot.save) {
    target.missions.restore(hot.save.mission);
    target.missions.begin();
    if (hot.save.clock) target.setClock(new Date(hot.save.clock.timeOfDay));
  }
  Object.assign(target.camera, hot.camera);
  return hot.mode === 'car' && hot.drivenId ? { mode: 'car', drivenId: hot.drivenId } : { mode: 'foot', drivenId: null };
}

/** Marks that the next page load continues from the autosave slot. */
export function markResume(storage: Pick<KeyValueStore, 'setItem'> | null) {
  try { storage?.setItem(RESUME_KEY, '1'); } catch { /* blocked storage: the start menu shows after the reload */ }
}

/** True once after `markResume`: the marker is consumed. */
export function takeResume(storage: Pick<KeyValueStore, 'getItem' | 'removeItem'> | null): boolean {
  try {
    const marked = storage?.getItem(RESUME_KEY) === '1';
    if (marked) storage?.removeItem(RESUME_KEY);
    return marked;
  } catch { return false; }
}

/** A running play session (play.ts startPlay). */
export interface PlaySession {
  /** Stops the session and returns what the next one continues from. */
  dispose(): HotPlay;
  /** Autosaves and reloads the page, which continues from the autosave (dev server only). */
  reload(): void;
}

/** Release only the old session's actors and UI; the persistent world belongs to main.ts. */
export function disposePlayScene(root: import('three').Object3D, body: Set<Element>) {
  root.removeFromParent();
  root.traverse(object => {
    const mesh = object as import('three').Mesh;
    if (!mesh.isMesh) return;
    mesh.geometry.dispose();
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) material.dispose();
  });
  for (const element of [...document.body.children]) if (!body.has(element)) element.remove();
}
