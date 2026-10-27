// Facade walls of a loaded visual tile, as vertical rectangles in engine space, and their on-screen measure.
// A wall is a glTF object range with `building_id` and `facade_index` (docs/specs/tiles.md §3.1); batched nodes list
// them in `extras.objects` as index ranges ({start, count}) of the joined primitive, unbatched nodes carry them
// directly. The facade id is `<building_id>_<facade_index>`, the key of the facade approval manifest.
import * as THREE from 'three/webgpu';

export interface FacadeWall {
  id: string;
  /** Base corner where the horizontal tangent starts (engine x, z), the tangent and the outward normal (unit, xz). */
  ox: number; oz: number; tx: number; tz: number; nx: number; nz: number;
  width: number; y0: number; y1: number;
  /** Bounding sphere for the cheap distance cull. */
  cx: number; cy: number; cz: number; radius: number;
}

export interface WallView { facade_id: string; screen_fraction: number; distance_m: number; angle_deg: number }

const v = new THREE.Vector3();

/** Walls of every mesh under `model`, from current world matrices (call once the model is attached to the scene). */
export function extractWalls(model: THREE.Object3D): FacadeWall[] {
  const walls: FacadeWall[] = [];
  model.updateWorldMatrix(true, true);
  model.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    const data = mesh.userData as Record<string, unknown>;
    const position = mesh.geometry.getAttribute('position') as THREE.BufferAttribute | undefined;
    if (!position) return;
    const index = mesh.geometry.index;
    const total = index ? index.count : position.count;
    const ranges = Array.isArray(data.objects) ? data.objects as Record<string, unknown>[]
      : [{ ...data, start: 0, count: total }];
    const normalMatrix = new THREE.Matrix3().getNormalMatrix(mesh.matrixWorld);
    for (const r of ranges) {
      if (r.building_id == null || r.facade_index == null) continue;
      const start = Number(r.start), count = Number(r.count);
      if (!(count >= 3) || start < 0 || start + count > total) continue;
      const wall = fitWall(`${String(r.building_id)}_${String(r.facade_index)}`, mesh, position, index, start, count,
        Array.isArray(r.normal) ? r.normal as number[] : null, normalMatrix);
      if (wall) walls.push(wall);
    }
  });
  return walls;
}

function fitWall(id: string, mesh: THREE.Mesh, position: THREE.BufferAttribute, index: THREE.BufferAttribute | null,
  start: number, count: number, normal: number[] | null, normalMatrix: THREE.Matrix3): FacadeWall | null {
  const points: number[] = [];
  for (let k = start; k < start + count; k++) {
    v.fromBufferAttribute(position, index ? index.getX(k) : k).applyMatrix4(mesh.matrixWorld);
    points.push(v.x, v.y, v.z);
  }
  let nx: number, nz: number;
  if (normal) {
    v.set(normal[0], normal[1], normal[2]).applyMatrix3(normalMatrix);
    nx = v.x; nz = v.z;
  } else {
    // First triangle's winding normal.
    const ax = points[3] - points[0], ay = points[4] - points[1], az = points[5] - points[2];
    const bx = points[6] - points[0], by = points[7] - points[1], bz = points[8] - points[2];
    nx = ay * bz - az * by; nz = ax * by - ay * bx;
  }
  const len = Math.hypot(nx, nz);
  if (len < 1e-9) return null;
  nx /= len; nz /= len;
  const tx = -nz, tz = nx;
  let u0 = Infinity, u1 = -Infinity, y0 = Infinity, y1 = -Infinity, d = 0;
  for (let i = 0; i < points.length; i += 3) {
    const u = points[i] * tx + points[i + 2] * tz;
    u0 = Math.min(u0, u); u1 = Math.max(u1, u);
    y0 = Math.min(y0, points[i + 1]); y1 = Math.max(y1, points[i + 1]);
    d += points[i] * nx + points[i + 2] * nz;
  }
  d /= points.length / 3;
  const width = u1 - u0;
  if (!(width > 0.05) || !(y1 - y0 > 0.05)) return null;
  const ox = tx * u0 + nx * d, oz = tz * u0 + nz * d;
  return { id, ox, oz, tx, tz, nx, nz, width, y0, y1,
    cx: ox + tx * width / 2, cy: (y0 + y1) / 2, cz: oz + tz * width / 2, radius: Math.hypot(width, y1 - y0) / 2 };
}

// Scratch polygons for clipping: up to 4 + 1 (near) + 4 (viewport) vertices.
const poly = new Float64Array(32);
const next = new Float64Array(32);
const view = new THREE.Vector3();

/**
 * Screen fraction (0..1 of the viewport, occlusion ignored), distance from the camera to the nearest point of the wall
 * and the angle between the wall normal and the direction from the wall centre to the camera (0 = head-on). Null when the wall faces away,
 * lies beyond `maxDistance` or is off screen.
 */
