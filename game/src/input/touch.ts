// Touch input source: a floating stick on the left half of the game element, drag-to-look and pinch-to-zoom on the
// right half, and thumb buttons for the action buttons. It produces the same actions as the keyboard and the gamepad;
// `Input` (index.ts) owns the browser listeners and merges this source into its frame.
import { DEFAULT_BINDINGS, MOVE_CURVE, emptyActions, glyph, radial, type Action, type Actions, type Bindings } from './bindings';

/** Thumb travel in CSS px for a full stick deflection; the base follows the thumb beyond it. */
export const STICK_RADIUS = 64;
/** Zoom steps per e-fold of pinch distance: about one mouse-wheel notch per 10 % pinch. */
const PINCH_ZOOM = 10;

/** Which buttons the overlay shows. `hidden`: start screen, fullscreen overlay or a non-touch player. */
export type TouchContext = 'hidden' | 'foot' | 'car' | 'menu' | 'paused';

interface Region { kind: 'stick' | 'look'; x: number; y: number; ox: number; oy: number }
export interface TouchFrame { actions: Actions; taps: ReadonlySet<Action>; lookX: number; lookY: number; zoom: number }

/** Pointer bookkeeping without DOM: one stick pointer, up to two look pointers (pinch) and any number of buttons. */
export class TouchPad {
  private regions = new Map<number, Region>();
  private buttons = new Map<number, Action>();
  private tapped = new Set<Action>();
  private lookX = 0;
  private lookY = 0;
  private zoom = 0;

  /** A pointer landed on the free game area at (x, y) of an element `width` px wide. */
  down(id: number, x: number, y: number, width: number) {
    const kind = x < width / 2 ? 'stick' : 'look';
    const regions = [...this.regions.values()];
    if (kind === 'stick' && regions.some(r => r.kind === 'stick')) return;
    if (kind === 'look' && regions.filter(r => r.kind === 'look').length >= 2) return;
    this.regions.set(id, { kind, x, y, ox: x, oy: y });
  }

  move(id: number, x: number, y: number) {
    const r = this.regions.get(id);
    if (!r) return;
    if (r.kind === 'stick') {
      const dx = x - r.ox, dy = y - r.oy, d = Math.hypot(dx, dy);
      if (d > STICK_RADIUS) { r.ox = x - dx / d * STICK_RADIUS; r.oy = y - dy / d * STICK_RADIUS; }
    } else {
      const other = [...this.regions.entries()].find(([k, o]) => k !== id && o.kind === 'look')?.[1];
      if (other) {
        const before = Math.hypot(r.x - other.x, r.y - other.y), after = Math.hypot(x - other.x, y - other.y);
        if (before > 1 && after > 1) this.zoom -= Math.log(after / before) * PINCH_ZOOM;
      } else {
        this.lookX += x - r.x; this.lookY += y - r.y;
      }
    }
    r.x = x; r.y = y;
  }

  /** A pointer was lifted or cancelled, on the game area or on a button. */
  up(id: number) { this.regions.delete(id); this.buttons.delete(id); }

  /** A thumb button went down: held until `up(id)`, and a press edge even if released before the next frame. */
  press(id: number, action: Action) { this.buttons.set(id, action); this.tapped.add(action); }

  /** Stick position for the overlay: origin and knob in CSS px, or null without a stick pointer. */
  get stick(): { ox: number; oy: number; x: number; y: number } | null {
    const r = [...this.regions.values()].find(p => p.kind === 'stick');
    return r ? { ox: r.ox, oy: r.oy, x: r.x, y: r.y } : null;
  }
  get active() { return this.regions.size + this.buttons.size > 0; }

