import type { V3 } from '../core/origin';
import type { CharacterState, VehicleInput, VehicleState } from '../physics/protocol';
import { CAR } from '../physics/constants';
import { rotate } from '../vehicle/math';
import { clamp, type AudioEvent } from './mix';

/** Converts game snapshots into audio events once per physics tick, independent of render FPS. */
export class GameplayAudio {
  private tick = -1;
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
    if (mode !== this.mode && v) {
      this.emit({ type: 'door', position: v.pos, open: true });
      this.doorPosition = { ...v.pos }; this.closeAt = frame.time + 0.3;
      this.previousFeet = null; this.distance = 0;
    }
    this.mode = mode;
    if (this.doorPosition && frame.time >= this.closeAt) {
      this.emit({ type: 'door', position: v?.pos ?? this.doorPosition, open: false }); this.doorPosition = null;
    }
    if (v) {
      const lateral = rotate({ x: 0, y: 0, z: 1 }, v.rot);
      const sideSpeed = Math.abs(v.linvel.x * lateral.x + v.linvel.z * lateral.z);
      const contact = v.wheels.some(w => w.contact);
      const throttle = clamp(Math.abs(input?.throttle ?? 0) + (v.forwardSpeed < 0 ? input?.brake ?? 0 : 0));
      // Vehicle v1 has no drivetrain RPM. A three-gear approximation provides an audible rev range.
      const speed = Math.abs(v.forwardSpeed), gear = speed < 8 ? 1 : speed < 17 ? 2 : 3;
      this.emit({ type: 'car', position: v.pos, rpm: 850 + speed / gear * 300 + throttle * 700,
        throttle, speed: contact ? v.speed : 0, slip: contact ? Math.max(sideSpeed / 5, input?.handbrake ? 0.9 : 0) : 0,
        running: mode === 'car' });
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
      this.emit({ type: 'curb', position: v.pos, impulse: v.curbStrike.speed * CAR.mass * v.curbStrike.loss });
    }
    if (v?.impact && v.impact.id !== this.impactId) {
      this.impactId = v.impact.id;
      this.emit({ type: 'impact', position: v.pos, impulse: v.impact.impulse });
    }
  }
}
