// Damage indicator while driving a damaged car (vehicle/mechanics.ts): a small top view of the car, bottom centre,
// with its four corner zones and wheels tinted by level and the engine bay by the worse of radiator and engine,
// beside short lines for the damaged systems ("Radiator", "Engine", "Steering pull ←"). Hidden for an intact car;
// full strength for a few seconds after each new hit, then dimmed. No key, no pointer input.
import { mechanicsOf, type Mechanics } from '../vehicle/mechanics';

/** How long (ms) the indicator stays at full strength after the damage changed, and its strength afterwards. */
export const DAMAGE_HUD_FRESH_MS = 6000, DAMAGE_HUD_DIM = .45;
/** Level 0..4 tints: intact outline, then yellow → red. */
export const LEVEL_COLOURS = ['#f1f1ea40', '#f0d04a', '#f0a040', '#e8653a', '#d83a32'] as const;
const CORNERS = ['front left', 'front right', 'rear left', 'rear right'] as const;
/** A steering pull (front axle, degrees) the indicator names. */
const PULL_DEG = .1;

export interface DamageHudModel {
  /** Tints: corner zones and wheels in the physics wheel order (front left, front right, rear left, rear right). */
  zones: string[]; wheels: string[]; bay: string;
  /** Lines for the damaged systems, worst first: text and level. */
  lines: { text: string; level: number }[];
}

/** What the indicator shows for a mechanical state; null for an intact car. */
export function damageHudModel(m: Mechanics): DamageHudModel | null {
  if (!m.worst && !m.steer) return null;
  const lines: { text: string; level: number }[] = [];
  if (m.engine) lines.push({ text: 'Engine', level: m.engine });
  if (m.cooling) lines.push({ text: 'Radiator', level: m.cooling });
  const pull = (m.steer + (m.toe[0] + m.toe[1]) / 2) * 180 / Math.PI;
  if (Math.abs(pull) >= PULL_DEG || m.steering) lines.push({ text: `Steering pull ${pull > 0 ? '←' : '→'}`, level: Math.max(1, m.steering) });
  m.suspension.forEach((level, i) => { if (level) lines.push({ text: `Suspension ${CORNERS[i]}`, level }); });
  lines.sort((a, b) => b.level - a.level);
  // A wheel shows the worse of its suspension and a visible toe (a quarter degree reads as level 1).
  const wheels = m.suspension.map((level, i) => LEVEL_COLOURS[Math.max(level, Math.abs(m.toe[i]) * 180 / Math.PI >= .25 ? 1 : 0)]);
  return { zones: m.zones.map(level => LEVEL_COLOURS[level]), wheels, bay: LEVEL_COLOURS[Math.max(m.cooling, m.engine)], lines };
}

const PANEL = { position: 'fixed', left: '50%', bottom: '18px', transform: 'translateX(-50%)', display: 'flex', alignItems: 'center',
  gap: '8px', padding: '5px 9px', font: '600 12px/1.25 system-ui, sans-serif', color: '#f1f1ea', background: 'rgba(16,18,20,0.55)',
  borderRadius: '4px', pointerEvents: 'none', zIndex: '15', transition: 'opacity 0.6s ease' };

export class DamageHud {
  private el: HTMLElement | null = null;
  private parts: { zones: HTMLElement[]; wheels: HTMLElement[]; bay: HTMLElement; lines: HTMLElement } | null = null;
  private wire: string | null = null;
  private changedAt = -Infinity;
  private drawn = '';
  constructor(private readonly parent: HTMLElement) {}

  /** `wire`: the driven car's damage (VehicleState.damage), null when not driving; `now` in ms. */
  update(wire: string | null | undefined, now: number) {
    const model = wire ? damageHudModel(mechanicsOf(wire)) : null;
    if (!model) { if (this.el) this.el.hidden = true; this.wire = wire ?? null; return; }
    // A new hit, or getting into a damaged car: full strength for a while.
    if (wire !== this.wire) { this.changedAt = now; this.wire = wire!; }
    const el = this.el ?? this.build();
    el.hidden = false;
    const key = JSON.stringify(model);
    if (key !== this.drawn) {
      this.drawn = key;
      const p = this.parts!;
      model.zones.forEach((c, i) => { p.zones[i].style.background = c; });
      model.wheels.forEach((c, i) => { p.wheels[i].style.background = c; });
      p.bay.style.background = model.bay;
      p.lines.replaceChildren(...model.lines.map(line => {
        const row = document.createElement('div');
        row.textContent = line.text; row.style.color = LEVEL_COLOURS[line.level];
        return row;
      }));
    }
    const opacity = now - this.changedAt < DAMAGE_HUD_FRESH_MS ? '1' : String(DAMAGE_HUD_DIM);
    if (el.style.opacity !== opacity) el.style.opacity = opacity;
  }

  dispose() { this.el?.remove(); this.el = this.parts = null; }

  private build() {
    const el = this.el = document.createElement('div');
    el.id = 'damage-hud';
    Object.assign(el.style, PANEL);
    // The car seen from above, nose up: 26 x 46 px, the corner zones as quarters, wheels at the sides, the bay on top.
    const car = document.createElement('div');
    Object.assign(car.style, { position: 'relative', width: '26px', height: '46px', flex: 'none' });
    const box = (left: number, top: number, width: number, height: number, radius = '2px') => {
      const b = document.createElement('i');
      Object.assign(b.style, { position: 'absolute', left: `${left}px`, top: `${top}px`, width: `${width}px`, height: `${height}px`,
        borderRadius: radius, background: LEVEL_COLOURS[0] });
      car.append(b);
      return b;
    };
    const wheels = [box(0, 8, 3, 9), box(23, 8, 3, 9), box(0, 30, 3, 9), box(23, 30, 3, 9)];
    const zones = [box(4, 0, 8.5, 22, '6px 1px 1px 1px'), box(13.5, 0, 8.5, 22, '1px 6px 1px 1px'),
      box(4, 24, 8.5, 22, '1px 1px 1px 5px'), box(13.5, 24, 8.5, 22, '1px 1px 5px 1px')];
    const bay = box(8, 3, 10, 5, '2px');
    bay.style.outline = '1px solid rgba(16,18,20,0.7)';
    const lines = document.createElement('div');
    Object.assign(lines.style, { display: 'flex', flexDirection: 'column', gap: '1px', whiteSpace: 'nowrap', letterSpacing: '0.03em' });
    el.append(car, lines);
    this.parts = { zones, wheels, bay, lines };
    this.parent.append(el);
    return el;
  }
}
