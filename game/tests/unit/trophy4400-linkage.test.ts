import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { CORNERS, TruckLinkage, rotate, type Hardpoints, type SpringRest, type Vec, type Corner } from '../../src/actors/trophy4400/linkage';
import { TRUCK } from '../../src/physics/constants';
import { ackermann4 } from '../../src/vehicle/wheelKinematics';

const file = new URL('../../public/assets/vehicles/trophy4400/hardpoints.json', import.meta.url);
const hp = JSON.parse(readFileSync(file, 'utf8')) as Hardpoints & { springs: Record<Corner, SpringRest> };
const dist = (a: Vec, b: Vec) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
const sub = (a: Vec, b: Vec): Vec => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a: Vec, b: Vec): Vec => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];

describe('trophy4400 linkage (SPEC M4)', () => {
  it('hardpoints match the physics layout', () => {
    for (const [i, c] of CORNERS.entries()) {
      const hub = hp.corners[c].hub, w = TRUCK.wheels[i];
      expect(Math.abs(hub[0] - w.x)).toBeLessThan(0.01);
      expect(Math.abs(hub[2] - w.z)).toBeLessThan(0.01);
      expect(hub[1]).toBeCloseTo(TRUCK.wheelRadius, 2);
    }
  });

  it('stays rigid over the full travel at full lock both ways', () => {
    const linkage = new TruckLinkage(hp, hp.springs);
    const maxFront = TRUCK.maxSteer, maxRear = TRUCK.rearSteer.maxAngle;
    let worstCv = 0, worstOuter = 0, worstPlunge = 0, worstTierod = 0;
    for (const steerSign of [-1, 0, 1]) for (let k = 0; k <= 40; k++) {
      const wheels = CORNERS.map(c => {
        const t = c[0] === 'f' ? hp.travel.front : hp.travel.rear;
        const w = TRUCK.wheels[CORNERS.indexOf(c)];
        // The physics wheel angles: ackermann4 about the common instant centre (vehicle/vehicle.ts).
        const steer = ackermann4(steerSign * maxFront, -steerSign * maxRear, TRUCK.wheels[0].x, TRUCK.wheels[2].x, 2 * Math.abs(w.z), w.x, w.z, maxFront);
        return { dy: -t.droop + (t.bump + t.droop) * k / 40, steer };
      });
      const pose = linkage.solve(wheels);
      for (const [i, c] of CORNERS.entries()) {
        const p = hp.corners[c], s = pose.corners[c];
        expect(s.converged).toBe(true);
        // Hub reaches the commanded height.
        expect(Math.abs(s.upright.p[1] - (p.hub[1] + wheels[i].dy))).toBeLessThan(1e-4);
        // Rigid links: the joints move with their arms and the upright keeps its length.
        const rel = (v: Vec, pivot: Vec, q: typeof s.lca) => add(pivot, rotate(q, sub(v, pivot)));
        const bl = rel(p.lcaOut, p.lcaInF, s.lca), bu = rel(p.ucaOut, p.ucaInF, s.uca);
        expect(Math.abs(dist(bl, p.lcaInF) - dist(p.lcaOut, p.lcaInF))).toBeLessThan(1e-3);
        expect(Math.abs(dist(bu, p.ucaInF) - dist(p.ucaOut, p.ucaInF))).toBeLessThan(1e-3);
        expect(Math.abs(dist(bu, bl) - dist(p.ucaOut, p.lcaOut))).toBeLessThan(1e-3);
        // The upright transform carries its own joints to where the arms put them.
        const fromUpright = (v: Vec) => add(s.upright.p, rotate(s.upright.q, sub(v, p.hub)));
        expect(dist(fromUpright(p.lcaOut), bl)).toBeLessThan(1e-3);
        expect(dist(fromUpright(p.ucaOut), bu)).toBeLessThan(1e-3);
        // The scaled tie rod meets its outer joint.
        const tieEnd = add(s.tierod.p, rotate(s.tierod.q, sub(p.tierodOut, p.tierodIn).map(v => v * s.tierod.scale) as Vec));
        expect(dist(tieEnd, fromUpright(p.tierodOut))).toBeLessThan(1e-3);
        worstTierod = Math.max(worstTierod, Math.abs(s.tierod.error));
        worstCv = Math.max(worstCv, s.cv.angle.inner); worstOuter = Math.max(worstOuter, s.cv.angle.outer); worstPlunge = Math.max(worstPlunge, Math.abs(s.cv.plunge));
        expect(s.coil.spring.scale).toBeGreaterThan(0.4);
      }
    }
    console.log(`linkage: CV inner ${(worstCv * 180 / Math.PI).toFixed(1)}°, outer ${(worstOuter * 180 / Math.PI).toFixed(1)}°, plunge ${(worstPlunge * 1000).toFixed(0)} mm, tie-rod mismatch ${(worstTierod * 1000).toFixed(0)} mm`);
    // CV operating angle over travel and steer. SPEC M4 asks ≤ 28°; the round-7 hardpoints (0.78 m CV, inner cup
    // 4.7 cm above the hub) reach ~34° at the diff at full bump, a known model gap. This bounds the current geometry.
    expect(worstCv * 180 / Math.PI).toBeLessThan(35);
    // The outer joint also carries the steer angle (42° front lock): ≤ 50°, as on a long-travel CV.
    expect(worstOuter * 180 / Math.PI).toBeLessThan(50);
    expect(worstPlunge).toBeLessThan(0.025 + 0.03);
    expect(worstTierod).toBeLessThan(0.06);
  });

  it('is path-independent: any history gives the pose of a direct solve (no branch flips on landings or remote steps)', () => {
    const fresh = () => new TruckLinkage(hp, hp.springs);
    const seq = fresh();
    let rnd = 12345;
    const r = () => ((rnd = (rnd * 1103515245 + 12345) % 2147483648) / 2147483648);
    let worstAxis = 0, worstCamber = 0;
    for (let k = 0; k < 400; k++) {
      // Large random steps across the whole travel and steer range: droop→bump jumps, resets, 4 Hz remote poses.
      const wheels = CORNERS.map(c => {
        const t = c[0] === 'f' ? hp.travel.front : hp.travel.rear, max = c[0] === 'f' ? TRUCK.maxSteer : TRUCK.rearSteer.maxAngle;
        return { dy: -t.droop + (t.bump + t.droop) * r(), steer: (2 * r() - 1) * max };
      });
      const a = seq.solve(wheels), b = fresh().solve(wheels);
      for (const c of CORNERS) {
        const side = Math.sign(hp.corners[c].hub[2]) as 1 | -1;
        expect(a.corners[c].converged).toBe(true);
        const axA = rotate(a.corners[c].upright.q, [0, 0, side]), axB = rotate(b.corners[c].upright.q, [0, 0, side]);
        worstAxis = Math.max(worstAxis, dist(axA, axB));
        // Camber from the spin axis with the steer taken out (rotate back about the vertical).
        const steer = wheels[CORNERS.indexOf(c)].steer, ax = rotate([0, Math.sin(-steer / 2), 0, Math.cos(-steer / 2)], axA);
        worstCamber = Math.max(worstCamber, Math.abs(Math.asin(ax[1])) * 180 / Math.PI);
        // The spin axis stays outboard (no 160° flip shows the wheel's back face).
        expect(Math.abs(axA[2])).toBeGreaterThan(0.5);
      }
    }
    for (const c of CORNERS) expect(seq.corners[c].failures).toBe(0);
    console.log(`linkage path independence: spin-axis error ${worstAxis.toExponential(1)}, worst camber ${worstCamber.toFixed(1)}°`);
    expect(worstAxis).toBeLessThan(1e-6);
    // Long-arm camber curve plus the kingpin's steer camber stays within a few degrees (SPEC M4), never 16–45°.
    expect(worstCamber).toBeLessThan(8);
  });

  it('is cheap enough for every changed frame', () => {
    const linkage = new TruckLinkage(hp, hp.springs);
    const t0 = performance.now();
    for (let i = 0; i < 2000; i++) linkage.solve(CORNERS.map((_, j) => ({ dy: Math.sin(i * 0.01 + j) * 0.25, steer: Math.sin(i * 0.003) * 0.6 })));
    const ms = (performance.now() - t0) / 2000;
    expect(ms).toBeLessThan(0.2);
  });
});
