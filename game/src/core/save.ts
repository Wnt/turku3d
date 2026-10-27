// Save slots: three named slots and an autosave under one localStorage key. Each slot wraps the mission system's own
// save text (an opaque string, versioned and migrated by gameplay/missions) with the player's pose, the own car, the
// clock and the numbers the slot list shows. A mission save from before the slots (key turku3d.missions) becomes
// slot 1 on the first run.

export const SAVES_KEY = 'turku3d.saves';
/** The unslotted mission save of main before the save menu. */
export const LEGACY_MISSION_KEY = 'turku3d.missions';
export const SLOT_IDS = ['auto', 'slot1', 'slot2', 'slot3'] as const;
export type SlotId = typeof SLOT_IDS[number];
/** The slots the player saves into; the autosave is written only by the game. */
export const MANUAL_SLOTS: readonly SlotId[] = ['slot1', 'slot2', 'slot3'];
export const SLOT_NAMES: Record<SlotId, string> = { auto: 'Autosave', slot1: 'Slot 1', slot2: 'Slot 2', slot3: 'Slot 3' };

export interface KeyValueStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

/** EPSG:3877 E/N, N2000 height and the engine yaw (radians; the camera yaw on foot, the chassis yaw in a car). */
export interface SavedPose { E: number; N: number; H: number; yaw: number }

export interface SaveGame {
  version: 1;
  /** Unix milliseconds. */
  savedAt: number;
  label: string;
  /** `MissionSystem.serialize()` output. */
  mission: string;
  /** Null: the spawn point (a migrated mission save has no position). */
  player: SavedPose | null;
  /** The player's own car when one is owned, or null (the car then waits at its spawn parking). */
  vehicle: (SavedPose & { owned: boolean }) | null;
  /** ISO time of the sun clock, or null for the default time. */
  clock: { timeOfDay: string } | null;
  stats: { deliveries: number; money: number };
}

interface SaveFile { version: 1; slots: Partial<Record<SlotId, SaveGame>> }

/** Why the game autosaves. */
type AutosaveReason = 'delivery' | 'purchase' | 'pagehide' | 'quit';

const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const pose = (v: unknown): v is SavedPose => {
  const p = v as SavedPose | null;
  return !!p && finite(p.E) && finite(p.N) && finite(p.H) && finite(p.yaw);
};

function validGame(v: unknown): SaveGame | null {
  const g = v as Partial<SaveGame> | null;
  if (!g || g.version !== 1 || !finite(g.savedAt) || typeof g.label !== 'string' || typeof g.mission !== 'string') return null;
  if (g.player !== null && !pose(g.player)) return null;
  if (g.vehicle !== null && !(pose(g.vehicle) && typeof g.vehicle?.owned === 'boolean')) return null;
  if (g.clock !== null && !(typeof g.clock?.timeOfDay === 'string' && Number.isFinite(Date.parse(g.clock.timeOfDay)))) return null;
  if (!finite(g.stats?.deliveries) || !finite(g.stats?.money)) return null;
  return g as SaveGame;
}

/** Slot label: the last delivery's address and the clock time (Europe/Helsinki, 24 h). */
export function formatLabel(lastAddress: string | null, time: Date): string {
  const clock = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Helsinki', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  return `${lastAddress ?? 'Before the first delivery'} · ${clock.format(time)}`;
}

/** One line of slot details: deliveries, money and when it was saved (local date and time). */
export function formatDetails(game: SaveGame, locale?: string): string {
  const n = game.stats.deliveries;
  const when = new Date(game.savedAt).toLocaleString(locale ?? 'en-GB', { dateStyle: 'short', timeStyle: 'short' });
  return `${n} ${n === 1 ? 'delivery' : 'deliveries'} · ${game.stats.money} € · saved ${when}`;
}

export class SaveStore {
  private file: SaveFile;

