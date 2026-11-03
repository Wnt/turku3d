// Local, cosmetic reactions of drawn walkers to players: sidestep a player who walks into them, hurry or dive out
// of the path of a player's car, and fall down when one hits them, then get up and catch up with their route.
// The deterministic population (population.ts) still decides where every walker is: a reaction is a displacement
// from that position that decays back to zero, so births, routes and what traffic yields to never change. Every
// client reacts to every player it knows of (local and remote), so multiplayer clients see nearly the same thing.

/** A player as walkers see it: feet position and velocity, plus the footprint of the vehicle it drives. */
export interface Threat {
  x: number; y: number; z: number; vx: number; vz: number;
  /** Unit heading and footprint of the player's vehicle; null on foot. */
  car: { hx: number; hz: number; length: number; width: number } | null;
  /** Centre-to-centre clearance kept from this one on foot (m); REACT.personal for a player. */
  personal?: number;
}

/** The walker this frame, from the population: position, facing, and walking speed (0 while idle). */
export interface Base { id: number; x: number; y: number; z: number; yaw: number; speed: number }

export interface Reaction {
  x: number; z: number;
  /** Facing while the reaction moves the walker (radians, the population's convention), else null. */
  yaw: number | null;
  /** Extra metres walked by the reaction (advances the walk cycle). */
  extra: number;
  /** The reaction moves the walker although its route pauses (play the walk clip). */
  moving: boolean;
  /** Knock-down: lean angle from upright (radians) towards `tiltYaw`; lying walkers play the idle clip. */
  tilt: number; tiltYaw: number;
  down: boolean;
}

export const REACT = {
  /** Centre-to-centre clearance kept from a player on foot (m) and the look-ahead for meeting one (s). */
  personal: 0.85, notice: 1.6, step: 1.5,
  /** Clearance beyond the side of a car's path (m) and how far ahead its path counts (s of travel). */
  clear: 1.1, warn: 1.8,
  /** Sideways speed when a car is still coming (m/s) and when it is about to arrive (a dive). */
  hurry: 3.0, dive: 5.5, diveWithin: 0.7,
  /** Speed of walking back onto the route (m/s) and the largest dodge offset (m). */
  back: 1.1, maxOffset: 3.5,
  /** A car at least this fast knocks a walker down (m/s); slower ones push it aside. */
  hitSpeed: 2.5,
  /** Knock-down timeline (s): falling, lying, getting up. Largest lean (rad) and the lift that keeps a lying body
   *  above the ground (m). */
  fall: 0.45, down: 4, rise: 0.9, lean: 1.42, lift: 0.12,
  /** Panic: a knock-down or a dive makes walkers within `panicRadius` m hurry `flee` m away from it for `panic` s. */
  panic: 3, panicRadius: 12, flee: 3,
  /** A standing walker turns to look at a player on foot this close (m). */
  look: 2.5,
  /** A state unseen this long is dropped (s). */
  forget: 3,
} as const;

interface State {
  dx: number; dz: number; vx: number; vz: number; extra: number;
  /** Seconds since a knock-down, or -1. */
  knocked: number;
  /** World position and slide velocity while knocked down, and the direction of the fall. */
  ax: number; az: number; tx: number; tz: number; fallYaw: number; yaw: number;
  seen: number;
}

const ease = (u: number) => u * u * (3 - 2 * u);

export class Reactions {
  private states = new Map<number, State>();
  /** Recent knock-downs and dives (world position, frame time in s). */
  private alarms: { x: number; z: number; t: number }[] = [];

  /** Walkers displaced or knocked down right now. */
  get active() { let n = 0; this.states.forEach(s => { if (s.knocked >= 0 || s.dx || s.dz || s.vx || s.vz) n++; }); return n; }

