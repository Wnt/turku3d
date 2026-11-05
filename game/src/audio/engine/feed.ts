import type { VehicleState, WheelState } from '../../physics/protocol';
import { clamp } from './dsp';
import { Drivetrain, type EngineDrive } from './drivetrain';
import type { Drv } from '../../net/drv';

/** Turns a truck's VehicleState (local or remote) into the engine voice input of SPEC §4.6, identically on every
 *  client: the driven-wheel speed comes from Δ WheelState.rotation, the rear-steer slew from the rear wheels' synced
 *  steer. The local driver passes its input; a remote truck passes the synced `drv` record, or nothing, in which case
 *  the throttle is estimated from the acceleration. */

/** SPEC §5 wire record [throttlePct 0..100, rpm10 0..800, gear −1..3, rearMode 0..3] (net/drv.ts encodes it). */
export type NetDrive = Readonly<Drv>;

export interface FeedControl {
  /** Local driver: throttle/brake after handling (0..1) and whether reverse is engaged. */
  throttle?: number; brake?: number; reverse?: boolean;
  /** Remote: the synced record (preferred over the estimate). */
  drv?: NetDrive | null;
}

export interface FeedOutput { drive: EngineDrive; rearSlew: number; drivenWheelSpeed: number }

const TWO_PI = Math.PI * 2;
const wrap = (a: number) => a - TWO_PI * Math.round(a / TWO_PI);

export class TruckSoundFeed {
  private readonly drivetrain: Drivetrain;
  private rotations: number[] = [];
  private rear = NaN;
  private slew = 0;
  private speed = NaN;
  private accelThrottle = 0;
  private netRpm = NaN;
  private netGear = 1;
  private shiftLeft = 0;
  private last: EngineDrive | null = null;

  constructor(seed = 1, private readonly radius = 0.533, private readonly driven: readonly number[] = [0, 1, 2, 3],
    private readonly rearWheels: readonly number[] = [2, 3]) {
    this.drivetrain = new Drivetrain({ seed, wheelRadius: radius });
  }

  step(dt: number, v: Pick<VehicleState, 'forwardSpeed' | 'speed' | 'wheels'>, control: FeedControl = {}): FeedOutput {
    dt = clamp(dt, 1e-3, 0.25);
    const forward = Number.isFinite(v.forwardSpeed) ? v.forwardSpeed : 0;
    const wheelSpeed = this.wheelSpeed(dt, v.wheels, forward);
    const contacts = v.wheels.reduce((n, w) => n + (w?.contact ? 1 : 0), 0);
    // Rear-steer slew from the synced rear steer angles (rad/s, smoothed over ≈ 80 ms).
    const rear = this.rearWheels.reduce((s, i) => s + (v.wheels[i]?.steer ?? 0), 0) / this.rearWheels.length;
    const raw = Number.isFinite(this.rear) ? (rear - this.rear) / dt : 0;
    this.rear = rear; this.slew += (raw - this.slew) * (1 - Math.exp(-dt / 0.08));
    const accel = Number.isFinite(this.speed) ? (forward - this.speed) / dt : 0;
    this.speed = forward;

    let drive: EngineDrive;
    if (control.drv) {
      const [pct, rpm10, gear] = control.drv, throttle = clamp(pct / 100), target = clamp(rpm10 * 10, 0, 8000);
      // The synced rpm, smoothed with a 60 ms first-order filter; a gear change opens the shift window.
      this.netRpm = Number.isFinite(this.netRpm) ? this.netRpm + (target - this.netRpm) * (1 - Math.exp(-dt / 0.06)) : target;
      if (gear !== this.netGear) { this.shiftLeft = gear > this.netGear && this.netGear > 0 ? 0.25 : 0; this.netGear = gear; }
      this.shiftLeft = Math.max(0, this.shiftLeft - dt);
      const load = throttle > 0.1 ? (contacts === 0 ? 0.3 : throttle) : -clamp((this.netRpm - 1200) / 2500) * (1 - throttle / 0.1);
      drive = { rpm: this.netRpm, load, throttle, gear, shifting: this.shiftLeft > 0, limiter: this.netRpm >= 6990 && throttle > 0.95 };
    } else {
      let throttle = control.throttle;
      if (throttle === undefined) {
        // Remote without drv: throttle from the forward acceleration (4 m/s² ≈ full throttle), held briefly.
        this.accelThrottle += (clamp(accel / 4) - this.accelThrottle) * (1 - Math.exp(-dt / 0.15));
        throttle = contacts === 0 ? this.last?.throttle ?? 0 : this.accelThrottle;
      }
      drive = this.drivetrain.step({ dt, throttle: clamp(throttle), brake: clamp(control.brake ?? 0), forwardSpeed: forward,
        drivenWheelSpeed: wheelSpeed, contacts, reverse: control.reverse ?? forward < -0.5, rearSteerMoving: Math.abs(this.slew) > 0.1 });
    }
    this.last = drive;
    return { drive, rearSlew: this.slew, drivenWheelSpeed: wheelSpeed };
  }

  /** Mean rolling speed (m/s) of the driven wheels in contact, from the change of their rotation angle. The angle
   *  step is unwrapped around the step the body speed predicts, so long frames (hitches, 15 fps, remote poses) do not
   *  alias once ω·Δt passes π: only the slip part (wheel minus body) has to stay within ±π. */
  private wheelSpeed(dt: number, wheels: readonly WheelState[], forward: number) {
    const expected = forward * dt / this.radius;
    let sum = 0, n = 0, any = 0, anyN = 0;
    for (const i of this.driven) {
      const w = wheels[i];
      if (!w || !Number.isFinite(w.rotation)) { this.rotations[i] = NaN; continue; }
      const prev = this.rotations[i];
      this.rotations[i] = w.rotation;
      if (!Number.isFinite(prev)) continue;
      const speed = (expected + wrap(w.rotation - prev - expected)) / dt * this.radius;
      any += speed; anyN++;
      if (w.contact) { sum += speed; n++; }
    }
    return n ? sum / n : anyN ? any / anyN : 0;
  }
}
