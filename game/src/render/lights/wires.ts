// Overhead wires: the generator's list (manifest `lights.wires`, pipeline/turku3d/coarse/wires.py) of the span wires,
// utility and power lines the survey lidar sees over a tile, and the street lamps hung on span wires.
// Every resident wire is in one draw: camera-facing ribbons along the wire's sag (a parabola through its ends) whose
// width never drops under one pixel. A wire thinner than that keeps the one-pixel ribbon and fades by its true share
// of the pixel instead (phone-wire anti-aliasing), and the ribbon's edge is analytic, so a wire neither breaks up nor
// shimmers at any distance and TRAA only refines it. The hung lamps are one instanced draw on the lamp posts'
// material, and their lights join the street lights (coronas, light pools, ground lamp field).
import * as THREE from 'three/webgpu';
import { attribute, cameraPosition, clamp, cross, length, max, min, positionLocal, uniform, varying } from 'three/tsl';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import type { Lamp } from './lampField';
import { lampKey, owns, type Bounds } from './tileLights';

export type WireKind = 'span' | 'utility' | 'power';
export interface WiresField { version: number; kinds?: string[]; wires: number[][]; lamps: number[][] }
/** A wire between its ends (engine metres) hanging `sag` metres below the chord at mid span. */
export interface WireSpan { ax: number; ay: number; az: number; bx: number; by: number; bz: number; sag: number; kind: WireKind }
/** The point on a span wire a lamp hangs from. */
export interface WireLamp { x: number; y: number; z: number }
export interface WireTile { wires: WireSpan[]; lamps: WireLamp[] }

export const WIRE = {
  /** Real radius by kind, metres: a lighting span cable, an aerial bundled cable, a power conductor. */
  radius: { span: 0.007, utility: 0.012, power: 0.014 } as Record<WireKind, number>,
  /** Ribbon stations: one per metre along the span, at least 2 and at most 48 segments. */
  segment: 1, maxSegments: 48,
  /** Drawn width floor, pixels. */
  minPixels: 1,
  color: 0x151617,
  /** A hung lamp: its light sits this far below the wire; warm-white LED like the street posts. */
  lamp: { drop: 0.6, radius: 1.2, color: [1, 0.86, 0.68] as const, intensity: 5 },
  /** A listed lamp hung over the street this close to a wire lamp is the same lamp. */
  same: 3,
} as const;

const KINDS: readonly WireKind[] = ['span', 'utility', 'power'];
const finite = (values: readonly unknown[]) => values.every(v => typeof v === 'number' && Number.isFinite(v));

/** The tile's wires and hung lamps; malformed or absent lists give none. Only items the tile owns are kept. */
export function decodeWires(field: WiresField | null | undefined, bounds: Bounds | null): WireTile | null {
  if (!field || field.version !== 1 || !Array.isArray(field.wires) || !Array.isArray(field.lamps)) return null;
  const names = Array.isArray(field.kinds) ? field.kinds : KINDS;
  const wires: WireSpan[] = [];
  for (const w of field.wires) {
    if (!Array.isArray(w) || w.length < 8 || !finite(w.slice(0, 8))) continue;
    const name = names[w[7]] as WireKind;
    const span: WireSpan = { ax: w[0], ay: w[1], az: w[2], bx: w[3], by: w[4], bz: w[5], sag: Math.max(0, w[6]),
      kind: KINDS.includes(name) ? name : 'utility' };
    const mid = wireAt(span, 0.5);
    if (!bounds || owns(bounds, mid.x, mid.z)) wires.push(span);
  }
  const lamps = field.lamps.filter(l => Array.isArray(l) && l.length >= 3 && finite(l.slice(0, 3)) && (!bounds || owns(bounds, l[0], l[2])))
    .map(l => ({ x: l[0], y: l[1], z: l[2] }));
  return wires.length || lamps.length ? { wires, lamps } : null;
}

/** The wire's point at `t` (0 at a, 1 at b). */
export function wireAt(w: WireSpan, t: number) {
  return { x: w.ax + (w.bx - w.ax) * t, y: w.ay + (w.by - w.ay) * t - w.sag * 4 * t * (1 - t), z: w.az + (w.bz - w.az) * t };
}