  /** The reaction of one drawn walker at frame time `now` (s); `dt` is the frame step. */
  apply(base: Base, threats: readonly Threat[], dt: number, now: number): Reaction {
    let s = this.states.get(base.id);
    const fx = Math.sin(base.yaw), fz = Math.cos(base.yaw);
    if (!s) {
      if (!this.anyNear(threats, base)) return this.result(base.x, base.z, null, 0, false, 0, 0, false);
      s = { dx: 0, dz: 0, vx: 0, vz: 0, extra: 0, knocked: -1, ax: 0, az: 0, tx: 0, tz: 0, fallYaw: 0, yaw: base.yaw, seen: now };
      this.states.set(base.id, s);
    }
    s.seen = now;
    if (s.knocked >= 0) return this.knockedDown(s, base, dt);
    const px = base.x + s.dx, pz = base.z + s.dz;
    // The most urgent car decides the dodge; players on foot add a sidestep.
    let targetX = 0, targetZ = 0, speed: number = REACT.back, threatened = false, urgency = Infinity;
    for (const t of threats) {
      if (!t.car) continue;
      const travel = Math.sqrt((t.vx) ** 2 + (t.vz) ** 2);
      const rx = px - t.x, rz = pz - t.z;
      const along = rx * t.car.hx + rz * t.car.hz, across = -rx * t.car.hz + rz * t.car.hx;
      const inside = Math.abs(along) < t.car.length / 2 + 0.25 && Math.abs(across) < t.car.width / 2 + 0.3;
      if (inside && travel >= REACT.hitSpeed && Math.abs(t.y - base.y) < 2) {
        this.knock(s, px, pz, t, across >= 0 ? 1 : -1, base.yaw);
        return this.knockedDown(s, base, 0);
      }
      // The footprint swept along the car's motion relative to the walker: a car coming at it, a reversing car, or
      // a parked one the walker is about to walk into.
      const rvx = t.vx - fx * base.speed, rvz = t.vz - fz * base.speed, closing = Math.sqrt((rvx) ** 2 + (rvz) ** 2);
      const ux = closing > 0.3 ? rvx / closing : t.car.hx, uz = closing > 0.3 ? rvz / closing : t.car.hz;
      const hu = Math.abs(t.car.hx * ux + t.car.hz * uz), hn = Math.abs(-t.car.hx * uz + t.car.hz * ux);
      const front = t.car.length / 2 * hu + t.car.width / 2 * hn, half = t.car.length / 2 * hn + t.car.width / 2 * hu;
      const a = rx * ux + rz * uz, c = -rx * uz + rz * ux;
      const reach = closing < 0.5 ? 0.3 : closing * REACT.warn + 1;
      if (a < -front || a > front + reach || Math.abs(c) > half + REACT.clear || Math.abs(t.y - base.y) > 2) continue;
      const arrive = Math.max(0, a - front) / Math.max(0.5, closing);
      if (arrive >= urgency) continue;
      urgency = arrive; threatened = true;
      const side = Math.abs(c) > 0.05 ? Math.sign(c) : (base.id & 1 ? 1 : -1);
      const need = side * (half + REACT.clear) - c;
      targetX = s.dx - uz * need; targetZ = s.dz + ux * need;
      speed = travel < 1 ? REACT.step : arrive < REACT.diveWithin ? REACT.dive : REACT.hurry;
      if (speed === REACT.dive) this.alarm(px, pz, now);
    }
    // No car to dodge: hurry away from a recent knock-down or dive nearby.
    if (urgency === Infinity) for (const a of this.alarms) {
      const rx = px - a.x, rz = pz - a.z, d = Math.sqrt((rx) ** 2 + (rz) ** 2);
      if (now - a.t > REACT.panic || d > REACT.panicRadius || d < 0.3) continue;
      threatened = true; urgency = now - a.t;
      targetX = s.dx + rx / d * REACT.flee; targetZ = s.dz + rz / d * REACT.flee; speed = REACT.hurry;
      break;
    }
    for (const t of threats) {
      if (t.car || Math.abs(t.y - base.y) > 2) continue;
      const rx = px - t.x, rz = pz - t.z, d = Math.sqrt((rx) ** 2 + (rz) ** 2);
      if (d > 4) continue;
      // Closest approach of the player relative to the walker over the next `notice` seconds.
      const wx = fx * base.speed + s.vx, wz = fz * base.speed + s.vz;
      const vx = t.vx - wx, vz = t.vz - wz, v2 = vx * vx + vz * vz;
      const tc = v2 > 1e-4 ? Math.max(0, Math.min(REACT.notice, (rx * vx + rz * vz) / v2)) : 0;
      const cx = rx - vx * tc, cz = rz - vz * tc, closest = Math.sqrt((cx) ** 2 + (cz) ** 2);
      const personal = t.personal ?? REACT.personal;
      if (closest >= personal && d >= personal) continue;
      threatened = true;
      // Step sideways relative to the walker's own heading, to the side the player passes away from.
      const nx = -fz, nz = fx;
      const side = Math.abs(cx * nx + cz * nz) > 0.05 ? Math.sign(cx * nx + cz * nz) : (base.id & 1 ? 1 : -1);
      const need = personal + 0.1 - Math.min(closest, d);
      targetX += nx * side * need; targetZ += nz * side * need;
      if (urgency === Infinity) { targetX += s.dx; targetZ += s.dz; urgency = tc; }
      speed = Math.max(speed, REACT.step);
    }
    if (!threatened) { targetX = 0; targetZ = 0; speed = REACT.back; }
    const offset = Math.sqrt((targetX) ** 2 + (targetZ) ** 2);
    if (offset > REACT.maxOffset) { targetX *= REACT.maxOffset / offset; targetZ *= REACT.maxOffset / offset; }
    // Move towards the target at up to `speed`, easing in over a fifth of a second.
    const ex = targetX - s.dx, ez = targetZ - s.dz, e = Math.sqrt((ex) ** 2 + (ez) ** 2);
    const v = Math.min(speed, e / 0.2), k = Math.min(1, dt * 10);
    s.vx += ((e > 1e-4 ? ex / e * v : 0) - s.vx) * k; s.vz += ((e > 1e-4 ? ez / e * v : 0) - s.vz) * k;
    const step = Math.sqrt((s.vx) ** 2 + (s.vz) ** 2) * dt;
    if (step > e) { s.dx = targetX; s.dz = targetZ; } else { s.dx += s.vx * dt; s.dz += s.vz * dt; }
    s.extra += step;
    const totalX = fx * base.speed + s.vx, totalZ = fz * base.speed + s.vz, total = Math.sqrt((totalX) ** 2 + (totalZ) ** 2);
    const reacting = Math.sqrt((s.vx) ** 2 + (s.vz) ** 2) > 0.5;
    let yaw = reacting && total > 0.3 ? Math.atan2(totalX, totalZ) : null;
    // Standing still, a walker turns to look at the nearest player on foot who comes up close.
    if (!reacting && base.speed === 0) {
      let nearest: number = REACT.look;
      for (const t of threats) {
        if (t.car || t.personal !== undefined || Math.abs(t.y - base.y) > 2) continue;
        const d = Math.sqrt((t.x - px) ** 2 + (t.z - pz) ** 2);
        if (d < nearest && d > 0.2) { nearest = d; yaw = Math.atan2(t.x - px, t.z - pz); }
      }
    }
    const result = this.result(base.x + s.dx, base.z + s.dz, yaw, s.extra, reacting, 0, 0, false);
    // A settled state stays (pruned once unseen): recreating it every frame for crowded walkers was pure churn.
    if (!threatened && s.dx * s.dx + s.dz * s.dz < 1e-4 && s.vx * s.vx + s.vz * s.vz < 0.0025) { s.dx = s.dz = s.vx = s.vz = 0; }
    return result;
  }

