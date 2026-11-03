// Night floodlights of lit sports venues: the generator's list (manifest `lights.floodlights`,
// pipeline/turku3d/coarse/floodlights.py) of each tile's lit venues and the masts standing in it.
// Every mast gets a frame of lamp heads facing its venue centre (glare sprites in the one sprite draw), every
// venue lights its field and surroundings through the light set's area light (floodArea.ts, no draw of its own), and
// masts the city does not draw already (`mesh: 0`) get a pole (FloodMasts, one instanced draw sharing the
// lamp posts' material). Everything follows the timecycle's night level, so every client agrees.
import * as THREE from 'three/webgpu';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import type { Lamp } from './lampField';
import { owns, type Bounds } from './tileLights';

export interface FloodVenue {
  id: string; kind: string; x: number; y: number; z: number;
  /** Unit long axis of the lit field (engine x/z). */
  ux: number; uz: number; length: number; width: number;
  /** Relative brightness of the field (stadium 1). */
  level: number;
}
export interface FloodMast {
  id: string; venue: string;
  /** Base on the ground, and the absolute height of the lamp heads. */
  x: number; y: number; z: number; top: number;
  /** Venue centre the heads face (x, z). */
  aim: readonly [number, number];
  lamps: number;
  /** 1: the city already draws this mast (a mapped lattice mast); 0: FloodMasts draws its pole. */
  mesh: number;
}
export interface FloodlightsField { version: number; venues: FloodVenue[]; masts: FloodMast[] }
export interface FloodTile { venues: FloodVenue[]; masts: FloodMast[] }

export const FLOOD = {
  /** Linear colour of the LED floodlights (about 5000 K) and HDR intensity of one lamp head. */
  color: [1.0, 0.95, 0.86] as const, intensity: 30,
  /** Corona radius of one lamp head (m) and spacing of the heads on their frame. */
  radius: 1.5, spacing: 1.0, perRow: 5,
} as const;

/** The tile's floodlights; malformed or absent lists give none. Only items the tile owns are kept. */
export function decodeFloodlights(field: FloodlightsField | null | undefined, bounds: Bounds | null): FloodTile | null {
  if (!field || field.version !== 1 || !Array.isArray(field.venues) || !Array.isArray(field.masts)) return null;
  const mine = (x: number, z: number) => !bounds || owns(bounds, x, z);
  const venues = field.venues.filter(v => Number.isFinite(v.x + v.y + v.z + v.ux + v.uz + v.length + v.width) && mine(v.x, v.z));
  const masts = field.masts.filter(m => Number.isFinite(m.x + m.y + m.z + m.top) && m.top > m.y && mine(m.x, m.z));
  return venues.length || masts.length ? { venues, masts } : null;
}

/** Lamp-head points of one mast: rows of heads across the aim direction, half a metre toward the field. */
export function floodHeads(mast: FloodMast): { x: number; y: number; z: number }[] {
  let dx = mast.aim[0] - mast.x, dz = mast.aim[1] - mast.z;
  const d = Math.hypot(dx, dz);
  if (d > 1e-3) { dx /= d; dz /= d; } else { dx = 1; dz = 0; }
  const rows = Math.ceil(mast.lamps / FLOOD.perRow);
  const out: { x: number; y: number; z: number }[] = [];
  for (let i = 0; i < mast.lamps; i++) {
    const row = Math.floor(i / FLOOD.perRow), inRow = Math.min(FLOOD.perRow, mast.lamps - row * FLOOD.perRow);
    const across = (i - row * FLOOD.perRow - (inRow - 1) / 2) * FLOOD.spacing;
    out.push({ x: mast.x + dx * 0.5 - dz * across, y: mast.top - 0.4 - (row - (rows - 1) / 2) * FLOOD.spacing * 0.8,
      z: mast.z + dz * 0.5 + dx * across });
  }
  return out;
}

/** Glare sprites of every lamp head of the tile's masts. */
export function floodLamps(tile: FloodTile | null): Lamp[] {
  if (!tile) return [];
  const [r, g, b] = FLOOD.color, k = FLOOD.intensity;
  return tile.masts.flatMap(mast => floodHeads(mast).map(p => ({
    key: `${Math.round(p.x * 10)},${Math.round(p.y * 10)},${Math.round(p.z * 10)}`, x: p.x, y: p.y, z: p.z,
    yaw: 0, scale: 1, post: false, kind: 'flood' as const,
    light: { x: p.x, y: p.y, z: p.z, radius: FLOOD.radius, r: r * k, g: g * k, b: b * k, day: 0, glare: 1 },
  })));
}

function part(geometry: THREE.BufferGeometry, color: readonly number[], glow: number) {
  const g = geometry.index ? geometry.toNonIndexed() : geometry;
  const n = g.attributes.position.count;
  g.setAttribute('color', new THREE.Float32BufferAttribute(Array.from({ length: n }, () => color).flat(), 3));
  g.setAttribute('lampGlow', new THREE.Float32BufferAttribute(new Array(n).fill(glow), 1));
  g.deleteAttribute('uv');
  return g;
}

/** A unit-height mast (scaled by its height): tapered pole, and a lamp frame at the top facing local +X. */
export function mastGeometry(): THREE.BufferGeometry {
  const steel = [0.5, 0.52, 0.54], dark = [0.16, 0.17, 0.18], glass = [0.95, 0.94, 0.9];
  const pole = new THREE.CylinderGeometry(0.009, 0.016, 1, 8, 1, true).translate(0, 0.5, 0);
  const tilt = new THREE.Matrix4().makeRotationZ(-0.4);
  const frame = new THREE.BoxGeometry(0.03, 0.07, 0.17).applyMatrix4(tilt).translate(0.02, 0.975, 0);
  const lens = new THREE.PlaneGeometry(0.06, 0.15).rotateY(Math.PI / 2).applyMatrix4(tilt).translate(0.036, 0.975, 0);
  const merged = mergeGeometries([part(pole, steel, 0), part(frame, dark, 0), part(lens, glass, 1)]);
  merged.computeBoundingSphere();
  return merged;
}

const _m = new THREE.Matrix4(), _s = new THREE.Vector3();

/** Poles of the resident masts the city does not draw: one instanced draw on the lamp posts' material. */
export class FloodMasts {
  readonly mesh: THREE.InstancedMesh;
  constructor(material: THREE.Material, readonly capacity = 512) {
    this.mesh = new THREE.InstancedMesh(mastGeometry(), material, capacity);
    this.mesh.name = 'floodlight-masts';
    this.mesh.castShadow = true; this.mesh.receiveShadow = true;
    this.mesh.frustumCulled = false;
    this.setMasts([]);
  }

  setMasts(masts: readonly FloodMast[]) {
    const drawn = masts.filter(m => !m.mesh).slice(0, this.capacity);
    drawn.forEach((m, i) => {
      const yaw = Math.atan2(-(m.aim[1] - m.z), m.aim[0] - m.x);
      _m.makeRotationY(yaw).scale(_s.setScalar(m.top - m.y)).setPosition(m.x, m.y, m.z);
      this.mesh.setMatrixAt(i, _m);
    });
    // Keep one collapsed instance so the draw (and its pipeline) stays alive.
    if (!drawn.length) this.mesh.setMatrixAt(0, _m.makeScale(0, 0, 0).setPosition(0, -1000, 0));
    this.mesh.count = Math.max(1, drawn.length);
    this.mesh.instanceMatrix.needsUpdate = true;
    return drawn.length;
  }

  dispose() { this.mesh.geometry.dispose(); this.mesh.dispose(); }
}
