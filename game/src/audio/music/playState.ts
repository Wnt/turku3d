import type { MusicState } from './index';

/** The play session's visible context and order determine the music variant. */
export function musicScene(start: boolean, paused: boolean, carrying: boolean): MusicState {
  return start || paused ? 'menu' : carrying ? 'mission' : 'explore';
}

export function musicMuffle(intro: boolean, shop: boolean, vehicle: boolean): number {
  return intro || shop ? 1 : vehicle ? 0.6 : 0;
}

export function deadlineProgress(elapsed: number, par: number): number {
  return par > 0 ? Math.max(0, Math.min(1, elapsed / par)) : 0;
}
