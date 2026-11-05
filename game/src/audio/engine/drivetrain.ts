/** Presentation-only rpm and gear model of the trophy truck's 3-speed automatic (SPEC §4.2). It feeds sound, haptics
 *  and the HUD and never feeds forces back into the physics. Pure and deterministic for a given seed, so the local
 *  driver and every remote client compute the same revs from the same synced inputs. */

export interface DriveInput { dt: number; throttle: number; brake: number; forwardSpeed: number;
  /** m/s from Δ WheelState.rotation × r, averaged over driven wheels in contact. */
  drivenWheelSpeed: number;
  /** Wheels in contact, 0..4. */
  contacts: number; reverse: boolean; rearSteerMoving: boolean }
export interface EngineDrive { rpm: number;
  /** −1 overrun .. +1 full load. */
  load: number; throttle: number;
  /** −1 R, 0 N, 1..3. */
  gear: number; shifting: boolean; limiter: boolean }

export interface DrivetrainSpec {
  idle: number; idleWander: number; idleDip: number;
  /** Forward ratios, then the effective final drive. */
  gears: readonly number[]; reverse: number; final: number;
  stall: number; wheelRadius: number;
  softLimit: number; hardLimit: number;
  freeRise: number; freeFall: number;
  /** Per-second approach rate toward the coupled rpm after a lift-off, and the unlocked converter's overrun ratio. */
  liftRate: number; overrunSlip: number;
  shiftTime: number; seed: number;
}

export const DRIVETRAIN: DrivetrainSpec = {
  idle: 920, idleWander: 0.03, idleDip: 45,
  gears: [2.48, 1.48, 1.0], reverse: 2.08, final: 8.5,
  stall: 3200, wheelRadius: 0.533,
  softLimit: 7000, hardLimit: 7200,
  freeRise: 4500, freeFall: 3000,
  // SPEC §4.2 quotes 1.3/s toward the coupled value; with the converter's 6 % drive slip alone that cannot reach the
  // measured 0.70-0.80x within 0.6 s (S4). On a lift the converter unlocks and the engine runs below the turbine
  // (overrunSlip), approached at 4/s: 0.80x after 0.33 s, 0.75x after 0.6 s.
  liftRate: 4, overrunSlip: 0.72,
  shiftTime: 0.25, seed: 1,
};

const clamp = (v: number, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, Number.isFinite(v) ? v : lo));
const RAD_TO_RPM = 60 / (2 * Math.PI);

/** The upshift point at this throttle (6,600 rpm flat out, 2,800 coasting). */
export const upshiftRpm = (throttle: number) => 2800 + 3800 * clamp(throttle);

export class Drivetrain {
  readonly spec: DrivetrainSpec;
  private rpm: number;
  private gear = 1;
  private shiftLeft = 0;
  private shiftFrom = 0;
  private time = 0;
  private liftLeft = 0;
  private history: { t: number; throttle: number }[] = [];
  private readonly wander: [number, number];

