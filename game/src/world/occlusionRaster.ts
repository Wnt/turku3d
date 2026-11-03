/** Conservative software occlusion for placed models. No three.js: it runs in the visibility worker and in tests.
 *
 * Occluders are generator boxes strictly inside opaque LoD2 volumes (pipeline/turku3d/coarse/occluders.py).
 * A pixel only receives an occluder depth when the pixel square lies wholly inside the projected shape, and that
 * depth is the farthest depth of the shape, so stored depths never claim more than the box hides. Anything that
 * crosses the near plane fails open (never occludes, never gets culled). */

export const GRID_W = 128, GRID_H = 64;
/** Per-slot record in `slots`: min xyz, max xyz, view distance, flags. */
export const SLOT_STRIDE = 8;
/** Per-box record in `boxes`: eight world corners. Each slot owns MAX_SLOT_BOXES consecutive records. */
export const BOX_STRIDE = 24, MAX_SLOT_BOXES = 8;
export const ACTIVE = 1;
/** Occluders farther than this, or beyond their building's own view distance, are not rasterised. */
export const OCCLUDER_RANGE = 450;
export const MAX_RASTER_BOXES = 192;

export interface VisibilityViews {
  slots: Float32Array;      // SLOT_STRIDE per slot
  boxCounts: Uint8Array;    // boxes used by each slot
  boxes: Float32Array;      // BOX_STRIDE * MAX_SLOT_BOXES per slot
  slotCount: number;        // high-water mark
}

/** Column-major view-projection; returns clip x, y, w for one point. */
function project(m: ArrayLike<number>, x: number, y: number, z: number, out: Float64Array, at: number) {
  const w = m[3] * x + m[7] * y + m[11] * z + m[15];
  out[at] = ((m[0] * x + m[4] * y + m[8] * z + m[12]) / w + 1) * GRID_W / 2;
  out[at + 1] = (1 - (m[1] * x + m[5] * y + m[9] * z + m[13]) / w) * GRID_H / 2;
  out[at + 2] = w;
}

const FACES = [0, 1, 3, 2, 4, 6, 7, 5, 0, 4, 5, 1, 2, 3, 7, 6, 0, 2, 6, 4, 1, 5, 7, 3];

export class DepthGrid {
  readonly depth = new Float32Array(GRID_W * GRID_H);
  private points = new Float64Array(24);
  private hull = new Float64Array(18);
  private quad = new Float64Array(12);

  clear() { this.depth.fill(Infinity); }

