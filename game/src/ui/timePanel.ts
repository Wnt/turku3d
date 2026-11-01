// Developer time panel (developer tools: localhost or ?dev=1). T or Developer → Time of day shows it: an hour slider
// with a live readout, pause/resume, ×1/×10/×60/×600, quick times, ±1 h and ±10 min steps and the way back to the live
// clock. Like ?time=, it is a local view: this browser's sky, sun, shadows, timecycle, lit windows and street lights,
// and in single player the ambient traffic and walkers; multiplayer peers and the shared world keep the live cycle.
import { formatClock } from '../core/dayCycle';
import { CLOCK_SPEEDS } from '../render/dayClock';
import { DEFAULT_BINDINGS, type Action } from '../input/bindings';
import type { Input } from '../input';

/** What the panel and the Developer page drive (render/dayClock.ts DayClock). */
export interface DevClock {
  readonly hour: number;
  readonly running: boolean;
  /** The running developer clock's speed; null when live or frozen. */
  readonly rate: number | null;
  /** The speed a resume runs at. */
  readonly speed: number;
  /** Not the shared 30-minute cycle. */
  readonly local: boolean;
  readonly night: number;
  readonly sunElevation: number;
  seek(hour: number): void;
  step(hours: number): void;
  pause(): void;
  resume(rate?: number): void;
  goLive(): void;
}

/** Quick times on the cycle's sun path: sunrise, morning, high sun, golden hour (sun +3°), blue hour (−4°), night. */
export const TIME_PRESETS = [
  { id: 'dawn', label: 'Dawn', hour: 6.5 }, { id: '0900', label: '09:00', hour: 9 }, { id: '1300', label: '13:00', hour: 13 },
  { id: 'golden', label: 'Golden hour', hour: 19.5 }, { id: 'blue', label: 'Blue hour', hour: 20.5 },
  { id: '2200', label: '22:00', hour: 22 }, { id: '0400', label: '04:00', hour: 4 },
] as const;

/** The panel's keyboard shortcuts (input/bindings.ts); they act while the panel shows. */
export const TIME_KEYS = {
  timePause: { label: 'Pause / resume', run: (c: DevClock) => { if (c.running) c.pause(); else c.resume(); } },
  timeSpeed1: { label: '×1', run: (c: DevClock) => c.resume(CLOCK_SPEEDS[0]) },
  timeSpeed2: { label: '×10', run: (c: DevClock) => c.resume(CLOCK_SPEEDS[1]) },
  timeSpeed3: { label: '×60', run: (c: DevClock) => c.resume(CLOCK_SPEEDS[2]) },
  timeSpeed4: { label: '×600', run: (c: DevClock) => c.resume(CLOCK_SPEEDS[3]) },
  timeHourBack: { label: '−1 h', run: (c: DevClock) => c.step(-1) },
  timeMinutesBack: { label: '−10 min', run: (c: DevClock) => c.step(-1 / 6) },
  timeMinutesForward: { label: '+10 min', run: (c: DevClock) => c.step(1 / 6) },
  timeHourForward: { label: '+1 h', run: (c: DevClock) => c.step(1) },
  timeLive: { label: 'Live', run: (c: DevClock) => c.goLive() },
} satisfies Partial<Record<Action, { label: string; run: (clock: DevClock) => void }>>;
type TimeKey = keyof typeof TIME_KEYS;

/** US labels of the bound key codes; `keyboardLayout` swaps in what the keys print on this keyboard (Finnish Å ¨). */
const KEY_LABELS: Record<string, string> = { BracketLeft: '[', BracketRight: ']', Comma: ',', Period: '.' };
/** The key label of an action's first keyboard binding. */
export function keyLabel(action: Action): string {
  const code = DEFAULT_BINDINGS[action].find(b => b.kind === 'key');
  if (!code || code.kind !== 'key') return '';
  return KEY_LABELS[code.code] ?? code.code.replace(/^Key|^Digit/, '');
}
/** Chrome's keyboard layout map gives the printed labels; elsewhere the US ones stay. */
export async function keyboardLayout(): Promise<boolean> {
  const keyboard = (navigator as Navigator & { keyboard?: { getLayoutMap?: () => Promise<Map<string, string>> } }).keyboard;
  try {
    const map = await keyboard?.getLayoutMap?.();
    if (!map) return false;
    for (const code of Object.keys(KEY_LABELS)) { const key = map.get(code); if (key) KEY_LABELS[code] = key.toUpperCase(); }
    return true;
  } catch { return false; }
}

/** "22:00 · sun −13.4° · night 1.00 · ×600": clock time, sun elevation, night factor and how the clock runs. */
export function clockReadout(clock: DevClock): string {
  const how = !clock.local ? 'live' : clock.rate !== null ? `×${clock.rate}` : clock.running ? 'local cycle' : 'paused';
  return `${formatClock(clock.hour)} · sun ${clock.sunElevation.toFixed(1).replace('-', '−')}° · night ${clock.night.toFixed(2)} · ${how}`;
}

const STORAGE_KEY = 'turku3d.time-panel.v1';
const REFRESH_MS = 100;

export class TimePanel {
  readonly element = Object.assign(document.createElement('section'), { id: 'time-panel' });
  private readonly readout = Object.assign(document.createElement('span'), { className: 'time-readout' });
  private readonly slider = document.createElement('input');
  private readonly pauseButton: HTMLButtonElement;
  private readonly speedButtons: [number, HTMLButtonElement][] = [];
  private readonly liveButton: HTMLButtonElement;
  private readonly keyed: [Action, HTMLElement][] = [];
  private shown = false;
  private dragging = false;
  private lastRefresh = -Infinity;