  constructor(spec: Partial<DrivetrainSpec> = {}) {
    this.spec = { ...DRIVETRAIN, ...spec };
    this.rpm = this.spec.idle;
    let s = (this.spec.seed * 2654435761) >>> 0;
    const rand = () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 4294967296; };
    this.wander = [rand() * Math.PI * 2, rand() * Math.PI * 2];
  }

  /** Engine rpm at this driven-wheel speed (m/s) in a gear, without converter slip. */
  coupledRpm(wheelSpeed: number, gear = this.gear) {
    const ratio = gear < 0 ? this.spec.reverse : this.spec.gears[Math.max(0, Math.min(this.spec.gears.length - 1, gear - 1))];
    return Math.abs(wheelSpeed) / this.spec.wheelRadius * RAD_TO_RPM * ratio * this.spec.final;
  }

  step(i: DriveInput): EngineDrive {
    const sp = this.spec, dt = clamp(i.dt, 0, 0.25), throttle = clamp(i.throttle);
    this.time += dt;
    // Throttle history for the lift-off and kickdown triggers (a 0.15 s window).
    this.history.push({ t: this.time, throttle });
    while (this.history.length > 1 && this.time - this.history[0].t > 0.15) this.history.shift();
    const recentMax = Math.max(...this.history.map(h => h.throttle)), recentMin = Math.min(...this.history.map(h => h.throttle));
    const lifted = recentMax - throttle > 0.5, kicked = throttle - recentMin > 0.6;
    if (lifted) this.liftLeft = 0.8;
    this.liftLeft = Math.max(0, this.liftLeft - dt);

    const wander = 1 + sp.idleWander * 0.5 * (Math.sin(2 * Math.PI * 0.55 * this.time + this.wander[0])
      + Math.sin(2 * Math.PI * 0.87 * this.time + this.wander[1]));
    let idle = sp.idle * wander;
    if (i.rearSteerMoving && this.rpm < 1300) idle -= sp.idleDip;

    const wheel = Number.isFinite(i.drivenWheelSpeed) ? i.drivenWheelSpeed : 0;
    const forward = Number.isFinite(i.forwardSpeed) ? i.forwardSpeed : 0;
    const slip = Math.abs(wheel) > 1 ? (Math.abs(wheel) - Math.abs(forward)) / Math.abs(wheel) : 0;
    const free = i.contacts <= 0 || slip > 0.25;

    // Gear selection: reverse follows the input, forward gears shift on the coupled rpm.
    if (i.reverse && this.gear > 0 && Math.abs(forward) < 1.5) this.gear = -1;
    else if (!i.reverse && this.gear < 0) this.gear = 1;
    if (this.gear > 0 && this.shiftLeft <= 0 && !free) {
      // Upshift on the engine rpm (converter slip included); downshift on the lower gear's coupled rpm.
      const up = upshiftRpm(throttle);
      if (this.gear < sp.gears.length && this.rpm >= up && this.coupledRpm(wheel) > sp.stall) this.shift(this.gear + 1);
      else if (this.gear > 1) {
        const lower = this.coupledRpm(wheel, this.gear - 1);
        if (lower < up - 600 || (kicked && lower < up)) this.shift(this.gear - 1);
      }
    }

    const couple = clamp(Math.abs(wheel) / 6);
    // Driving, the converter slips 6 % at full throttle; coasting, it unlocks and the engine runs below the turbine.
    const driveSlip = 1 + 0.06 * Math.max(0.15, throttle);
    const coupledTarget = this.coupledRpm(wheel) * (throttle >= 0.15 ? driveSlip
      : sp.overrunSlip + (driveSlip - sp.overrunSlip) * throttle / 0.15);
    const stallTarget = idle + throttle * (sp.stall - idle) * (1 - couple);
    let demand: number;
    if (free) {
      demand = idle + throttle * (sp.softLimit - idle);
      const rate = demand > this.rpm ? sp.freeRise : -sp.freeFall;
      this.rpm += rate * dt;
      if ((rate > 0 && this.rpm > demand) || (rate < 0 && this.rpm < demand)) this.rpm = demand;
    } else {
      demand = Math.max(coupledTarget, stallTarget);
      if (this.shiftLeft > 0) {
        // The shift interpolates the revs from the old gear's value to the new one's over shiftTime.
        const k = 1 - this.shiftLeft / sp.shiftTime;
        const from = Math.max(this.shiftFrom, stallTarget);
        this.rpm = from + (Math.max(demand, idle) - from) * Math.min(1, k * 1.6);
      } else if (this.liftLeft > 0 && this.rpm > demand) {
        this.rpm = demand + (this.rpm - demand) * Math.exp(-sp.liftRate * dt);
        if (this.rpm - demand < 15) this.liftLeft = 0;
      } else {
        // Coupled: the converter pins the revs; a short lag keeps the line smooth over wheel-speed noise.
        this.rpm = demand + (this.rpm - demand) * Math.exp(-dt / 0.06);
      }
    }
    if (this.shiftLeft > 0) this.shiftLeft = Math.max(0, this.shiftLeft - dt);
    this.rpm = clamp(this.rpm, Math.min(idle, sp.idle - sp.idleDip), sp.softLimit);
    const limiter = demand > sp.hardLimit;

    let load: number;
    if (free) load = throttle > 0.1 ? 0.3 : -clamp((this.rpm - 1200) / 2500);
    else if (throttle > 0.1) load = throttle;
    else load = -clamp((this.rpm - 1200) / 2500) * (1 - throttle / 0.1);
    return { rpm: this.rpm, load, throttle, gear: this.gear, shifting: this.shiftLeft > 0, limiter };
  }

  private shift(gear: number) {
    this.shiftFrom = this.rpm; this.gear = gear; this.shiftLeft = this.spec.shiftTime;
  }
}
