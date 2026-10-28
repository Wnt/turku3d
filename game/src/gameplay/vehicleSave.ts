// Last-used vehicle persistence, independent of the mission's vehicle ownership.
import { enToEngine, engineToEN, type V3 } from '../core/origin';
import type { SavedVehicle } from '../core/save';
import { VEHICLES } from '../physics/constants';
import type { PhysicsClient } from '../physics/client';
import type { VehicleState } from '../physics/protocol';
import { MAT } from '../shared/col/materials';
import type { Manifest } from '../world/manifest';
import { CAR, type RoadGraph } from './roadGraph';

export type VehicleSpot = V3 & { yaw: number };

export function captureVehicle(vehicle: VehicleState | undefined, seated: boolean, yaw: number,
  manifest: Manifest, previous?: SavedVehicle | null): SavedVehicle | null {
  if (!vehicle?.id) return null;
  if (previous?.id !== vehicle.id) previous = null;
  const p = engineToEN(vehicle.pos.x, vehicle.pos.y, vehicle.pos.z);
  const appearance = manifest.vehicles?.find(v => v.object_id === vehicle.id) ?? previous?.appearance;
  const modelAsset = appearance ? manifest.vehicle_models?.[`${appearance.model}/${appearance.color}`] ?? previous?.modelAsset : undefined;
  return { id: vehicle.id, kind: vehicle.kind ?? 'car', seated, E: p.E, N: p.N, H: p.H, yaw,
    ...(appearance ? { appearance, modelAsset } : {}) };
}

/** Projection and short intervals on every drivable segment, nearest first; also works beyond router.snap's range. */
export function roadSpots(graph: RoadGraph, near: V3): VehicleSpot[] {
  const spots: VehicleSpot[] = [];
  for (const edge of graph.edges) {
    if (!(edge.flags & CAR)) continue;
    for (let i = 0; i + 1 < edge.cumulative.length; i++) {
      const [x0, z0, x1, z1] = edge.points.subarray(i * 2, i * 2 + 4);
      const dx = x1 - x0, dz = z1 - z0, len = Math.hypot(dx, dz);
      if (!len) continue;
      const t = Math.min(1, Math.max(0, ((near.x - x0) * dx + (near.z - z0) * dz) / (len * len)));
      for (const f of [t, ...Array.from({ length: Math.ceil(len / 8) + 1 }, (_, j) => Math.min(1, j * 8 / len))]) {
        spots.push({ x: x0 + f * dx, z: z0 + f * dz,
          y: edge.heights ? edge.heights[i] + f * (edge.heights[i + 1] - edge.heights[i]) : near.y,
          yaw: Math.atan2(-dz, dx) });
      }
    }
  }
  return spots.sort((a, b) => Math.hypot(a.x - near.x, a.z - near.z) - Math.hypot(b.x - near.x, b.z - near.z));
}

/** Tests static support, water, enclosing buildings and chassis clearance without colliding with other saved cars. */
export async function vehicleGround(physics: Pick<PhysicsClient, 'raycast' | 'cameraCast'>,
  at: VehicleSpot, saved: SavedVehicle, spawnHeight: number, fallback: boolean): Promise<number | null> {
  const down = { x: 0, y: -1, z: 0 };
  const top = { x: at.x, y: at.y + 2, z: at.z };
  const hit = await physics.raycast(top, down, fallback ? 60 : 8)
    ?? (fallback ? await physics.raycast({ ...top, y: 500 }, down, 1000) : null);
  if (!hit || !hit.tile || hit.normal.y < 0.7 || ([MAT.building, MAT.roof, MAT.wall] as number[]).includes(hit.material)) return null;
  const y = hit.point.y;
  if (!fallback && Math.abs(at.y - spawnHeight - y) > 3) return null;
  const water = await physics.raycast({ ...top, y: y + 50 }, down, 100, 'water');
  if (water && water.point.y > y + 0.1) return null;
  const ceiling = await physics.raycast({ x: at.x, y: y + 0.3, z: at.z }, { x: 0, y: 1, z: 0 }, 100);
  if (ceiling && (([MAT.building, MAT.roof] as number[]).includes(ceiling.material) || ceiling.toi < 2)) return null;
  const h = saved.appearance ? { x: saved.appearance.length / 2, z: saved.appearance.width / 2 } : VEHICLES[saved.kind].halfExt;
  const forward = { x: Math.cos(at.yaw), y: 0, z: -Math.sin(at.yaw) };
  const side = { x: -forward.z, z: forward.x };
  for (const offset of [-h.z, 0, h.z]) {
    const origin = { x: at.x - forward.x * h.x + side.x * offset, y: y + spawnHeight,
      z: at.z - forward.z * h.x + side.z * offset };
    if (await physics.cameraCast(origin, forward, h.x * 2) < h.x * 2 - 0.05) return null;
    for (const end of [0, h.x * 2]) {
      const support = await physics.raycast({ x: origin.x + forward.x * end, y: y + 2, z: origin.z + forward.z * end }, down, 4);
      if (!support?.tile || support.normal.y < 0.7 || Math.abs(support.point.y - y) > 0.6) return null;
    }
  }
  return y;
}

/** Saved pose first; an invalid spot goes to the nearest usable road by the player, never silently into empty space. */
export async function restoreVehicleSpot(saved: SavedVehicle, player: V3, graph: RoadGraph,
  ground: (at: VehicleSpot, fallback: boolean) => Promise<number | null>, spawnHeight: number): Promise<VehicleSpot> {
  const at = { ...enToEngine(saved.E, saved.N, saved.H), yaw: saved.yaw };
  const y = await ground(at, false);
  if (y !== null) return { ...at, y: y + spawnHeight };
  for (const road of roadSpots(graph, player)) {
    const roadY = await ground(road, true);
    if (roadY !== null) return { ...road, y: roadY + spawnHeight };
  }
  throw new Error('No collision-backed drivable road is available to restore the saved vehicle');
}

/** A borrowed vehicle can outlive the draft tile that originally supplied its model and identity. */
export function keepSavedVehicle(manifest: Manifest, last: SavedVehicle | null | undefined) {
  if (!last?.appearance || !last.modelAsset) return;
  manifest.vehicles ??= [];
  if (!manifest.vehicles.some(v => v.object_id === last.id)) manifest.vehicles.push(last.appearance);
  manifest.vehicle_models ??= {};
  manifest.vehicle_models[`${last.appearance.model}/${last.appearance.color}`] ??= last.modelAsset;
}
