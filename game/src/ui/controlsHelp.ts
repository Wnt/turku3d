import type { Action, Input } from '../input';
import { helpHidden, recordHelp, type HelpProgress } from '../core/helpLearning';

/** Device-specific prompts for the current control mode. */
export function controlsHelp(input: Input, mode: 'foot' | 'car', flying: boolean, paused: boolean, lost: boolean) {
  return paused ? `${input.prompt('pause')} Resume`
      : lost ? `${input.prompt('respawn')} return to solid ground · ${input.prompt('map')} map · ${input.prompt('pause')} pause`
      : mode === 'foot' && flying
      ? `Flying · ${input.prompt('moveY')} move · ${input.prompt('flyUp')} up · ${input.prompt('flyDown')} down · ${input.prompt('sprint')} fast · ${input.prompt('jump')} ${input.prompt('jump')} land · ${input.prompt('pause')} pause`
      : mode === 'foot'
      ? `${input.prompt('moveY')} move · ${input.source === 'gamepad' ? 'Ⓡ' : 'mouse'} look · ${input.prompt('sprint')} sprint · ${input.prompt('jump')} jump · ${input.prompt('interact')} enter vehicle · ${input.prompt('cameraReset')} camera · ${input.prompt('map')} map · ${input.prompt('pause')} pause`
      : `${input.prompt('throttle')} throttle · ${input.prompt('brake')} brake/reverse · ${input.prompt('steer')} steer · ${input.prompt('handbrake')} handbrake · ${input.prompt('interact')} exit · ${input.prompt('cameraReset')} camera · ${input.prompt('map')} map · ${input.prompt('pause')} pause`;
}

const GROUPS = {
  foot: { move: ['moveX', 'moveY'], look: ['lookX', 'lookY'], sprint: ['sprint'], jump: ['jump'],
    interact: ['interact'], camera: ['cameraReset'], map: ['map'], pause: ['pause'] },
  fly: { move: ['moveX', 'moveY'], up: ['flyUp'], down: ['flyDown'], fast: ['sprint'], land: ['jump'], pause: ['pause'] },
  car: { throttle: ['throttle'], brake: ['brake'], steer: ['steer'], handbrake: ['handbrake'],
    interact: ['interact'], camera: ['cameraReset'], map: ['map'], pause: ['pause'] },
} satisfies Record<string, Record<string, Action[]>>;
type HelpMode = keyof typeof GROUPS;
const STORAGE_KEY = 'turku3d.controls-help.v1';

/** Per-mode browser memory. Menus/paused time do not spend the minute; held buttons do not count as new uses. */
export class ControlsHelp {
  private progress: Partial<Record<HelpMode, HelpProgress>> = {};
  private last: { time: number; mode: HelpMode } | null = null;
  private stored = '';
  constructor() {
    try {
      const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}');
      for (const mode of Object.keys(GROUPS) as HelpMode[]) {
        const row = parsed[mode];
        if (!row || typeof row.elapsedMs !== 'number' || !Number.isFinite(row.elapsedMs) || row.elapsedMs < 0) continue;
        const counts: Record<string, number> = {};
        for (const key of Object.keys(GROUPS[mode])) if (typeof row.counts?.[key] === 'number') counts[key] = Math.max(0, Math.min(2, row.counts[key]));
        this.progress[mode] = { elapsedMs: Math.min(60_000, row.elapsedMs), counts };
      }
    } catch { /* Private browsing or unavailable storage: retain memory for this session. */ }
  }

  update(input: Input, mode: 'foot' | 'car', flying: boolean, active: boolean, now: number) {
    const current: HelpMode = mode === 'car' ? 'car' : flying ? 'fly' : 'foot';
    const groups: Record<string, Action[]> = GROUPS[current];
    const row = this.progress[current] ?? { counts: {}, elapsedMs: 0 };
    const hidden = helpHidden(row, Object.keys(groups));
    if (active && !hidden) {
      const elapsed = this.last?.mode === current ? now - this.last.time : 0;
      const pressed = Object.entries(groups).filter(([, actions]) => actions.some(a => input.activity.has(a))).map(([key]) => key);
      if (elapsed > 0 || pressed.length) {
        this.progress[current] = recordHelp(row, pressed, elapsed);
        this.persist();
      }
    }
    this.last = active ? { time: now, mode: current } : null;
  }

  hidden(mode: 'foot' | 'car', flying: boolean) {
    const current = mode === 'car' ? 'car' : flying ? 'fly' : 'foot';
    return helpHidden(this.progress[current] ?? { counts: {}, elapsedMs: 0 }, Object.keys(GROUPS[current]));
  }

  private persist() {
    // Persist whole seconds and capped press counts, not a synchronous localStorage write every frame.
    const value = JSON.stringify(Object.fromEntries(Object.entries(this.progress).map(([mode, row]) =>
      [mode, { ...row, elapsedMs: Math.floor(row!.elapsedMs / 1000) * 1000 }])));
    if (value === this.stored) return;
    this.stored = value;
    try { localStorage.setItem(STORAGE_KEY, value); } catch { /* Storage may be unavailable. */ }
  }
}
