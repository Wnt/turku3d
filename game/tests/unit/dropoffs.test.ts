import { afterEach, describe, expect, it, vi } from 'vitest';
import type { MissionHudState, RouteTarget, Router, WorldPoint } from '../../src/gameplay/contract';
import { addressSlug, type PoolAddress } from '../../src/gameplay/content/addresses';
import { DELIVERIES } from '../../src/gameplay/content/deliveries';
import { MARKET, PIZZERIA } from '../../src/gameplay/content/locations';
import { DropoffGuard, checkDropoff, type GroundRay } from '../../src/gameplay/missions/dropoffs';
import { MissionSystem, type MissionFrame } from '../../src/gameplay/missions/missions';
import { MAT } from '../../src/shared/col/materials';

/** Synthetic block outlines (rotated rectangles, x/z) around the four scripted addresses' buildings, in play order. */
const BLOCKS: WorldPoint[][] = [
  [[172.8, 120.4], [202.3, 100.2], [195.0, 89.4], [165.4, 109.6]],
  [[302.5, 138.0], [261.8, 79.5], [230.2, 101.5], [270.9, 160.0]],
  [[442.9, 53.3], [421.1, 37.6], [409.0, 54.3], [430.9, 70.0]],
  [[485.3, 372.7], [439.7, 403.2], [454.6, 425.5], [500.2, 394.9]],
].map(block => block.map(([x, z]) => ({ x, z })));

function inside(p: WorldPoint, poly: WorldPoint[]): boolean {
  let hit = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const a = poly[i], b = poly[j];
    if ((a.z > p.z) !== (b.z > p.z) && p.x < ((b.x - a.x) * (p.z - a.z)) / (b.z - a.z) + a.x) hit = !hit;
  }
  return hit;
}

function edgeDistance(p: WorldPoint, poly: WorldPoint[]): number {
  return Math.min(...poly.map((a, i) => {
    const b = poly[(i + 1) % poly.length];
    const dx = b.x - a.x, dz = b.z - a.z;
    const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.z - a.z) * dz) / (dx * dx + dz * dz)));
    return Math.hypot(p.x - a.x - t * dx, p.z - a.z - t * dz);
  }));
}

/** A pool entry for every scripted address, 8 m along x from its fallback, with its own street point and distances. */
const SCRIPTED_POOL: PoolAddress[] = DELIVERIES.map((d, i) => ({
  id: addressSlug(d.address), address: d.address, tile: 't0_0_0', point: { x: d.point.x + 8, z: d.point.z },
  street: { x: d.point.x + 8, z: d.point.z + 3 }, foot: 150 + 100 * i, car: 160 + 100 * i,
}));
const straight: Router = {
  route: (from, to) => [from, to],
  distance: path => Math.hypot(path[1].x - path[0].x, path[1].z - path[0].z),
};
const near = (p: WorldPoint): WorldPoint => ({ x: p.x + 1, z: p.z });

function game(pool: readonly PoolAddress[], router: Router = straight) {
  const missions = new MissionSystem({ router, addresses: pool, seed: 3 });
  const states: MissionHudState[] = [];
  missions.subscribe(s => states.push(s));
  const at = (p: WorldPoint, extra: Partial<MissionFrame> = {}, dt = 0.1) =>
    missions.update(dt, { position: p, interact: false, input: 'keyboard', vehicleId: null, ...extra });
  const choose = (point: WorldPoint, id: 'rent_permit' | 'take_order') => {
    at(near(point), { interact: true });
    at(near(point), { choose: id });
    at(near(point), { leave: true });
  };
  const pickUp = () => {
    if (missions.permit === 0) choose(MARKET.point, 'rent_permit');
    for (let t = 0; t < 30; t += 0.5) at(near(PIZZERIA.point), {}, 0.5);
    at(near(PIZZERIA.point), { interact: true });
    const quote = missions.shop!.options.find(o => o.id === 'take_order')!.quote!;
    at(near(PIZZERIA.point), { choose: 'take_order' });
    return quote;
  };
  const dropoff = (): RouteTarget => states[states.length - 1].targets.find(t => t.kind === 'dropoff')!;
  missions.begin();
  return { missions, at, pickUp, dropoff };
}