  /** One result object, reused: callers read it before the next apply(). */
  private out: Reaction = { x: 0, z: 0, yaw: null, extra: 0, moving: false, tilt: 0, tiltYaw: 0, down: false };
  private result(x: number, z: number, yaw: number | null, extra: number, moving: boolean, tilt: number, tiltYaw: number, down: boolean) {
    const o = this.out;
    o.x = x; o.z = z; o.yaw = yaw; o.extra = extra; o.moving = moving; o.tilt = tilt; o.tiltYaw = tiltYaw; o.down = down;
    return o;
  }

  private anyNear(threats: readonly Threat[], base: Base) {
    for (let i = 0; i < threats.length; i++) if (near(threats[i], base)) return true;
    for (const a of this.alarms) {
      const dx = a.x - base.x, dz = a.z - base.z;
      if (dx * dx + dz * dz < REACT.panicRadius * REACT.panicRadius) return true;
    }
    return false;
  }

  /** Drops the states of walkers no longer drawn. */
  prune(now: number) {
    this.states.forEach((s, id) => { if (now - s.seen > REACT.forget) this.states.delete(id); });
    if (this.alarms.length) this.alarms = this.alarms.filter(a => now - a.t <= REACT.panic && a.t <= now);
  }

  private alarm(x: number, z: number, now: number) {
    if (!this.alarms.some(a => now - a.t < 1 && Math.sqrt((a.x - x) ** 2 + (a.z - z) ** 2) < 5)) this.alarms.push({ x, z, t: now });
  }

  clear() { this.states.clear(); this.alarms = []; }

  private knock(s: State, x: number, z: number, t: Threat, side: number, yaw: number) {
    const car = t.car!;
    // Thrown along the car's motion and away from its centreline.
    s.tx = t.vx * 0.55 - car.hz * side * 1.5; s.tz = t.vz * 0.55 + car.hx * side * 1.5;
    s.ax = x; s.az = z; s.knocked = 0; s.fallYaw = Math.atan2(s.tx, s.tz); s.yaw = yaw; s.vx = s.vz = 0;
    this.alarm(x, z, s.seen);
  }

  private knockedDown(s: State, base: Base, dt: number): Reaction {
    s.knocked += dt;
    const slide = Math.sqrt((s.tx) ** 2 + (s.tz) ** 2);
    if (slide > 0) {
      const slower = Math.max(0, slide - 7 * dt) / slide;
      s.ax += s.tx * dt; s.az += s.tz * dt; s.tx *= slower; s.tz *= slower;
    }
    s.dx = s.ax - base.x; s.dz = s.az - base.z;
    const k = s.knocked, up = REACT.fall + REACT.down;
    const lean = k < REACT.fall ? ease(k / REACT.fall) : k < up ? 1 : 1 - ease(Math.min(1, (k - up) / REACT.rise));
    if (k >= up + REACT.rise) s.knocked = -1;
    return this.result(s.ax, s.az, s.yaw, s.extra, false, lean * REACT.lean, s.fallYaw, true);
  }
}

function near(t: Threat, base: Base): boolean {
  const reach = t.car ? Math.sqrt((t.vx) ** 2 + (t.vz) ** 2) * REACT.warn + t.car.length + REACT.clear + 2 : 4;
  return Math.abs(t.x - base.x) < reach && Math.abs(t.z - base.z) < reach;
}
