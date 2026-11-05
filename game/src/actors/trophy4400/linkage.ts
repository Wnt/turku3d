// Rigid long-travel linkage of the 4400 car (SPEC §2.4): from each corner's physics hub height and wheel steer, the
// transforms of its A-arms, upright, tie rod, coilover, bypass and CV axle, plus the rack slider offsets. Pure and
// three.js-free, so it runs in Node tests and in every local or remote truck alike. All points and transforms are in
// the GLB ground frame (+X forward, +Y up, left = −Z; hardpoints.json); node rotations are world deltas to compose
// onto each node's rest rotation (q = delta · rest).

export type Vec = [number, number, number];
export type Quat = [number, number, number, number];
export const CORNERS = ['fl', 'fr', 'rl', 'rr'] as const;
export type Corner = typeof CORNERS[number];

export interface CornerPoints {
  lcaInF: Vec; lcaInR: Vec; lcaOut: Vec; ucaInF: Vec; ucaInR: Vec; ucaOut: Vec; hub: Vec;
  tierodIn: Vec; tierodOut: Vec; coilTop: Vec; coilBot: Vec; bypassTop: Vec; bypassBot: Vec; cvIn: Vec; cvOut: Vec;
}
export interface Hardpoints {
  corners: Record<Corner, CornerPoints>;
  racks: { front: { l: Vec; r: Vec }; rear: { l: Vec; r: Vec } };
  travel: { front: { bump: number; droop: number }; rear: { bump: number; droop: number } };
}
/** Rest data that is not a hardpoint: the spring's top seat and its modelled length along its axis. */
export interface SpringRest { seat: Vec; length: number }

export interface CornerPose {
  /** Rotation about the LCA axis (rad) and the UCA swing angle; `converged` false if the solve failed (then held). */
  theta: number; phi: number; converged: boolean;
  lca: Quat; uca: Quat;
  upright: { p: Vec; q: Quat };
  /** Tie rod: origin at the inner joint on the rack, rotation delta and the Y scale that meets the outer joint;
   *  `error` is the length mismatch before that scale (rigid rack, physics Ackermann ≠ geometric Ackermann). */
  tierod: { p: Vec; q: Quat; scale: number; error: number };
  coil: { body: Quat; shaft: { p: Vec; q: Quat }; spring: { p: Vec; q: Quat; scale: number }; length: number };
  bypass: { body: Quat; shaft: { p: Vec; q: Quat }; length: number };
  /** `angle`: operating angles (rad) at the inner (diff) and outer (upright, steer included) joints. */
  cv: { inner: Quat; shaft: { q: Quat; scale: number }; outer: { p: Vec; q: Quat }; angle: { inner: number; outer: number }; plunge: number };
  /** This corner's own rack offset solution (m along the rack axis, + toward the right). */
  rack: number;
}

// ---- small vector / quaternion helpers (tuples, no allocation-free tricks needed at 4 corners per truck) ----
const add = (a: Vec, b: Vec): Vec => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const sub = (a: Vec, b: Vec): Vec => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const scale = (a: Vec, s: number): Vec => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a: Vec, b: Vec) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: Vec, b: Vec): Vec => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const len = (a: Vec) => Math.hypot(a[0], a[1], a[2]);
const norm = (a: Vec): Vec => scale(a, 1 / (len(a) || 1));
export const IDENTITY: Quat = [0, 0, 0, 1];
export function axisAngle(axis: Vec, angle: number): Quat {
  const s = Math.sin(angle / 2);
  return [axis[0] * s, axis[1] * s, axis[2] * s, Math.cos(angle / 2)];
}
export function qmul(a: Quat, b: Quat): Quat {
  return [a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1], a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
    a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3], a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2]];
}
export function rotate(q: Quat, v: Vec): Vec {
  const u: Vec = [q[0], q[1], q[2]], t = scale(cross(u, v), 2);
  return add(add(v, scale(t, q[3])), cross(u, t));
}
/** Shortest rotation taking unit vector a to unit vector b. */
export function between(a: Vec, b: Vec): Quat {
  const d = dot(a, b);
  if (d < -0.999999) { const axis = norm(Math.abs(a[0]) < 0.9 ? cross(a, [1, 0, 0]) : cross(a, [0, 1, 0])); return [axis[0], axis[1], axis[2], 0]; }
  const c = cross(a, b), w = 1 + d, n = Math.hypot(c[0], c[1], c[2], w);
  return [c[0] / n, c[1] / n, c[2] / n, w / n];
}
const around = (p: Vec, origin: Vec, q: Quat) => add(origin, rotate(q, sub(p, origin)));
const wrap = (a: number) => Math.atan2(Math.sin(a), Math.cos(a));

