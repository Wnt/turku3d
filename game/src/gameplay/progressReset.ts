// "Reset progress" (the pause menu, ui/menu.ts): the player starts over as a brand-new player.
//
// Cleared, for every city: the save slots and the autosave (core/save.ts, localStorage), the multiplayer mission
// progress (net/progress.ts), a trip in flight and the portal a stay started from (gameplay/travel.ts) and the dev
// server's resume marker (gameplay/hotState.ts). Money, permits, deliveries, missions, owned vehicles, the driven
// vehicle, the last position and the city all live in those. Every city room this tab played in resets the player too
// (net/client.ts markProgressReset, server PROTOCOL.md `reset`): score, seat, own vehicles, retained pose.
//
// Kept: settings (graphics quality, controls, audio volumes, HUD, time and developer panels, which have their own keys),
// the controls-help progress (how well the person knows the controls, not the game's progress), the developer editor's
// sessions and the multiplayer identity (the per-tab session token: player ID, nickname and spawn slot stay).
import { CITIES, cityKey } from '../core/city';
import { LEGACY_MISSION_KEY, SAVES_KEY, type KeyValueStore } from '../core/save';
import { markProgressReset } from '../net/client';
import { PROGRESS_KEY } from '../net/progress';
import { RESUME_KEY } from './hotState';
import { CAME_THROUGH_KEY, TICKET_KEY } from './travel';

/** Removes the stored progress of every city from `local` (localStorage) and `session` (sessionStorage) and marks the
 *  rooms of this tab's multiplayer sessions for a reset at their next hello. */
export function clearProgress(local: KeyValueStore | null, session: KeyValueStore | null) {
  const remove = (store: KeyValueStore | null, key: string) => { try { store?.removeItem(key); } catch { /* blocked */ } };
  for (const city of Object.values(CITIES)) {
    remove(local, cityKey(SAVES_KEY, city));
    remove(session, cityKey(PROGRESS_KEY, city));
  }
  remove(local, LEGACY_MISSION_KEY);
  for (const key of [TICKET_KEY, CAME_THROUGH_KEY, RESUME_KEY]) remove(session, key);
  markProgressReset(session);
}
