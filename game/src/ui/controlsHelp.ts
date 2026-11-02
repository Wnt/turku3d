import { devToolsEnabled } from '../core/localOnly';
import type { Action, Input } from '../input';
import { helpHidden, recordHelp, type HelpProgress } from '../core/helpLearning';

/** Device-specific prompts for the current control mode. */
export function controlsHelp(input: Input, mode: 'foot' | 'car', flying: boolean, paused: boolean, lost: boolean) {
  const photo = devToolsEnabled(location) ? ` · ${input.prompt('positionHud')} position · ${input.prompt('photoCharacter')} hide/show character · J copy position · T time of day` : '';
  return (paused ? `${input.prompt('pause')} Resume`
      : lost ? `${input.prompt('respawn')} return to solid ground · ${input.prompt('map')} map · ${input.prompt('pause')} pause`
      : mode === 'foot' && flying
      ? `Flying · ${input.prompt('moveY')} move · ${input.prompt('flyUp')} up · ${input.prompt('flyDown')} down · ${input.prompt('sprint')} fast · ${input.prompt('jump')} ${input.prompt('jump')} land · ${input.prompt('pause')} pause`
      : mode === 'foot'
      ? `${input.prompt('moveY')} move · ${input.source === 'gamepad' ? 'Ⓡ' : 'mouse'} look · ${input.prompt('sprint')} sprint · ${input.prompt('jump')} jump · ${input.prompt('interact')} enter vehicle · ${input.prompt('cameraReset')} camera · ${input.prompt('map')} map · ${input.prompt('pause')} pause`
      : `${input.prompt('throttle')} throttle · ${input.prompt('brake')} brake/reverse · ${input.prompt('steer')} steer · ${input.prompt('handbrake')} handbrake · ${input.prompt('horn')} horn (hold) · ${input.prompt('lights')} lights · ${input.prompt('interact')} exit · ${input.prompt('cameraReset')} camera · ${input.prompt('map')} map · ${input.prompt('pause')} pause`) + photo;
}

const GROUPS = {
  foot: { move: ['moveX', 'moveY'], look: ['lookX', 'lookY'], sprint: ['sprint'], jump: ['jump'],
    interact: ['interact'], camera: ['cameraReset'], map: ['map'], pause: ['pause'] },
  fly: { move: ['moveX', 'moveY'], up: ['flyUp'], down: ['flyDown'], fast: ['sprint'], land: ['jump'], pause: ['pause'] },
  car: { horn: ['horn'], lights: ['lights'], throttle: ['throttle'], brake: ['brake'], steer: ['steer'], handbrake: ['handbrake'],
    interact: ['interact'], camera: ['cameraReset'], map: ['map'], pause: ['pause'] },
} satisfies Record<string, Record<string, Action[]>>;
type HelpMode = keyof typeof GROUPS;
const PROMPTS = Object.fromEntries(Object.entries(GROUPS).map(([mode, groups]) =>
  [mode, { keys: Object.keys(groups), groups: Object.entries(groups) }])) as
  Record<HelpMode, { keys: string[]; groups: [string, Action[]][] }>;
const NO_PRESSES: readonly string[] = [];
const STORAGE_KEY = 'turku3d.controls-help.v1';

/** Per-mode browser memory. Menus/paused time do not spend the minute; held buttons do not count as new uses. */
export class ControlsHelp {
  private progress: Partial<Record<HelpMode, HelpProgress>> = {};
  private lastMode: HelpMode | null = null;
  private lastTime = 0;
  private stored = '';
  private rendered: { source: Input['source']; mode: 'foot' | 'car'; flying: boolean; paused: boolean; lost: boolean; at: number } | null = null;
  constructor(private element: HTMLElement) {
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
    const { keys, groups } = PROMPTS[current];
    const row = this.progress[current] ??= { counts: {}, elapsedMs: 0 };
    if (helpHidden(row, keys)) { this.lastMode = null; return; }
    if (active) {
      const elapsed = this.lastMode === current ? now - this.lastTime : 0;
      const pressed = input.activity.size ? groups.filter(([, actions]) => actions.some(a => input.activity.has(a))).map(([key]) => key) : NO_PRESSES;
      if (elapsed > 0 || pressed.length) {
        const next = this.progress[current] = recordHelp(row, pressed, elapsed);
        if (next.counts !== row.counts || Math.floor(next.elapsedMs / 1000) !== Math.floor(row.elapsedMs / 1000)) this.persist();
      }
    }
    this.lastMode = active ? current : null;
    this.lastTime = now;
  }

  hidden(mode: 'foot' | 'car', flying: boolean) {
    const current = mode === 'car' ? 'car' : flying ? 'fly' : 'foot';
    const row = this.progress[current];
    return row ? helpHidden(row, PROMPTS[current].keys) : false;
  }

  /** State changes update immediately; a periodic refresh also covers in-place binding edits. */
  render(input: Input, mode: 'foot' | 'car', flying: boolean, paused: boolean, lost: boolean, playing: boolean, now: number) {
    const hidden = !playing || this.hidden(mode, flying);
    if (this.element.hidden !== hidden) this.element.hidden = hidden;
    if (hidden) return;
    const previous = this.rendered;
    if (previous && previous.source === input.source && previous.mode === mode && previous.flying === flying
      && previous.paused === paused && previous.lost === lost && now - previous.at < 1000) return;
    this.rendered = { source: input.source, mode, flying, paused, lost, at: now };
    const text = controlsHelp(input, mode, flying, paused, lost);
    if (this.element.textContent !== text) this.element.textContent = text;
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