/** One corner's rigid double-wishbone solve, warm-started from its previous result. */
export class CornerLinkage {
  readonly front: boolean;
  readonly bump: number; readonly droop: number;
  private readonly axL: Vec; private readonly axU: Vec;
  private readonly cU: Vec; private readonly e1: Vec; private readonly e2: Vec; private readonly rU: number;
  private readonly upright0: Vec; private readonly uprightLength: number;
  private readonly rackAxis: Vec; private readonly tierodLength: number; private readonly tierodDir0: Vec;
  private readonly coilDir0: Vec; private readonly coilLength0: number;
  private readonly bypassDir0: Vec; private readonly bypassLength0: number;
  private readonly cvDir0: Vec; private readonly cvLength0: number;
  private theta = 0; private phi = 0; private s = 0;

  constructor(readonly p: CornerPoints, rack: { l: Vec; r: Vec }, travel: { bump: number; droop: number },
    private readonly spring: SpringRest) {
    this.front = p.hub[0] > 0;
    this.bump = travel.bump; this.droop = travel.droop;
    this.axL = norm(sub(p.lcaInR, p.lcaInF));
    this.axU = norm(sub(p.ucaInR, p.ucaInF));
    this.cU = add(p.ucaInF, scale(this.axU, dot(sub(p.ucaOut, p.ucaInF), this.axU)));
    const radial = sub(p.ucaOut, this.cU);
    this.rU = len(radial); this.e1 = norm(radial); this.e2 = cross(this.axU, this.e1);
    this.upright0 = sub(p.ucaOut, p.lcaOut); this.uprightLength = len(this.upright0);
    this.rackAxis = norm(sub(rack.r, rack.l));
    this.tierodLength = len(sub(p.tierodOut, p.tierodIn)); this.tierodDir0 = norm(sub(p.tierodOut, p.tierodIn));
    this.coilLength0 = len(sub(p.coilBot, p.coilTop)); this.coilDir0 = norm(sub(p.coilBot, p.coilTop));
    this.bypassLength0 = len(sub(p.bypassBot, p.bypassTop)); this.bypassDir0 = norm(sub(p.bypassBot, p.bypassTop));
    this.cvLength0 = len(sub(p.cvOut, p.cvIn)); this.cvDir0 = norm(sub(p.cvOut, p.cvIn));
  }

  /** The upright for an LCA angle and a steer angle: ball joints, UCA angle, upright rotation and the hub. */
  private upright(theta: number, steer: number) {
    const p = this.p, qL = axisAngle(this.axL, theta);
    const bl = around(p.lcaOut, p.lcaInF, qL);
    // UCA outer joint: on its swing circle and at the upright length from the lower joint: A cos φ + B sin φ = C.
    const d = sub(this.cU, bl), r = this.rU;
    const A = 2 * r * dot(d, this.e1), B = 2 * r * dot(d, this.e2), C = this.uprightLength ** 2 - dot(d, d) - r * r;
    const R = Math.hypot(A, B);
    if (!(R > 1e-9) || Math.abs(C / R) > 1) return null;
    const base = Math.atan2(B, A), spread = Math.acos(C / R);
    const a = wrap(base + spread), b = wrap(base - spread);
    const phi = Math.abs(wrap(a - this.phi)) <= Math.abs(wrap(b - this.phi)) ? a : b;
    const bu = add(this.cU, add(scale(this.e1, r * Math.cos(phi)), scale(this.e2, r * Math.sin(phi))));
    const kingpin = norm(sub(bu, bl));
    const q = qmul(axisAngle(kingpin, steer), between(norm(this.upright0), kingpin));
    const hub = add(bl, rotate(q, sub(p.hub, p.lcaOut)));
    return { bl, bu, phi, q, qL, hub };
  }

