import { expect, test } from 'vitest';
import { CITIES } from '../../src/core/city';
import { enToEngine } from '../../src/core/origin';
import { FLEET, fleetModel, type FleetModel } from '../../src/actors/fleet/catalog';
import { exhibits, layoutShow, showRecords, showTile, withCarShows } from '../../src/world/carShow';
import type { ParkedVehicle } from '../../src/world/manifest';

const show = CITIES.turku.carShows![0];
type Box = { x: number; z: number; yaw: number; length: number; width: number };
const corners = (b: Box) => {
  const fx = Math.sin(b.yaw), fz = Math.cos(b.yaw), rx = -fz, rz = fx;
  return [[1, 1], [1, -1], [-1, -1], [-1, 1]].map(([f, r]) => ({ x: b.x + fx * f * b.length / 2 + rx * r * b.width / 2, z: b.z + fz * f * b.length / 2 + rz * r * b.width / 2 }));
};
/** Least gap between two boxes along any separating axis of either (negative: they overlap). */
function gap(a: Box, b: Box) {
  let best = -Infinity;
  for (const yaw of [a.yaw, b.yaw]) for (const [ax, az] of [[Math.sin(yaw), Math.cos(yaw)], [Math.cos(yaw), -Math.sin(yaw)]]) {
    const pa = corners(a).map(p => p.x * ax + p.z * az), pb = corners(b).map(p => p.x * ax + p.z * az);
    best = Math.max(best, Math.min(...pb) - Math.max(...pa), Math.min(...pa) - Math.max(...pb));
  }
  return best;
}
const boxes = () => layoutShow(show).stands.map(s => ({ x: s.x, z: s.z, yaw: s.yaw, length: s.exhibit.length, width: s.exhibit.width, id: s.exhibit.id }));
// The lot's generated slots as the served tile carries them (two of its 40, enough for the ground plane's level).
const lotSlot = (x: number, z: number, y: number): ParkedVehicle => ({ object_id: `${show.lot}:aisle:x:${x}`, source: show.lot, model: 'sedan',
  color: 'grey', x, y, z, yaw: 2.18, width: 1.85, height: 1.48, length: 4.55, clear_front_m: 3, clear_rear_m: 0, zone: 'centre-lot', occ: .2 });
const lot = [lotSlot(-185.9, 322.3, 4.7), lotSlot(-150.2, 318.5, 4.75), lotSlot(-170.0, 335.0, 4.85)];

test('the Turku show stands every catalog model and the trophy truck once, inside its area, apart and facing an aisle', () => {
  const { stands, left } = layoutShow(show);
  expect(left).toEqual([]);
  expect(stands.map(s => s.exhibit.id).sort()).toEqual([...FLEET.map(m => m.id), 'truck'].sort());
  const placed = boxes();
  for (let i = 0; i < placed.length; i++) for (let j = i + 1; j < placed.length; j++)
    expect(gap(placed[i], placed[j]), `${placed[i].id} / ${placed[j].id}`).toBeGreaterThanOrEqual(0.45);
  // Inside the rectangle: every corner projects within its edges.
  const [c0, c1, , c3] = show.area.map(([E, N]) => enToEngine(E, N));
  const L = Math.hypot(c1.x - c0.x, c1.z - c0.z), W = Math.hypot(c3.x - c0.x, c3.z - c0.z);
  for (const b of placed) for (const p of corners(b)) {
    const s = ((p.x - c0.x) * (c1.x - c0.x) + (p.z - c0.z) * (c1.z - c0.z)) / L, t = ((p.x - c0.x) * (c3.x - c0.x) + (p.z - c0.z) * (c3.z - c0.z)) / W;
    expect(s).toBeGreaterThanOrEqual(-1e-6); expect(s).toBeLessThanOrEqual(L + 1e-6);
    expect(t).toBeGreaterThanOrEqual(-1e-6); expect(t).toBeLessThanOrEqual(W + 1e-6);
  }
  // Drive out: the aisle-wide strip straight ahead of every exhibit is free of the others.
  for (const b of placed) {
    const fx = Math.sin(b.yaw), fz = Math.cos(b.yaw), ahead = (b.length + show.aisle - 0.2) / 2 + 0.1;
    const strip = { x: b.x + fx * ahead, z: b.z + fz * ahead, yaw: b.yaw, length: show.aisle - 0.2, width: b.width };
    for (const o of placed) if (o !== b) expect(gap(strip, o), `${b.id} drives out past ${o.id}`).toBeGreaterThan(0);
  }
  // Long vehicles fill the first row; the microcar closes the show.
  expect(stands.filter(s => s.row === 0).map(s => s.exhibit.id)).toContain('transit-bus');
  expect(stands.at(-1)!.exhibit.id).toBe('carla-micro-microlino');
});

test('a model added to the catalog joins the show; the others keep their ids', () => {
  const extra: FleetModel = { ...FLEET.find(m => m.id === 'golf7')!, id: 'test-coupe', name: 'Test Coupe' };
  const before = showRecords(show, lot), after = showRecords(show, lot, exhibits([...FLEET, extra]));
  expect(after.length).toBe(before.length + 1);
  expect(after.map(r => r.object_id)).toContain(`show:${show.lot}:test-coupe`);
  expect(new Set(after.map(r => r.object_id))).toEqual(new Set([...before.map(r => r.object_id), `show:${show.lot}:test-coupe`]));
});

test('show records are exact, always-present parked records every client derives alike', () => {
  const records = showRecords(show, lot);
  expect(showRecords(show, [...lot].reverse())).toEqual(records);
  for (const r of records) {
    expect(r.occ).toBeUndefined();
    expect(r.show).toBe(show.lot);
    if (r.model === 'truck') continue;
    // The record names its exact model and that model fits its own stand (catalog fitsSlot).
    expect(fleetModel(r.model, r.object_id, r)!.id).toBe(r.model);
    expect(r.object_id.length + 'park:'.length).toBeLessThanOrEqual(180);
    expect(r.y).toBeGreaterThan(4.5); expect(r.y).toBeLessThan(5.1);
  }
  expect(new Set(records.map(r => r.color)).size).toBeGreaterThan(4);
});

test('the show replaces its lot in its own tile only and keeps the street around it', () => {
  expect(showTile(show)).toBe('t0_-1_1');
  const kerb: ParkedVehicle = { ...lotSlot(-243, 407, 4.3), object_id: 'osm:way:1230162051:0:0:5', source: 'osm:way:1230162051' };
  const inside: ParkedVehicle = { ...lotSlot(-165, 330, 4.8), object_id: 'kerb:stray', source: 'kerb:osm' };
  const out = withCarShows('t0_-1_1', [...lot, kerb, inside]);
  expect(out.filter(r => r.source === show.lot)).toEqual([]);
  expect(out).toContainEqual(kerb);
  expect(out.find(r => r.object_id === 'kerb:stray')).toBeUndefined();
  expect(out.filter(r => r.show).length).toBe(FLEET.length + 1);
  // Another tile, or the show tile before its lot's slots arrive, keeps what it has.
  expect(withCarShows('t0_0_1', [...lot, kerb])).toEqual([...lot, kerb]);
  expect(withCarShows('t0_-1_1', [kerb])).toEqual([kerb]);
});