  /**
   * `summarize` reads a legacy mission save (gameplay/missions `missionSummary`) for the migrated slot's details.
   * A null storage (blocked by the browser) keeps the slots in memory for this page only.
   */
  constructor(private readonly storage: KeyValueStore | null,
    summarize: (mission: string) => { deliveries: number; money: number; lastAddress: string | null }) {
    this.file = this.read();
    const legacy = this.storage?.getItem(LEGACY_MISSION_KEY) ?? null;
    if (legacy === null) return;
    if (!this.has('slot1')) {
      const s = summarize(legacy);
      // The old save had no position or clock: it continues from the spawn point at the default time.
      this.file.slots.slot1 = { version: 1, savedAt: Date.now(), label: formatLabel(s.lastAddress, new Date()),
        mission: legacy, player: null, vehicle: null, clock: null, stats: { deliveries: s.deliveries, money: s.money } };
      this.flush();
    }
    this.storage?.removeItem(LEGACY_MISSION_KEY);
  }

  get(id: SlotId): SaveGame | null { return this.file.slots[id] ?? null; }
  has(id: SlotId): boolean { return this.get(id) !== null; }
  /** Whether any slot or the autosave holds a game. */
  get any(): boolean { return SLOT_IDS.some(id => this.has(id)); }

  /** The most recently saved slot (ties: the autosave first, then by slot order), or null. */
  latest(): { id: SlotId; game: SaveGame } | null {
    let best: { id: SlotId; game: SaveGame } | null = null;
    for (const id of SLOT_IDS) {
      const game = this.get(id);
      if (game && (!best || game.savedAt > best.game.savedAt)) best = { id, game };
    }
    return best;
  }

  write(id: SlotId, game: SaveGame) {
    this.file.slots[id] = game;
    this.flush();
  }

  remove(id: SlotId) {
    delete this.file.slots[id];
    this.flush();
  }

  private read(): SaveFile {
    const file: SaveFile = { version: 1, slots: {} };
    try {
      const parsed = JSON.parse(this.storage?.getItem(SAVES_KEY) ?? 'null') as Partial<SaveFile> | null;
      if (parsed?.version !== 1 || typeof parsed.slots !== 'object' || !parsed.slots) return file;
      for (const id of SLOT_IDS) {
        const game = validGame((parsed.slots as Record<string, unknown>)[id]);
        if (game) file.slots[id] = game;
      }
    } catch {
      // Not JSON: start without saves rather than failing the game.
    }
    return file;
  }

  private flush() {
    try {
      this.storage?.setItem(SAVES_KEY, JSON.stringify(this.file));
    } catch {
      // Storage full or blocked (private mode): the slots stay in memory for this page.
    }
  }
}

/** Writes the autosave slot after every delivery and shop purchase, on quit and when the page is hidden. */
export class Autosave {
  /** The reason of the last autosave written, for tests and the debug state. */
  last: AutosaveReason | null = null;

  /** `capture` returns null while no game runs (the start screen): nothing is saved then. */
  constructor(private readonly store: SaveStore, private readonly capture: () => SaveGame | null) {}

  /** Writes the autosave now; returns whether a game was running to save. */
  save(reason: AutosaveReason): boolean {
    const game = this.capture();
    if (!game) return false;
    this.store.write('auto', game);
    this.last = reason;
    return true;
  }

  /** Hooks page hide (tab close, reload, app switch on a phone). Returns the unsubscribe. */
  attach(target: Pick<Window, 'addEventListener' | 'removeEventListener'>, doc: Pick<Document, 'visibilityState' | 'addEventListener' | 'removeEventListener'>) {
    const hide = () => { this.save('pagehide'); };
    const visibility = () => { if (doc.visibilityState === 'hidden') this.save('pagehide'); };
    target.addEventListener('pagehide', hide);
    doc.addEventListener('visibilitychange', visibility);
    return () => { target.removeEventListener('pagehide', hide); doc.removeEventListener('visibilitychange', visibility); };
  }
}