describe('scripted drop-offs', () => {
  it('fall back to sidewalk points outside every block, in front of their own building', () => {
    DELIVERIES.forEach((d, i) => {
      for (const block of BLOCKS) expect(inside(d.point, block), `${d.address} inside a block`).toBe(false);
      expect(edgeDistance(d.point, BLOCKS[i]), `${d.address} in front of its building`).toBeLessThan(4);
    });
    // The point before the fix sat on the Linnankatu 13 roof.
    expect(inside({ x: 191.0, z: 106.5 }, BLOCKS[0])).toBe(true);
  });

  it('resolve every scripted address through the pool: its drop-off, route distance and par', () => {
    const { missions, at, pickUp, dropoff } = game(SCRIPTED_POOL);
    DELIVERIES.forEach((d, i) => {
      const quote = pickUp();
      expect(quote).toMatchObject({ address: d.address, metres: SCRIPTED_POOL[i].foot });
      expect(missions.currentStop).toMatchObject({ id: d.id, point: SCRIPTED_POOL[i].point });
      expect(dropoff().point).toEqual(SCRIPTED_POOL[i].point);
      at(near(d.point), { interact: true }); // the fallback point is 8 m off: no hand-over there
      expect(missions.phase).toBe('carrying');
      at(near(SCRIPTED_POOL[i].point), { interact: true });
      expect(missions.phase).toBe('delivered');
    });
    expect(JSON.parse(missions.serialize()).delivered).toEqual([]); // the opening does not use up pool addresses
  });

  it('use the fallback point when the pool lacks the address', () => {
    const { missions, pickUp } = game(SCRIPTED_POOL.slice(1));
    expect(pickUp().address).toBe(DELIVERIES[0].address);
    expect(missions.currentStop.point).toEqual(DELIVERIES[0].point);
  });
});

/** A street at y 2 with one 20 m building (roof y 22) at x 0..20, z 0..20 and a 6 m plinth (y 5, more than
 *  the 2 m MAX_STREET_STEP_M above the street) at x 40..46. */
const world: GroundRay = async (x, z) => {
  if (x >= 0 && x <= 20 && z >= 0 && z <= 20) return { y: 22, material: MAT.roof };
  if (x >= 40 && x <= 46 && z >= 0 && z <= 6) return { y: 5, material: MAT.paving_slab };
  if (x > 200) return null; // collision not resident
  return { y: 2, material: MAT.sidewalk };
};

describe('drop-off ground check', () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it('accepts the street, rejects roofs and spots off the street level, and waits for collision', async () => {
    expect(await checkDropoff({ x: 10, z: 22.5 }, world)).toEqual({ ok: true, y: 2 });
    expect(await checkDropoff({ x: 10, z: 10 }, world)).toMatchObject({ ok: false, reason: expect.stringContaining('roof') });
    expect(await checkDropoff({ x: 43, z: 3 }, world)).toMatchObject({ ok: false, reason: expect.stringContaining('from the street') });
    expect(await checkDropoff({ x: 300, z: 0 }, world)).toBeNull();
  });

  it('moves a drop-off off a roof to the next candidate and never marks the roof', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    // The pool puts Linnankatu 13a on the roof; the scripted fallback is on the sidewalk in front of it.
    const pool: PoolAddress[] = [{ ...SCRIPTED_POOL[0], point: { x: 10, z: 10 }, street: { x: 10, z: 30 } }];
    const fallback = DELIVERIES[0].point;
    const { missions, at, pickUp, dropoff } = game(pool);
    const shifted: GroundRay = (x, z) => world(x - fallback.x + 10, z - fallback.z + 22.5);
    const guard = new DropoffGuard(async (x, z) => (x === 10 && z === 10 ? world(x, z) : shifted(x, z)),
      (id, p) => missions.relocateDropoff(id, p));
    pickUp();
    expect(await guard.ground(dropoff())).toBeNull();
    expect(warn).toHaveBeenCalledWith(expect.stringMatching(/Linnankatu 13a at 10, 10 rejected: inside a building footprint/));
    expect(dropoff().point).toEqual(fallback);
    expect(await guard.ground(dropoff())).toBe(2);
    at(near(fallback), { interact: true });
    expect(missions.phase).toBe('delivered');
  });

  it('runs through the street candidates and gives up without a marker when every one is rejected', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const onRoof: PoolAddress = { ...SCRIPTED_POOL[0], id: 'kattokatu-1', address: 'Kattokatu 1', point: { x: 5, z: 5 },
      street: { x: 6, z: 6 } };
    const router: Router = { ...straight, route: (_from, to) => [{ x: 7, z: 7 }, to] };
    const { missions, pickUp, dropoff } = game([onRoof], router);
    missions.restore(JSON.stringify({ ...JSON.parse(missions.serialize()), delivery: DELIVERIES.length }));
    const guard = new DropoffGuard(world, (id, p) => missions.relocateDropoff(id, p));
    pickUp();
    const seen: WorldPoint[] = [];
    for (let i = 0; i < 4; i++) {
      seen.push(dropoff().point);
      expect(await guard.ground(dropoff())).toBeNull();
    }
    expect(seen).toEqual([{ x: 5, z: 5 }, { x: 6, z: 6 }, { x: 7, z: 7 }, { x: 7, z: 7 }]);
    expect(warn).toHaveBeenLastCalledWith(expect.stringContaining('no street-level candidate left'));
    const calls = warn.mock.calls.length;
    expect(await guard.ground(dropoff())).toBeNull();
    expect(warn.mock.calls.length).toBe(calls);
  });
});