/** Street lights of the hung lamps: a corona and a light pool like a lamp post's, at the lamp below the wire. */
export function wireLamps(tile: WireTile | null): Lamp[] {
  if (!tile) return [];
  const { drop, radius, color, intensity } = WIRE.lamp;
  return tile.lamps.map(l => {
    const y = l.y - drop;
    return { key: lampKey(l.x, l.z), x: l.x, y, z: l.z, yaw: 0, scale: 1, post: false, kind: 'street' as const,
      light: { x: l.x, y, z: l.z, radius, r: color[0] * intensity, g: color[1] * intensity, b: color[2] * intensity, day: 0 } };
  });
}

/** Listed lamps without the hung ones a wire lamp replaces (the survey measured where that lamp really hangs). */
export function withoutReplaced(listed: readonly Lamp[], hung: readonly Lamp[]): Lamp[] {
  if (!hung.length) return [...listed];
  return listed.filter(l => l.post || l.kind !== 'street' || !hung.some(h => Math.hypot(h.x - l.x, h.z - l.z) < WIRE.same));
}

/** Ribbon stations of every wire: centre-line positions, tangents, side (-1, +1) and real radius per vertex. */
export function wireGeometry(wires: readonly WireSpan[]): THREE.BufferGeometry {
  let vertices = 0, quads = 0;
  const counts = wires.map(w => {
    const n = Math.min(WIRE.maxSegments, Math.max(2, Math.ceil(Math.hypot(w.bx - w.ax, w.bz - w.az) / WIRE.segment)));
    vertices += (n + 1) * 2; quads += n;
    return n;
  });
  const position = new Float32Array(vertices * 3), tangent = new Float32Array(vertices * 3), normal = new Float32Array(vertices * 3);
  const side = new Float32Array(vertices), radius = new Float32Array(vertices);
  const index = new Uint32Array(quads * 6);
  let v = 0, q = 0;
  wires.forEach((w, i) => {
    const n = counts[i], r = WIRE.radius[w.kind];
    const dx = w.bx - w.ax, dz = w.bz - w.az, dy = w.by - w.ay;
    for (let k = 0; k <= n; k++) {
      const t = k / n, p = wireAt(w, t);
      // d/dt of the parabola; the sag term's slope is -4 sag (1 - 2t).
      let tx = dx, ty = dy - w.sag * 4 * (1 - 2 * t), tz = dz;
      const l = Math.hypot(tx, ty, tz) || 1; tx /= l; ty /= l; tz /= l;
      for (const s of [-1, 1]) {
        position.set([p.x, p.y, p.z], v * 3); tangent.set([tx, ty, tz], v * 3); normal.set([0, 1, 0], v * 3);
        side[v] = s; radius[v] = r; v++;
      }
      if (k < n) {
        const a = v - 2;
        index.set([a, a + 1, a + 2, a + 1, a + 3, a + 2], q * 6); q++;
      }
    }
  });
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(position, 3));
  g.setAttribute('normal', new THREE.BufferAttribute(normal, 3));
  g.setAttribute('wireTangent', new THREE.BufferAttribute(tangent, 3));
  g.setAttribute('wireSide', new THREE.BufferAttribute(side, 1));
  g.setAttribute('wireRadius', new THREE.BufferAttribute(radius, 1));
  g.setIndex(new THREE.BufferAttribute(index, 1));
  g.computeBoundingSphere();
  return g;
}

/** One wire far below the city: the draw (and its pipeline) exists before any tile brings wires. */
const PARKED: WireSpan = { ax: 0, ay: -1000, az: 0, bx: 1, by: -1000, bz: 0, sag: 0, kind: 'span' };

function part(geometry: THREE.BufferGeometry, color: readonly number[], glow: number) {
  const g = geometry.index ? geometry.toNonIndexed() : geometry;
  const n = g.attributes.position.count;
  g.setAttribute('color', new THREE.Float32BufferAttribute(Array.from({ length: n }, () => color).flat(), 3));
  g.setAttribute('lampGlow', new THREE.Float32BufferAttribute(new Array(n).fill(glow), 1));
  g.deleteAttribute('uv');
  return g;
}