  /** This frame's actions; look (px) and zoom (wheel notches) accumulated since the last sample, then reset. */
  sample(): TouchFrame {
    const actions = emptyActions();
    const s = this.stick;
    if (s) {
      // Screen y grows downwards; a thumb pushed up is forward. Same circular dead zone and curve as the gamepad.
      const [x, y] = radial((s.x - s.ox) / STICK_RADIUS, (s.oy - s.y) / STICK_RADIUS, MOVE_CURVE);
      actions.moveX = actions.steer = x;
      actions.moveY = y;
      actions.throttle = Math.max(0, y);
      actions.brake = Math.max(0, -y);
    }
    for (const action of this.buttons.values()) actions[action] = 1;
    const frame = { actions, taps: new Set(this.tapped), lookX: this.lookX, lookY: this.lookY, zoom: this.zoom };
    this.tapped.clear();
    this.lookX = this.lookY = this.zoom = 0;
    return frame;
  }

  clear() { this.regions.clear(); this.buttons.clear(); this.tapped.clear(); this.lookX = this.lookY = this.zoom = 0; }
}

interface ButtonSpec { action: Action; label: string; contexts: readonly TouchContext[]; corner?: true }
/** Thumb buttons, bottom right unless `corner` (top right). Glyphs are the HUD's gamepad glyphs. */
export const TOUCH_BUTTONS: readonly ButtonSpec[] = [
  { action: 'mission', label: 'Use', contexts: ['foot', 'car'] },
  { action: 'sprint', label: 'Sprint', contexts: ['foot'] },
  { action: 'jump', label: 'Jump', contexts: ['foot'] },
  { action: 'interact', label: 'Enter car', contexts: ['foot'] },
  { action: 'interact', label: 'Exit car', contexts: ['car'] },
  { action: 'handbrake', label: 'Handbrake', contexts: ['car'] },
  { action: 'menuBack', label: 'Back', contexts: ['menu'] },
  { action: 'map', label: 'Map', contexts: ['foot', 'car'], corner: true },
  { action: 'pause', label: 'Pause', contexts: ['foot', 'car'], corner: true },
  { action: 'pause', label: 'Resume', contexts: ['paused'], corner: true },
];

/** The on-screen part: stick base and knob, and the buttons, which report presses to the `Input` listeners. */
export class TouchOverlay {
  readonly element = document.createElement('div');
  private readonly base = document.createElement('div');
  private readonly knob = document.createElement('div');
  private readonly buttons: { spec: ButtonSpec; el: HTMLButtonElement }[] = [];
  private context: TouchContext = 'hidden';

  constructor(parent: HTMLElement, bindings: Bindings = DEFAULT_BINDINGS) {
    this.element.id = 'touch-controls';
    this.element.hidden = true;
    this.base.className = 'touch-stick';
    this.knob.className = 'touch-knob';
    this.base.append(this.knob);
    const pad = document.createElement('div'), corner = document.createElement('div');
    pad.className = 'touch-buttons';
    corner.className = 'touch-corner';
    for (const spec of TOUCH_BUTTONS) {
      const el = document.createElement('button');
      el.type = 'button';
      el.className = 'touch-button';
      el.dataset.action = spec.action;
      el.setAttribute('aria-label', spec.label);
      el.append(Object.assign(document.createElement('b'), { textContent: glyph(spec.action, bindings, true) }),
        Object.assign(document.createElement('small'), { textContent: spec.label }));
      (spec.corner ? corner : pad).append(el);
      this.buttons.push({ spec, el });
    }
    this.element.append(this.base, pad, corner);
    parent.append(this.element);
  }

  /** The action of a button element, for the pointer listeners. */
  actionOf(target: EventTarget | null): Action | null {
    const el = target instanceof Element ? target.closest<HTMLElement>('.touch-button') : null;
    return el && this.element.contains(el) ? el.dataset.action as Action : null;
  }

  setContext(context: TouchContext) {
    if (context === this.context) return;
    this.context = context;
    this.element.hidden = context === 'hidden';
    this.element.dataset.context = context;
    for (const { spec, el } of this.buttons) el.hidden = !spec.contexts.includes(context);
  }

  /** Draws the floating stick where the thumb landed. */
  draw(stick: TouchPad['stick']) {
    this.base.hidden = !stick;
    if (!stick) return;
    this.base.style.transform = `translate(${stick.ox}px, ${stick.oy}px)`;
    this.knob.style.transform = `translate(${stick.x - stick.ox}px, ${stick.y - stick.oy}px)`;
  }

  dispose() { this.element.remove(); }
}
