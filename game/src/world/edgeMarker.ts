// The visible edge of the world: a low translucent striped band along every true edge of the collision, where the
// worker's edge walls stand (physics/edgeWalls.ts, CollisionStreamer.trueEdges). It follows the ground, sampled by
// downward rays just inside the loaded tile, so the player sees where the drivable world ends before reaching it.
import * as THREE from 'three/webgpu';
import { float, fract, mix, positionWorld, step, uv, vec3 } from 'three/tsl';
import { COLLISION_TILE, type V3 } from '../core/origin';

const BAND = {
  /** Height above the ground (m) and depth below it, so slopes between samples stay covered. */
  height: 1.1, below: 0.3,
  /** Ground samples along a 62.5 m edge, taken this far inside the loaded tile (m). */
  samples: 26, inset: 0.4,
  /** Rays start this far above the tile's highest vertex (m). */
  above: 6,
} as const;
const SIDES = [[1, 0], [-1, 0], [0, 1], [0, -1]] as const;

/** The physics worker's downward rays (PhysicsClient.raycast). */
interface Rays { raycast(origin: V3, dir: V3, maxToi: number): Promise<{ point: V3 } | null> }

/** Points along side `side` of tile (i, j), from one end to the other, with the inward sample position. */
export function edgeLine(i: number, j: number, side: number, n: number = BAND.samples) {
  const [di, dj] = SIDES[side];
  const x0 = i * COLLISION_TILE, z0 = j * COLLISION_TILE;
  const out: { x: number; z: number; sx: number; sz: number }[] = [];
  for (let k = 0; k < n; k++) {
    const t = k / (n - 1) * COLLISION_TILE;
    const x = di ? x0 + (di > 0 ? COLLISION_TILE : 0) : x0 + t;
    const z = dj ? z0 + (dj > 0 ? COLLISION_TILE : 0) : z0 + t;
    out.push({ x, z, sx: x - di * BAND.inset, sz: z - dj * BAND.inset });
  }
  return out;
}

export class EdgeMarker {
  readonly group = new THREE.Group();
  private bands = new Map<string, THREE.Mesh | null>();
  private material: THREE.MeshBasicNodeMaterial;
  private nextCheck = 0;
  private readonly warmup: THREE.Mesh;

  /** `collision`: the live manifest record, for each tile's height range (`h`). */
  constructor(private rays: Rays, private collision: Readonly<Record<string, { h: [number, number] }>>) {
    this.group.name = 'edge-marker';
    const m = new THREE.MeshBasicNodeMaterial({ transparent: true, depthWrite: false, side: THREE.DoubleSide, fog: true });
    // Diagonal amber and dark stripes, 0.8 m wide, opaque at the ground and fading upwards; a bright line on top.
    const stripe = step(0.5, fract(positionWorld.x.add(positionWorld.z).add(positionWorld.y).div(1.6)));
    const v = uv().y;
    m.colorNode = mix(mix(vec3(0.07, 0.07, 0.08), vec3(1, 0.72, 0.12), stripe), vec3(1, 0.9, 0.5), step(0.9, v));
    m.opacityNode = mix(float(0.85), float(0.4), v).max(step(0.9, v).mul(0.9));
    this.material = m;
    // Edges arrive after the first worker replies. Keep their exact indexed position/UV
    // variant resident from startup warmup, including both sides of the transparent band.
    // Degenerate triangles produce no pixels; the retained hidden draw keeps r186's node
    // builder cache alive even when the last real edge disappears during streaming.
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(12), 3));
    geometry.setAttribute('uv', new THREE.Float32BufferAttribute(new Float32Array(8), 2));
    geometry.setIndex([0, 1, 2, 2, 1, 3]);
    this.warmup = new THREE.Mesh(geometry, m);
    this.warmup.name = 'edge-material-warmup';
    this.warmup.frustumCulled = false;
    this.group.add(this.warmup);
  }

  /** The bands along `edges` ([i, j, side], see CollisionStreamer.trueEdges); checked at most 4x per second. */
  update(edges: () => [number, number, number][], now = performance.now()) {
    this.warmup.visible = false;
    if (now < this.nextCheck) return;
    this.nextCheck = now + 250;
    const want = new Set(edges().map(e => e.join('_')));
    for (const [key, mesh] of this.bands) {
      if (want.has(key)) continue;
      if (mesh) { mesh.removeFromParent(); mesh.geometry.dispose(); }
      this.bands.delete(key);
    }
    for (const key of want) if (!this.bands.has(key)) void this.build(key);
  }

  /** Edges currently marked (tests and the HUD). */
  get count() { return [...this.bands.values()].filter(Boolean).length; }

  private async build(key: string) {
    this.bands.set(key, null);
    const [i, j, side] = key.split('_').map(Number);
    const [lo, hi] = this.collision[`c_${i}_${j}`]?.h ?? [0, 0];
    const line = edgeLine(i, j, side), top = hi + BAND.above;
    const ground = await Promise.all(line.map(async p =>
      (await this.rays.raycast({ x: p.sx, y: top, z: p.sz }, { x: 0, y: -1, z: 0 }, top - lo + 50))?.point.y ?? null));
    if (!this.bands.has(key) || this.bands.get(key)) return; // gone (or built) meanwhile
    let last = ground.find(g => g !== null) ?? lo;
    const pos: number[] = [], uvs: number[] = [], idx: number[] = [];
    line.forEach((p, k) => {
      const y = last = ground[k] ?? last;
      pos.push(p.x, y - BAND.below, p.z, p.x, y + BAND.height, p.z);
      uvs.push(k / (line.length - 1), 0, k / (line.length - 1), 1);
      if (k) idx.push(2 * k - 2, 2 * k - 1, 2 * k, 2 * k, 2 * k - 1, 2 * k + 1);
    });
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
    geometry.setIndex(idx);
    const mesh = new THREE.Mesh(geometry, this.material);
    mesh.name = `edge ${key}`;
    mesh.renderOrder = 2;
    this.bands.set(key, mesh);
    this.group.add(mesh);
  }

  dispose() {
    for (const mesh of this.bands.values()) mesh?.geometry.dispose();
    this.bands.clear();
    this.group.removeFromParent();
    this.warmup.geometry.dispose();
    this.material.dispose();
  }
}
