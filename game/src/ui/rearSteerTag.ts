// Rear-steer tag of a 4WS vehicle (SPEC §3.5): "REAR AUTO / CRAB / MAN / LOCK" with a rear-angle tick, shown only in
// the trophy truck. The lockout tints it while it centres the rear above 30 mph.
import type { VehicleState } from '../physics/protocol';
import { REAR_MODE_TAG } from '../vehicle/rearSteerControl';

/** Tag text and tick offset (−1..1, + is the rear steering left) of a vehicle state, or null without rear steer. */
export function rearSteerTag(state: VehicleState | null | undefined, maxAngle: number) {
  if (!state?.rearMode) return null;
  const rear = state.wheels.slice(2);
  const angle = rear.length ? rear.reduce((s, w) => s + w.steer, 0) / rear.length : 0;
  return { text: REAR_MODE_TAG[state.rearMode], tick: Math.max(-1, Math.min(1, angle / maxAngle)), locked: !!state.rearLocked };
}

export class RearSteerTag {
  private el: HTMLElement | null = null;
  private tick: HTMLElement | null = null;
  private last = '';
  constructor(private parent: HTMLElement, private maxAngle: number) {}

  update(state: VehicleState | null | undefined) {
    const tag = rearSteerTag(state, this.maxAngle);
    if (!tag) { if (this.el) this.el.hidden = true; return; }
    if (!this.el) {
      this.el = document.createElement('div'); this.el.id = 'rear-steer-tag';
      Object.assign(this.el.style, { position: 'fixed', left: '50%', bottom: '64px', transform: 'translateX(-50%)', padding: '3px 10px 9px',
        font: '600 12px/1.2 system-ui, sans-serif', letterSpacing: '0.08em', color: '#f1f1ea', background: 'rgba(16,18,20,0.55)',
        borderRadius: '4px', pointerEvents: 'none', zIndex: '15' });
      this.tick = document.createElement('i');
      Object.assign(this.tick.style, { position: 'absolute', bottom: '3px', left: '50%', width: '2px', height: '4px', background: 'currentColor' });
      this.el.append(document.createElement('span'), this.tick);
      this.parent.append(this.el);
    }
    this.el.hidden = false;
    const key = `${tag.text}|${tag.locked}`;
    if (key !== this.last) {
      this.last = key;
      (this.el.firstChild as HTMLElement).textContent = tag.locked ? `${tag.text} · LOCKED` : tag.text;
      this.el.style.color = tag.locked ? '#f0b44a' : '#f1f1ea';
    }
    // The tick moves the way the rear wheels point: left of centre when the rear steers left.
    this.tick!.style.left = `calc(50% - ${(tag.tick * 40).toFixed(1)}%)`;
  }
}
