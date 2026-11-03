import { describe, expect, test } from 'vitest';
import { REACT, Reactions, type Base, type Threat } from '../../src/world/walkers/reactions';

const DT = 1 / 60;
const car = (x: number, vx: number, z = 0): Threat =>
  ({ x, y: 0, z, vx, vz: 0, car: { hx: Math.sign(vx) || 1, hz: 0, length: 4.2, width: 1.8 } });
const inside = (t: Threat, x: number, z: number) =>
  Math.abs(x - t.x) < t.car!.length / 2 + 0.2 && Math.abs(z - t.z) < t.car!.width / 2 + 0.25;

/** Runs a walker against threats that move with their velocity; `walk` moves the base along +Z. */
function run(base: Base, threats: Threat[], seconds: number, walk = 0) {
  const reactions = new Reactions();
  const frames: ReturnType<Reactions['apply']>[] = [];
  for (let i = 0; i < seconds / DT; i++) {
    const t = i * DT;
    const b = { ...base, z: base.z + walk * t, speed: walk };
    const moved = threats.map(th => ({ ...th, x: th.x + th.vx * t, z: th.z + th.vz * t }));
    const r = reactions.apply(b, moved, DT, t);
    frames.push(r);
    for (const th of moved) if (th.car && Math.hypot(th.vx, th.vz) >= REACT.hitSpeed && inside(th, r.x, r.z) && !r.down) {
      throw new Error(`walker inside a moving car at ${t.toFixed(2)} s`);
    }
  }
  return { reactions, frames };
}

describe('walker reactions to players', () => {
  test('a walker in the path of an approaching car gets out of the way before it arrives', () => {
    const { frames } = run({ id: 2, x: 0, y: 0, z: 0.3, yaw: 0, speed: 0 }, [car(-30, 12)], 4);
    expect(frames.some(f => f.down)).toBe(false);
    expect(Math.max(...frames.map(f => Math.abs(f.z)))).toBeGreaterThan(0.9 + 0.9);
    // Walks back onto its route once the car has passed.
    const end = run({ id: 2, x: 0, y: 0, z: 0.3, yaw: 0, speed: 0 }, [car(-30, 12)], 12).frames.at(-1)!;
    expect(Math.abs(end.z - 0.3)).toBeLessThan(0.05);
  });

  test('a fast car that is already on top of a walker knocks it down; it lies, gets up and catches up', () => {
    const { frames } = run({ id: 3, x: 0, y: 0, z: 0, yaw: 0, speed: 0 }, [car(-2.5, 14)], 14);
    const down = frames.findIndex(f => f.down);
    expect(down).toBeGreaterThanOrEqual(0);
    const peak = Math.max(...frames.map(f => f.tilt));
    expect(peak).toBeCloseTo(REACT.lean, 2);
    const lying = frames.filter(f => f.tilt > REACT.lean - 0.01).length * DT;
    expect(lying).toBeGreaterThan(REACT.down - 0.2);
    // Thrown along the car's motion, then still while lying.
    const fallen = frames[down + Math.round((REACT.fall + 1) / DT)];
    expect(fallen.x).toBeGreaterThan(1);
    const end = frames.at(-1)!;
    expect(end.down).toBe(false);
    expect(end.tilt).toBe(0);
    expect(Math.hypot(end.x, end.z)).toBeLessThan(Math.hypot(fallen.x, fallen.z));
  });

  test('a player car driving past along the road leaves pavement walkers alone', () => {
    const { frames } = run({ id: 4, x: 0, y: 0, z: 3.2, yaw: Math.PI / 2, speed: 1.4 }, [car(-40, 13)], 5);
    expect(Math.max(...frames.map(f => Math.abs(f.z - 3.2)))).toBeLessThan(0.05);
  });

  test('a parked player car on the footpath is walked around, not through', () => {
    const parked = car(0, 0, 0.2);
    const reactions = new Reactions();
    let closest = Infinity;
    for (let i = 0; i < 8 / DT; i++) {
      const t = i * DT, base: Base = { id: 6, x: -6 + 1.4 * t, y: 0, z: 0, yaw: Math.PI / 2, speed: 1.4 };
      const r = reactions.apply(base, [parked], DT, t);
      if (Math.abs(r.x) < 2.1) closest = Math.min(closest, Math.abs(r.z - 0.2));
    }
    expect(closest).toBeGreaterThan(0.9);
  });

  test('a walker sidesteps a player walking straight at it', () => {
    const reactions = new Reactions();
    let closest = Infinity;
    for (let i = 0; i < 6 / DT; i++) {
      const t = i * DT;
      const base: Base = { id: 8, x: 0, y: 0, z: -4 + 1.3 * t, yaw: 0, speed: 1.3 };
      const player: Threat = { x: 0, y: 0, z: 4 - 1.4 * t, vx: 0, vz: -1.4, car: null };
      const r = reactions.apply(base, [player], DT, t);
      closest = Math.min(closest, Math.hypot(r.x - player.x, r.z - player.z));
    }
    expect(closest).toBeGreaterThan(REACT.personal - 0.15);
  });

  test('no threats keep the deterministic position and drop the state', () => {
    const reactions = new Reactions();
    const r = reactions.apply({ id: 9, x: 5, y: 0, z: 7, yaw: 1, speed: 1.3 }, [car(200, 10)], DT, 0);
    expect([r.x, r.z, r.yaw, r.tilt]).toEqual([5, 7, null, 0]);
    expect(reactions.active).toBe(0);
  });
});
