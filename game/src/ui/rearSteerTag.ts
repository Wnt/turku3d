// Rear-steer tag of a 4WS vehicle (SPEC §3.5): "REAR AUTO / AUTO 50% / LOCK / SIDEWAYS" with a rear-angle tick, shown
// only in the trophy truck. The lockout tints it while it centres the rear above 30 mph. Above it a key hint
// ("Ⓐ · Rear steer: AUTO", the sprint binding of the device in use) shows for a few seconds after entering the truck
// and briefly after each mode change, then fades. Neither takes pointer input.
import type { VehicleState } from '../physics/protocol';
import type { RearSteerMode } from '../vehicle/rearSteer';
import { REAR_MODE_LABEL } from '../vehicle/rearSteerControl';

/** How long (ms) the key hint shows after entering the truck and after a mode change. */
export const HINT_ENTER_MS = 4000, HINT_CHANGE_MS = 2500;

/** Tag text and tick offset (−1..1, + is the rear steering left) of a vehicle state, or null without rear steer. */
export function rearSteerTag(state: VehicleState | null | undefined, maxAngle: number) {
  if (!state?.rearMode) return null;
  const rear = state.wheels.slice(2);
  const angle = rear.length ? rear.reduce((s, w) => s + w.steer, 0) / rear.length : 0;
  return { mode: state.rearMode, text: `REAR ${REAR_MODE_LABEL[state.rearMode]}`, tick: Math.max(-1, Math.min(1, angle / maxAngle)),
    locked: !!state.rearLocked };
}

/** Key hint text: the binding (empty without one, e.g. on touch) and the mode. */
export const rearSteerHint = (binding: string, mode: RearSteerMode) =>
  `${binding ? `${binding} · ` : ''}Rear steer: ${REAR_MODE_LABEL[mode]}`;

/** When the key hint shows: from the tag appearing (entering the truck) and from each mode change. */
export class RearSteerHintTimer {
  private mode: RearSteerMode | null = null;
  private until = -Infinity;

  /** `mode`: the rear mode of the shown truck, null while the tag is hidden. Returns whether the hint shows at `now`. */
  update(mode: RearSteerMode | null, now: number): boolean {
    if (mode === null) { this.mode = null; return false; }
    if (this.mode === null) this.until = now + HINT_ENTER_MS;
    else if (mode !== this.mode) this.until = Math.max(this.until, now + HINT_CHANGE_MS);
    this.mode = mode;
    return now < this.until;
  }
}

const PILL = { position: 'fixed', left: '50%', transform: 'translateX(-50%)', font: '600 12px/1.2 system-ui, sans-serif',
  color: '#f1f1ea', background: 'rgba(16,18,20,0.55)', borderRadius: '4px', pointerEvents: 'none', zIndex: '15' };

export class RearSteerTag {
  private el: HTMLElement | null = null;
  private tick: HTMLElement | null = null;
  private hint: HTMLElement | null = null;
  private last = '';
  private readonly timer = new RearSteerHintTimer();
  constructor(private parent: HTMLElement, private maxAngle: number) {}

  /** `binding`: the sprint prompt of the device in use (input.prompt), `now` in ms. */
  update(state: VehicleState | null | undefined, binding: string, now: number) {
    const tag = rearSteerTag(state, this.maxAngle);
    const hinted = this.timer.update(tag?.mode ?? null, now);
    if (!tag) { if (this.el) this.el.hidden = this.hint!.hidden = true; return; }
    if (!this.el) {
      this.el = document.createElement('div'); this.el.id = 'rear-steer-tag';
      Object.assign(this.el.style, PILL, { bottom: '64px', padding: '3px 10px 9px', letterSpacing: '0.08em' });
      this.tick = document.createElement('i');
      Object.assign(this.tick.style, { position: 'absolute', bottom: '3px', left: '50%', width: '2px', height: '4px', background: 'currentColor' });
      this.el.append(document.createElement('span'), this.tick);
      // Above the tag (64 px + its 26 px height + a gap); fades in and out on opacity alone, so it never moves.
      this.hint = document.createElement('div'); this.hint.id = 'rear-steer-hint';
      Object.assign(this.hint.style, PILL, { bottom: '96px', padding: '4px 10px', letterSpacing: '0.04em', whiteSpace: 'nowrap',
        opacity: '0', transition: 'opacity 0.5s ease' });
      this.parent.append(this.el, this.hint);
    }
    this.el.hidden = this.hint!.hidden = false;
    const key = `${tag.text}|${tag.locked}`;
    if (key !== this.last) {
      this.last = key;
      (this.el.firstChild as HTMLElement).textContent = tag.locked ? `${tag.text} · LOCKED` : tag.text;
      this.el.style.color = tag.locked ? '#f0b44a' : '#f1f1ea';
    }
    const text = rearSteerHint(binding, tag.mode);
    if (this.hint!.textContent !== text) this.hint!.textContent = text;
    const opacity = hinted ? '1' : '0';
    if (this.hint!.style.opacity !== opacity) this.hint!.style.opacity = opacity;
    // The tick moves the way the rear wheels point: left of centre when the rear steers left.
    this.tick!.style.left = `calc(50% - ${(tag.tick * 40).toFixed(1)}%)`;
  }
}