  /** Solves the corner for a hub displacement `dy` from static (+ up, clamped to the travel) and wheel `steer`. */
  solve(dy: number, steer: number): CornerPose {
    const p = this.p, target = p.hub[1] + Math.min(this.bump, Math.max(-this.droop, dy));
    let theta = this.theta, sol = this.upright(theta, steer), converged = false;
    // Newton on the hub height, numeric derivative; bisection fallback over ±1.2 rad.
    for (let i = 0; i < 10 && sol; i++) {
      const f = sol.hub[1] - target;
      if (Math.abs(f) < 1e-6) { converged = true; break; }
      const h = 1e-4, next = this.upright(theta + h, steer);
      if (!next) break;
      const slope = (next.hub[1] - sol.hub[1]) / h;
      if (Math.abs(slope) < 1e-6) break;
      theta -= f / slope; sol = this.upright(theta, steer);
    }
    if (!converged) {
      let lo = -1.2, hi = 1.2;
      const at = (t: number) => { const s = this.upright(t, steer); return s ? s.hub[1] - target : NaN; };
      let flo = at(lo);
      for (let i = 0; i < 60; i++) {
        const mid = (lo + hi) / 2, fm = at(mid);
        if (!Number.isFinite(fm)) break;
        if (Math.sign(fm) === Math.sign(flo)) { lo = mid; flo = fm; } else hi = mid;
        if (hi - lo < 1e-7) { theta = (lo + hi) / 2; sol = this.upright(theta, steer); converged = !!sol && Math.abs(sol.hub[1] - target) < 1e-5; break; }
      }
    }
    if (!sol || !converged) { theta = this.theta; sol = this.upright(theta, steer)!; }
    this.theta = theta; this.phi = sol.phi;
    const { q, qL, bl } = sol;
    const inUpright = (v: Vec) => add(bl, rotate(q, sub(v, p.lcaOut)));

    // Tie rod: the rack offset that keeps its length (closed-form quadratic, root nearest the last one).
    const tout = inUpright(p.tierodOut), w = sub(p.tierodIn, tout), wu = dot(w, this.rackAxis);
    const disc = wu * wu - (dot(w, w) - this.tierodLength ** 2);
    if (disc >= 0) {
      const r1 = -wu + Math.sqrt(disc), r2 = -wu - Math.sqrt(disc);
      this.s = Math.abs(r1 - this.s) <= Math.abs(r2 - this.s) ? r1 : r2;
    }

    const shock = (top: Vec, bot0: Vec, dir0: Vec, length0: number) => {
      const bot = around(bot0, p.lcaInF, qL), axis = sub(bot, top), length = len(axis);
      const body = between(dir0, scale(axis, 1 / length));
      return { body, shaft: { p: bot, q: body }, length, change: length - length0 };
    };
    const coil = shock(p.coilTop, p.coilBot, this.coilDir0, this.coilLength0);
    const bypass = shock(p.bypassTop, p.bypassBot, this.bypassDir0, this.bypassLength0);
    const springLength = Math.max(0.05, this.spring.length + coil.change);

    const cvOut = inUpright(p.cvOut), cvAxis = sub(cvOut, p.cvIn), cvLength = len(cvAxis), cvDir = scale(cvAxis, 1 / cvLength);
    const axle = rotate(q, [0, 0, Math.sign(p.hub[2]) || 1]), inner: Vec = [0, 0, Math.sign(p.hub[2]) || 1];
    const angle = { outer: Math.acos(Math.min(1, Math.abs(dot(cvDir, axle)))), inner: Math.acos(Math.min(1, Math.abs(dot(cvDir, inner)))) };

    return {
      theta, phi: sol.phi, converged,
      lca: qL, uca: axisAngle(this.axU, sol.phi),
      upright: { p: sol.hub, q },
      tierod: this.tierod(this.s, tout),
      coil: { body: coil.body, shaft: coil.shaft, length: coil.length,
        spring: { p: around(this.spring.seat, p.coilTop, coil.body), q: coil.body, scale: springLength / this.spring.length } },
      bypass: { body: bypass.body, shaft: bypass.shaft, length: bypass.length },
      cv: { inner: between(this.cvDir0, cvDir), shaft: { q: between(this.cvDir0, cvDir), scale: cvLength / this.cvLength0 },
        outer: { p: cvOut, q }, angle, plunge: cvLength - this.cvLength0 },
      rack: this.s,
    };
  }

  /** The tie rod for a (shared) rack offset `s` and the outer joint position. */
  tierod(s: number, tout: Vec): CornerPose['tierod'] {
    const tin = add(this.p.tierodIn, scale(this.rackAxis, s)), axis = sub(tout, tin), length = len(axis);
    return { p: tin, q: between(this.tierodDir0, scale(axis, 1 / length)), scale: length / this.tierodLength, error: length - this.tierodLength };
  }

  /** The outer tie-rod joint of the last solve (for the shared-rack correction). */
  tierodOuter(pose: CornerPose): Vec { return add(pose.upright.p, rotate(pose.upright.q, sub(this.p.tierodOut, this.p.hub))); }
}

export interface WheelInput { dy: number; steer: number }
export interface TruckPose { corners: Record<Corner, CornerPose>; rack: { front: number; rear: number } }

/** All four corners plus the two rigid racks (each takes the mean of its corners' offsets). */
export class TruckLinkage {
  readonly corners: Record<Corner, CornerLinkage>;
  constructor(readonly hardpoints: Hardpoints, springs: Record<Corner, SpringRest>) {
    const hp = hardpoints;
    this.corners = Object.fromEntries(CORNERS.map(c => {
      const front = c[0] === 'f';
      return [c, new CornerLinkage(hp.corners[c], front ? hp.racks.front : hp.racks.rear, front ? hp.travel.front : hp.travel.rear, springs[c])];
    })) as Record<Corner, CornerLinkage>;
  }

  /** `wheels` in corner order fl, fr, rl, rr (the physics wheel order). */
  solve(wheels: readonly WheelInput[]): TruckPose {
    const corners = {} as Record<Corner, CornerPose>;
    CORNERS.forEach((c, i) => { corners[c] = this.corners[c].solve(wheels[i]?.dy ?? 0, wheels[i]?.steer ?? 0); });
    const front = (corners.fl.rack + corners.fr.rack) / 2, rear = (corners.rl.rack + corners.rr.rack) / 2;
    for (const c of CORNERS) {
      const k = this.corners[c];
      corners[c].tierod = k.tierod(k.front ? front : rear, k.tierodOuter(corners[c]));
    }
    return { corners, rack: { front, rear } };
  }
}