export function measureWall(wall: FacadeWall, camera: THREE.PerspectiveCamera, maxDistance: number): WallView | null {
  const cam = camera.position;
  const dx = cam.x - wall.ox, dz = cam.z - wall.oz;
  if (dx * wall.nx + dz * wall.nz <= 0) return null; // behind the wall plane: the building interior side
  if (Math.hypot(cam.x - wall.cx, cam.y - wall.cy, cam.z - wall.cz) - wall.radius > maxDistance) return null;
  const u = Math.min(wall.width, Math.max(0, dx * wall.tx + dz * wall.tz));
  const y = Math.min(wall.y1, Math.max(wall.y0, cam.y));
  const px = wall.ox + wall.tx * u, pz = wall.oz + wall.tz * u;
  const distance = Math.hypot(cam.x - px, cam.y - y, cam.z - pz);
  if (distance > maxDistance) return null;
  // Viewing angle at the wall centre, so a long wall seen obliquely does not read as head-on at its nearest point.
  const toCentre = Math.hypot(cam.x - wall.cx, cam.y - wall.cy, cam.z - wall.cz);
  const cos = toCentre > 1e-6 ? ((cam.x - wall.cx) * wall.nx + (cam.z - wall.cz) * wall.nz) / toCentre : 1;
  const fraction = screenFraction(wall, camera);
  if (fraction <= 0) return null;
  return { facade_id: wall.id, screen_fraction: fraction, distance_m: distance,
    angle_deg: Math.acos(Math.min(1, Math.max(-1, cos))) * 180 / Math.PI };
}

function screenFraction(wall: FacadeWall, camera: THREE.PerspectiveCamera): number {
  // Corners in view space (camera looks down -Z), stored as (x, y, z) triples.
  const xs = [0, wall.width, wall.width, 0], ys = [wall.y0, wall.y0, wall.y1, wall.y1];
  for (let k = 0; k < 4; k++) {
    view.set(wall.ox + wall.tx * xs[k], ys[k], wall.oz + wall.tz * xs[k]).applyMatrix4(camera.matrixWorldInverse);
    poly[3 * k] = view.x; poly[3 * k + 1] = view.y; poly[3 * k + 2] = view.z;
  }
  // Clip against the near plane z <= -near.
  const near = -camera.near;
  let n = 0;
  for (let k = 0; k < 4; k++) {
    const a = 3 * k, b = 3 * ((k + 1) % 4);
    const ain = poly[a + 2] <= near, bin = poly[b + 2] <= near;
    if (ain) { next[3 * n] = poly[a]; next[3 * n + 1] = poly[a + 1]; next[3 * n + 2] = poly[a + 2]; n++; }
    if (ain !== bin) {
      const s = (near - poly[a + 2]) / (poly[b + 2] - poly[a + 2]);
      next[3 * n] = poly[a] + s * (poly[b] - poly[a]); next[3 * n + 1] = poly[a + 1] + s * (poly[b + 1] - poly[a + 1]);
      next[3 * n + 2] = near; n++;
    }
  }
  if (n < 3) return 0;
  // Perspective divide to NDC (x, y) pairs.
  const e = camera.projectionMatrix.elements;
  for (let k = 0; k < n; k++) {
    const x = next[3 * k], yv = next[3 * k + 1], z = next[3 * k + 2];
    const w = e[3] * x + e[7] * yv + e[11] * z + e[15];
    poly[2 * k] = (e[0] * x + e[4] * yv + e[8] * z + e[12]) / w;
    poly[2 * k + 1] = (e[1] * x + e[5] * yv + e[9] * z + e[13]) / w;
  }
  // Clip to the viewport square [-1, 1]^2, one edge at a time (Sutherland-Hodgman).
  for (const [axis, sign] of [[0, 1], [0, -1], [1, 1], [1, -1]] as const) {
    let m = 0;
    for (let k = 0; k < n; k++) {
      const a = 2 * k, b = 2 * ((k + 1) % n);
      const ain = sign * poly[a + axis] <= 1, bin = sign * poly[b + axis] <= 1;
      if (ain) { next[2 * m] = poly[a]; next[2 * m + 1] = poly[a + 1]; m++; }
      if (ain !== bin) {
        const s = (sign - poly[a + axis]) / (poly[b + axis] - poly[a + axis]);
        next[2 * m] = poly[a] + s * (poly[b] - poly[a]); next[2 * m + 1] = poly[a + 1] + s * (poly[b + 1] - poly[a + 1]); m++;
      }
    }
    n = m;
    if (n < 3) return 0;
    poly.set(next.subarray(0, 2 * n));
  }
  let area = 0;
  for (let k = 0; k < n; k++) {
    const a = 2 * k, b = 2 * ((k + 1) % n);
    area += poly[a] * poly[b + 1] - poly[b] * poly[a + 1];
  }
  return Math.abs(area) / 2 / 4; // the NDC viewport has area 4
}
