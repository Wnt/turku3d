// Mission progress follows the same per-tab, server-issued token as multiplayer identity.
// It is independent of solo save slots and never overrides server-owned pose/vehicles.
import type { KeyValueStore } from '../core/save';

const KEY = 'turku3d.mp.progress.v1';
const TOKEN = /^[a-f0-9]{64}$/;

export class MultiplayerProgress {
  constructor(private readonly storage: KeyValueStore | null) {}

  load(session: string | null): string | null {
    if (!session || !TOKEN.test(session)) return null;
    try {
      const saved = JSON.parse(this.storage?.getItem(KEY) ?? 'null') as { session?: unknown; mission?: unknown } | null;
      return saved?.session === session && typeof saved.mission === 'string' ? saved.mission : null;
    } catch { return null; }
  }

  save(session: string | null, mission: string) {
    if (!session || !TOKEN.test(session)) return;
    try { this.storage?.setItem(KEY, JSON.stringify({ session, mission })); }
    catch { /* Blocked session storage: same limitation as the multiplayer session token. */ }
  }

  /** Timed-round resets must not resurrect progress from the previous round. */
  clear(session: string | null) {
    if (!session || this.load(session) === null) return;
    try { this.storage?.removeItem(KEY); } catch { /* Storage may be blocked. */ }
  }
}