  /** Fill pixels wholly inside convex polygon `poly` (n xy points, any winding) with min(existing, depth). */
  private fill(poly: Float64Array, n: number, depth: number) {
    let area = 0, left = Infinity, right = -Infinity, top = Infinity, bottom = -Infinity;
    for (let i = 0; i < n; i++) {
      const x = poly[i * 2], y = poly[i * 2 + 1], j = (i + 1) % n;
      area += x * poly[j * 2 + 1] - poly[j * 2] * y;
      left = Math.min(left, x); right = Math.max(right, x); top = Math.min(top, y); bottom = Math.max(bottom, y);
    }
    if (Math.abs(area) < 1e-6) return;
    const sign = area > 0 ? 1 : -1;
    const x0 = Math.max(0, Math.ceil(left)), x1 = Math.min(GRID_W, Math.floor(right));
    const y0 = Math.max(0, Math.ceil(top)), y1 = Math.min(GRID_H, Math.floor(bottom));
    for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
      let inside = true;
      // A convex region contains the pixel square when it contains all four corners.
      for (let i = 0; inside && i < n; i++) {
        const ax = poly[i * 2], ay = poly[i * 2 + 1], j = (i + 1) % n, ex = poly[j * 2] - ax, ey = poly[j * 2 + 1] - ay;
        for (let c = 0; c < 4; c++) {
          const px = x + (c & 1), py = y + (c >> 1);
          if ((ex * (py - ay) - ey * (px - ax)) * sign < 1e-9) { inside = false; break; }
        }
      }
      if (inside) { const at = y * GRID_W + x; if (depth < this.depth[at]) this.depth[at] = depth; }
    }
  }

  /** `corners`: 8 world points (bit 0 = x side, bit 1 = y side, bit 2 = z side) starting at `offset`. */
  rasterBox(vp: ArrayLike<number>, near: number, corners: ArrayLike<number>, offset = 0) {
    const p = this.points;
    let far = 0;
    for (let i = 0; i < 8; i++) {
      project(vp, corners[offset + i * 3], corners[offset + i * 3 + 1], corners[offset + i * 3 + 2], p, i * 3);
      if (!(p[i * 3 + 2] > near) || !Number.isFinite(p[i * 3] + p[i * 3 + 1])) return false;
      far = Math.max(far, p[i * 3 + 2]);
    }
    // Silhouette (convex hull) at the box's farthest depth: seam-free and conservative.
    const n = this.convexHull();
    if (n >= 3) this.fill(this.hull, n, far);
    // Each face at its own farthest corner tightens the depth where a face alone covers the pixel.
    for (let f = 0; f < 24; f += 4) {
      let faceFar = 0;
      for (let k = 0; k < 4; k++) {
        const c = FACES[f + k] * 3;
        this.quad[k * 2] = p[c]; this.quad[k * 2 + 1] = p[c + 1]; faceFar = Math.max(faceFar, p[c + 2]);
      }
      this.fill(this.quad, 4, faceFar);
    }
    return true;
  }

  private convexHull() {
    const p = this.points, order = [0, 1, 2, 3, 4, 5, 6, 7].sort((a, b) => p[a * 3] - p[b * 3] || p[a * 3 + 1] - p[b * 3 + 1]);
    const out: number[] = [];
    const cross = (o: number, a: number, b: number) =>
      (p[a * 3] - p[o * 3]) * (p[b * 3 + 1] - p[o * 3 + 1]) - (p[a * 3 + 1] - p[o * 3 + 1]) * (p[b * 3] - p[o * 3]);
    for (const i of order) { while (out.length >= 2 && cross(out[out.length - 2], out[out.length - 1], i) <= 0) out.pop(); out.push(i); }
    const lower = out.length + 1;
    for (let k = order.length - 2; k >= 0; k--) {
      const i = order[k];
      while (out.length >= lower && cross(out[out.length - 2], out[out.length - 1], i) <= 0) out.pop();
      out.push(i);
    }
    out.pop();
    for (let k = 0; k < out.length; k++) { this.hull[k * 2] = p[out[k] * 3]; this.hull[k * 2 + 1] = p[out[k] * 3 + 1]; }
    return out.length;
  }

  /** True only when every on-screen pixel of the AABB's dilated rectangle is behind stored occluder depth. */
  occluded(vp: ArrayLike<number>, near: number, min: ArrayLike<number>, max: ArrayLike<number>, margin = .5) {
    const p = this.points;
    let left = Infinity, right = -Infinity, top = Infinity, bottom = -Infinity, closest = Infinity;
    for (let i = 0; i < 8; i++) {
      project(vp, i & 1 ? max[0] : min[0], i & 2 ? max[1] : min[1], i & 4 ? max[2] : min[2], p, 0);
      if (!(p[2] > near) || !Number.isFinite(p[0] + p[1])) return false;
      left = Math.min(left, p[0]); right = Math.max(right, p[0]); top = Math.min(top, p[1]); bottom = Math.max(bottom, p[1]);
      closest = Math.min(closest, p[2]);
    }
    const x0 = Math.max(0, Math.floor(left) - 1), x1 = Math.min(GRID_W, Math.ceil(right) + 1);
    const y0 = Math.max(0, Math.floor(top) - 1), y1 = Math.min(GRID_H, Math.ceil(bottom) + 1);
    if (x1 <= x0 || y1 <= y0) return false; // Off screen: the renderer's frustum test owns it.
    const limit = closest - margin;
    for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) if (!(this.depth[y * GRID_W + x] < limit)) return false;
    return true;
  }
}

function distance(slots: Float32Array, at: number, x: number, z: number) {
  const dx = Math.max(slots[at] - x, 0, x - slots[at + 3]), dz = Math.max(slots[at + 2] - z, 0, z - slots[at + 5]);
  return Math.hypot(dx, dz);
}

/** One visibility pass: set bit `slot` in `occluded` when the slot is hidden behind other placements' occluders. */
export function computeOcclusion(views: VisibilityViews, vp: ArrayLike<number>, eye: ArrayLike<number>, near: number,
  grid: DepthGrid, occluded: Uint32Array) {
  occluded.fill(0);
  const { slots, boxCounts, boxes, slotCount } = views;
  const candidates: [number, number][] = [];
  for (let slot = 0; slot < slotCount; slot++) {
    const at = slot * SLOT_STRIDE;
    if (!(slots[at + 7] & ACTIVE) || !boxCounts[slot]) continue;
    const d = distance(slots, at, eye[0], eye[2]);
    if (d <= slots[at + 6] && d <= OCCLUDER_RANGE) candidates.push([d, slot]);
  }
  candidates.sort((a, b) => a[0] - b[0]);
  grid.clear();
  let drawn = 0;
  for (const [, slot] of candidates) {
    for (let b = 0; b < boxCounts[slot] && drawn < MAX_RASTER_BOXES; b++, drawn++)
      grid.rasterBox(vp, near, boxes, (slot * MAX_SLOT_BOXES + b) * BOX_STRIDE);
    if (drawn >= MAX_RASTER_BOXES) break;
  }
  let hidden = 0;
  if (!drawn) return hidden;
  for (let slot = 0; slot < slotCount; slot++) {
    const at = slot * SLOT_STRIDE;
    if (!(slots[at + 7] & ACTIVE)) continue;
    if (grid.occluded(vp, near, slots.subarray(at, at + 3), slots.subarray(at + 3, at + 6))) {
      occluded[slot >> 5] |= 1 << (slot & 31); hidden++;
    }
  }
  return hidden;
}