/** A pendant street lamp, origin at the wire: hanger, a dark conical hood and a glowing bowl underneath (~70 tris). */
export function hungLampGeometry(): THREE.BufferGeometry {
  const dark = [0.16, 0.17, 0.18], glass = [0.92, 0.9, 0.84];
  const hanger = new THREE.CylinderGeometry(0.012, 0.012, 0.3, 4, 1, true).translate(0, -0.15, 0);
  const hood = new THREE.CylinderGeometry(0.1, 0.3, 0.2, 10, 1, false).translate(0, -0.4, 0);
  const bowl = new THREE.CylinderGeometry(0.28, 0.2, 0.08, 10, 1, false).translate(0, -0.54, 0);
  const merged = mergeGeometries([part(hanger, dark, 0), part(hood, dark, 0), part(bowl, glass, 1)]);
  merged.computeBoundingSphere();
  return merged;
}

const _m = new THREE.Matrix4();

export class OverheadWires {
  readonly mesh: THREE.Mesh;
  readonly lamps: THREE.InstancedMesh;
  /** Metres per pixel at one metre from the camera: 2 tan(fov / 2) / drawing-buffer height. */
  readonly pixelAngle = uniform(0.001);
  private count = 0;

  /** `lampMaterial`: the lamp posts' material (vertex colours and the night glow of the diffuser). */
  constructor(lampMaterial: THREE.Material, readonly lampCapacity = 1024) {
    const material = new THREE.MeshStandardNodeMaterial({ color: WIRE.color, roughness: 0.5, metalness: 0.3,
      transparent: true, depthWrite: false, side: THREE.DoubleSide });
    material.name = 'overhead-wires';
    const tangent = attribute('wireTangent', 'vec3'), side = attribute('wireSide', 'float'), radius = attribute('wireRadius', 'float');
    const centre = positionLocal;
    const toCamera = cameraPosition.sub(centre);
    const pixel = length(toCamera).mul(this.pixelAngle);
    // Drawn half width (pixels) never under half the floor; the ribbon reaches 0.75 px further for the analytic edge.
    const real = radius.div(pixel);
    const half = max(real, WIRE.minPixels / 2);
    const across = cross(tangent, toCamera);
    const outward = across.div(max(length(across), 1e-4));
    material.positionNode = centre.add(outward.mul(side.mul(half.add(0.75)).mul(pixel)));
    const edge = varying(side.mul(half.add(0.75)), 'v_wire_edge');
    const width = varying(half, 'v_wire_half');
    const share = varying(min(real.div(WIRE.minPixels / 2), 1), 'v_wire_share');
    material.opacityNode = clamp(width.add(0.5).sub(edge.abs()), 0, 1).mul(share);
    this.mesh = new THREE.Mesh(wireGeometry([PARKED]), material);
    this.mesh.name = 'overhead-wires';
    this.mesh.frustumCulled = false;
    this.mesh.castShadow = false; this.mesh.receiveShadow = true;
    this.lamps = new THREE.InstancedMesh(hungLampGeometry(), lampMaterial, lampCapacity);
    this.lamps.name = 'hung-street-lamps';
    this.lamps.frustumCulled = false;
    this.lamps.castShadow = false; this.lamps.receiveShadow = true;
    this.setTiles([]);
  }

  /** All resident tiles' wires and hung lamps. Rebuilds the one ribbon buffer; returns the wire count. */
  setTiles(tiles: readonly WireTile[]) {
    const wires = tiles.flatMap(t => t.wires), lamps = tiles.flatMap(t => t.lamps).slice(0, this.lampCapacity);
    const old = this.mesh.geometry;
    this.mesh.geometry = wireGeometry(wires.length ? wires : [PARKED]);
    old.dispose();
    lamps.forEach((l, i) => this.lamps.setMatrixAt(i, _m.makeTranslation(l.x, l.y, l.z)));
    if (!lamps.length) this.lamps.setMatrixAt(0, _m.makeScale(0, 0, 0).setPosition(0, -1000, 0));
    this.lamps.count = Math.max(1, lamps.length);
    this.lamps.instanceMatrix.needsUpdate = true;
    this.count = wires.length;
    return { wires: wires.length, lamps: lamps.length };
  }

  /** Per frame: the pixel footprint for the width floor. */
  update(camera: THREE.PerspectiveCamera, size: THREE.Vector2) {
    const height = Math.max(1, size.y);
    this.pixelAngle.value = 2 * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2) / camera.zoom / height;
  }

  get wires() { return this.count; }

  dispose() { this.mesh.geometry.dispose(); (this.mesh.material as THREE.Material).dispose(); this.lamps.geometry.dispose(); this.lamps.dispose(); }
}