  constructor(parent: HTMLElement, private readonly clock: DevClock) {
    const el = this.element;
    el.setAttribute('role', 'region');
    el.setAttribute('aria-label', 'Time of day');
    el.hidden = true;
    const header = document.createElement('header');
    const title = Object.assign(document.createElement('strong'), { textContent: 'Time' });
    this.readout.setAttribute('role', 'status');
    const close = this.button('×', () => this.toggle(), 'Close the time panel', 'timePanel');
    close.className = 'time-close';
    header.append(title, this.readout, close);
    const slider = this.slider;
    Object.assign(slider, { type: 'range', min: '0', max: '1440', step: '1' });
    slider.setAttribute('aria-label', 'Clock time');
    // A drag scrubs every input event (the sun and timecycle follow at once, the sky IBL at most every 150 ms); the
    // clock does not move the thumb under the pointer. A pointer drag gives the keys back to the game on release.
    slider.addEventListener('pointerdown', () => { this.dragging = true; });
    slider.addEventListener('input', () => { this.clock.seek(Number(slider.value) / 60); this.refresh(true); });
    slider.addEventListener('change', () => { if (this.dragging) slider.blur(); this.dragging = false; });
    slider.addEventListener('blur', () => { this.dragging = false; });
    const scale = Object.assign(document.createElement('div'), { className: 'time-scale' });
    scale.append(...['00:00', '06:00', '12:00', '18:00', '24:00'].map(t => Object.assign(document.createElement('span'), { textContent: t })));
    const run = document.createElement('div'); run.className = 'time-row';
    this.pauseButton = this.button('Pause', () => TIME_KEYS.timePause.run(this.clock), 'Pause or resume the clock', 'timePause');
    run.append(this.pauseButton);
    CLOCK_SPEEDS.forEach((speed, i) => {
      const action = `timeSpeed${i + 1}` as TimeKey;
      const b = this.button(`×${speed}`, () => TIME_KEYS[action].run(this.clock), `Run the clock at ×${speed}`, action);
      this.speedButtons.push([speed, b]); run.append(b);
    });
    this.liveButton = this.button('Live', () => TIME_KEYS.timeLive.run(this.clock), 'Back to the live clock', 'timeLive');
    run.append(this.liveButton);
    const steps = document.createElement('div'); steps.className = 'time-row';
    for (const action of ['timeHourBack', 'timeMinutesBack', 'timeMinutesForward', 'timeHourForward'] as const) {
      steps.append(this.button(TIME_KEYS[action].label, () => TIME_KEYS[action].run(this.clock), null, action));
    }
    const presets = document.createElement('div'); presets.className = 'time-row';
    for (const p of TIME_PRESETS) {
      const text = p.label === formatClock(p.hour) ? p.label : `${p.label} ${formatClock(p.hour)}`;
      const b = this.button(text, () => this.clock.seek(p.hour), null, null);
      b.dataset.preset = p.id; presets.append(b);
    }
    el.append(header, slider, scale, run, steps, presets);
    parent.append(el);
    try { this.shown = localStorage.getItem(STORAGE_KEY) === 'open'; } catch { /* Storage unavailable: starts closed. */ }
    void keyboardLayout().then(changed => { if (changed) this.labelKeys(); });
  }

  /** A button; a pointer click hands the keyboard back to the game, so WASD walks on right after it. */
  private button(text: string, act: () => void, title: string | null, action: Action | null) {
    const b = Object.assign(document.createElement('button'), { type: 'button', textContent: text });
    if (title) b.title = title;
    b.addEventListener('click', e => { act(); if (e.detail > 0) b.blur(); this.refresh(true); });
    if (action) {
      const kbd = document.createElement('kbd'); b.append(kbd);
      this.keyed.push([action, kbd]);
      kbd.textContent = keyLabel(action);
    }
    return b;
  }

  private labelKeys() { for (const [action, kbd] of this.keyed) kbd.textContent = keyLabel(action); }

  get open() { return this.shown; }

  toggle() {
    this.shown = !this.shown;
    try { localStorage.setItem(STORAGE_KEY, this.shown ? 'open' : 'closed'); } catch { /* Session state only. */ }
    // The mouse needs its pointer back for the slider.
    if (this.shown) void document.exitPointerLock?.();
    this.refresh(true);
  }

  /** Per frame: T toggles while playing; the time keys act while the panel shows, also while the pause menu is open. */
  handle(input: Input, playing: boolean) {
    if (playing && input.consume('timePanel')) this.toggle();
    this.element.hidden = !(playing && this.shown);
    for (const action of Object.keys(TIME_KEYS) as TimeKey[]) {
      if (input.consume(action) && playing && this.shown) { TIME_KEYS[action].run(this.clock); this.lastRefresh = -Infinity; }
    }
    this.refresh();
  }

  /** The readout, the thumb (unless it is being dragged) and the pressed states, about ten times a second. */
  refresh(now = false) {
    const t = performance.now();
    if (this.element.hidden || (!now && t - this.lastRefresh < REFRESH_MS)) return;
    this.lastRefresh = t;
    const c = this.clock;
    this.readout.textContent = clockReadout(c);
    const minutes = Math.round(c.hour * 60) % 1440;
    if (!this.dragging && Number(this.slider.value) % 1440 !== minutes) this.slider.value = String(minutes);
    this.slider.setAttribute('aria-valuetext', formatClock(c.hour));
    this.pauseButton.firstChild!.textContent = c.running ? 'Pause' : `Resume ×${c.speed}`;
    for (const [speed, b] of this.speedButtons) b.setAttribute('aria-pressed', String(c.rate === speed));
    this.liveButton.setAttribute('aria-pressed', String(!c.local));
  }

  dispose() { this.element.remove(); }
}
