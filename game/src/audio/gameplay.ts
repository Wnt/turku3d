import type { V3 } from '../core/origin';
import type { CharacterState, VehicleInput, VehicleState } from '../physics/protocol';
import { VEHICLES, type VehicleKind } from '../physics/constants';
import { TyreSlipFilter } from '../vehicle/tyreSlip';
import { clamp, type AudioEvent } from './mix';

/** Converts game snapshots into audio events once per physics tick, independent of render FPS. */
/** Vehicle v1 has no drivetrain RPM. A three-gear approximation provides the car's audible rev range; the
 *  scooter's continuously variable transmission holds high revs that climb with speed; the truck's V6 idles low
 *  and pulls four long gears up to 120 km/h. */
export function engineRpm(kind: VehicleKind, speed: number, throttle: number) {
  if (kind === 'scooter') return 1800 + throttle * 4200 + speed * 160;
  if (kind === 'truck') {
    const gear = speed < 9 ? 1 : speed < 17 ? 2 : speed < 25 ? 3 : 4;
    return 800 + speed / gear * 280 + throttle * 900;
  }
  const gear = speed < 8 ? 1 : speed < 17 ? 2 : 3;
  return 850 + speed / gear * 300 + throttle * 700;
}

export class GameplayAudio {
  private tick = -1;
  private readonly tyreSlip = new TyreSlipFilter(4);
  private slipTick = -1;
  private slipStrength = 0;
  private distance = 0;
  private previousFeet: V3 | null = null;
  private mode: 'foot' | 'car' = 'foot';
  private curbId = 0;
  private impactId = 0;
  private connected = false;
  private closeAt = 0;
  private doorPosition: V3 | null = null;

  constructor(private readonly emit: (event: AudioEvent) => void) {}

  update(frame: { tick: number; time: number; paused: boolean; mode: 'foot' | 'car'; connected: boolean;
    character: CharacterState | null; vehicle: VehicleState | null; input: VehicleInput | null;
    position: V3; forward: V3; up: V3 }) {
    const { character: c, vehicle: v, input, mode } = frame;
    this.emit({ type: 'listener', position: frame.position, forward: frame.forward, up: frame.up, paused: frame.paused });
    if (frame.connected && !this.connected) this.emit({ type: 'gamepad-connected' });
    this.connected = frame.connected;
    // Car doors open and close on entry and exit; a scooter has none.
    if (mode !== this.mode && v && v.kind !== 'scooter') {
      this.emit({ type: 'door', position: v.pos, open: true });
      this.doorPosition = { ...v.pos }; this.closeAt = frame.time + 0.3;
      this.previousFeet = null; this.distance = 0;
    }
    this.mode = mode;
    if (this.doorPosition && frame.time >= this.closeAt) {
      this.emit({ type: 'door', position: v?.pos ?? this.doorPosition, open: false }); this.doorPosition = null;
    }
    if (v) {
      if (!frame.paused && frame.tick !== this.slipTick) {
        const dt = this.slipTick < 0 ? 1 / 60 : (frame.tick - this.slipTick) / 60;
        this.slipTick = frame.tick; this.slipStrength = 0;
        for (let i = 0; i < 4; i++) {
          if (v.wheels[i]) this.slipStrength = Math.max(this.slipStrength, this.tyreSlip.update(i, v.wheels[i], v.speed, dt));
          else this.tyreSlip.reset(i);
        }
      }
      const slip = this.slipStrength;
      const contact = v.wheels.some(w => w.contact);
      const throttle = clamp(Math.abs(input?.throttle ?? 0) + (v.forwardSpeed < 0 ? input?.brake ?? 0 : 0));
      const kind = v.kind ?? 'car', rpm = engineRpm(kind, Math.abs(v.forwardSpeed), throttle);
      this.emit({ type: 'car', kind, position: v.pos, rpm,
        throttle, speed: contact ? v.speed : 0, slip,
        running: mode === 'car' });
    }
    if (!v || mode !== 'car') {
      for (let i = 0; i < 4; i++) this.tyreSlip.reset(i);
      this.slipTick = -1; this.slipStrength = 0;
    }
    if (frame.paused || frame.tick === this.tick) return;
    this.tick = frame.tick;
    if (c?.enabled && c.grounded && mode === 'foot') {
      if (this.previousFeet) {
        const d = Math.hypot(c.pos.x - this.previousFeet.x, c.pos.z - this.previousFeet.z);
        if (d < 2) this.distance += d; else this.distance = 0; // teleports never produce a burst
      }
      this.previousFeet = { ...c.pos };
      const speed = Math.hypot(c.vel.x, c.vel.z), stride = speed > 4 ? 1.35 : 0.85;
      if (this.distance >= stride && speed > 0.15) {
        this.distance %= stride;
        this.emit({ type: 'footstep', material: c.groundMaterial, position: c.pos, speed });
      }
    } else { this.previousFeet = null; this.distance = 0; }
    if (v?.curbStrike && v.curbStrike.id !== this.curbId) {
      this.curbId = v.curbStrike.id;
      this.emit({ type: 'curb', position: v.pos, impulse: v.curbStrike.speed * VEHICLES[v.kind ?? 'car'].mass * v.curbStrike.loss });
    }
    if (v?.impact && v.impact.id !== this.impactId) {
      this.impactId = v.impact.id;
      this.emit({ type: 'impact', position: v.pos, impulse: v.impact.impulse });
    }
  }
}
