import type { PreparedScene } from '../../world/preparedScene';
import type { Lamp } from './lampField';
import { Footprint } from './tileScan';
import { CORONA_RADIUS } from './tileLights';

export interface PreparedLighting { id: string; lamps: Lamp[]; footprint: Footprint | null }

/** Explicit source polygons, never a reconstruction from rendered building triangles. */
function footprint(scene: PreparedScene): Footprint | null {
  const polygons = scene.lightOccluders;
  if (!polygons?.length) return null;
  const points = polygons.flatMap(p => p.positions);
  const xs = points.map(p => p[0]), zs = points.map(p => p[1]);
  const grid = new Footprint(Math.floor(Math.min(...xs) / 2) * 2, Math.floor(Math.min(...zs) / 2) * 2,
    Math.max(...xs), Math.max(...zs));
  const min = new Float32Array(grid.cells.length).fill(Infinity), max = new Float32Array(grid.cells.length).fill(-Infinity);
  grid.heightRanges = { min, max }; grid.revision = scene.release;
  for (const polygon of polygons) {
    const p = polygon.positions, C = Footprint.CELL;
    const x0 = Math.max(0, Math.floor((Math.min(...p.map(p => p[0])) - grid.x0) / C));
    const x1 = Math.min(grid.nx - 1, Math.ceil((Math.max(...p.map(p => p[0])) - grid.x0) / C));
    const z0 = Math.max(0, Math.floor((Math.min(...p.map(p => p[1])) - grid.z0) / C));
    const z1 = Math.min(grid.nz - 1, Math.ceil((Math.max(...p.map(p => p[1])) - grid.z0) / C));
    for (let z = z0; z <= z1; z++) for (let x = x0; x <= x1; x++) {
      const wx = grid.x0 + (x + .5) * C, wz = grid.z0 + (z + .5) * C;
      let inside = false;
      for (let i = 0, j = p.length - 1; i < p.length; j = i++) {
        const [ax, az] = p[i], [bx, bz] = p[j];
        if ((az > wz) !== (bz > wz) && wx < (bx - ax) * (wz - az) / (bz - az) + ax) inside = !inside;
      }
      if (!inside) continue;
      const at = z * grid.nx + x; grid.cells[at] = 1;
      min[at] = Math.min(min[at], polygon.minY); max[at] = Math.max(max[at], polygon.maxY);
    }
  }
  return grid;
}

/** Preserve photometric inputs as floats. Corona display gain is separate from physical pool/field intensity. */
export function prepareLighting(scene: PreparedScene): PreparedLighting {
  const fixtures = new Map(scene.fixtures.map(f => [f.id, f]));
  const lamps: Lamp[] = scene.emitters.map(e => {
    const fixture = fixtures.get(e.fixtureId)!;
    const category = e.category ?? 'street', [x, y, z] = e.position;
    // A corona is an optical display cue, not the area of an emitter; 16 cd maps to one HDR display unit.
    const corona = e.intensity / 16;
    return { key: e.id, x: fixture.position[0], y: fixture.position[1], z: fixture.position[2],
      yaw: fixture.yaw, scale: 1, post: false, kind: category === 'wall' ? 'wall' : 'street',
      light: { x, y, z, radius: CORONA_RADIUS[category], r: e.color[0] * corona, g: e.color[1] * corona, b: e.color[2] * corona, day: 0 },
      photometry: { color: e.color, intensity: e.intensity, range: e.range, base: fixture.position[1], ...(e.aperture ? { aperture: e.aperture } : {}) } };
  });
  return { id: scene.id, lamps, footprint: footprint(scene) };
}
