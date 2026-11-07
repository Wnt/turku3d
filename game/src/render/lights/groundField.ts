// All resident mapped lamps illuminate the ground through one shared irradiance map. The near point slots still
// light people, posts and foliage; this field has no per-lamp draw. Floodlit venues light every material through the
// light set's area light instead (floodArea.ts): pitches and tracks are dry surfaces that never sample this map.
import * as THREE from 'three/webgpu';
import { Fn, If, float, normalWorldGeometry, positionWorld, smoothstep, texture, uniform, vec3 } from 'three/tsl';
import type { Lamp } from './lampField';
import { sightBlocked } from './pools';
import type { Footprint } from './tileScan';

const SIZE = 512, SPAN = 1024, CELL = SPAN / SIZE, RADIUS = 22;
const pixels = new Uint16Array(SIZE * SIZE * 4);
const map = new THREE.DataTexture(pixels, SIZE, SIZE, THREE.RGBAFormat, THREE.HalfFloatType);
map.minFilter = map.magFilter = THREE.LinearFilter;
map.needsUpdate = true;
export const groundLampField = {
  map, origin: uniform(new THREE.Vector2()), night: uniform(0), ready: uniform(0),
};

/** Added to diffuse indirect irradiance, so albedo and occlusion still shape each pool. */
export function mappedGroundLight() {
  return Fn(() => {
    const result = vec3(0).toVar();
    const coord = positionWorld.xz.sub(groundLampField.origin).div(SPAN);
    If(groundLampField.night.greaterThan(.001).and(groundLampField.ready.greaterThan(0))
      .and(coord.x.greaterThan(0)).and(coord.x.lessThan(1)).and(coord.y.greaterThan(0)).and(coord.y.lessThan(1)), () => {
      const field = texture(map, coord).level(float(0));
      // No light through upper floors, on roofs or on vertical facades.
      const height = float(1).sub(smoothstep(1, 2.5, positionWorld.y.sub(field.a).abs()));
      const up = smoothstep(.65, .95, normalWorldGeometry.y);
      result.assign(field.rgb.mul(height).mul(up).mul(groundLampField.night));
    });
    return result;
  })();
}

export class GroundLampField {
  /** Active shader state for the dev harness; use the instance, not a second HMR module import. */
  readonly state = groundLampField;
  private lamps: readonly Lamp[] = [];
  private dirty = true;
  private x = Infinity;
  private z = Infinity;
  private readonly linear = new Float32Array(pixels.length);
  private readonly strongest = new Float32Array(SIZE * SIZE);
  private work: Generator<void> | null = null;
  private buildMs = 0;
  lastBuildMs = 0;
  maxStepMs = 0;
  count = 0;
  constructor(private footprints: () => Iterable<Footprint>) {}
  get pending() { return this.work !== null; }
  setLamps(lamps: readonly Lamp[]) { this.lamps = lamps; this.dirty = true; }
  update(x: number, z: number, night: number) {
    groundLampField.night.value = night;
    const ox = Math.floor(x / 128) * 128 - SPAN / 2;
    const oz = Math.floor(z / 128) * 128 - SPAN / 2;
    if (ox !== this.x || oz !== this.z) this.dirty = true;
    if (night <= .001) {
      if (this.work) { this.work = null; this.dirty = true; }
      return;
    }
    if (this.dirty) {
      this.x = ox; this.z = oz; this.dirty = false;
      this.buildMs = 0; this.maxStepMs = 0;
      this.work = this.rasterize(ox, oz, this.lamps, [...this.footprints()]);
    }
    if (!this.work) return;
    const start = performance.now();
    // Keep the previous complete map on the GPU while producing its replacement.
    do { if (this.work.next().done) { this.work = null; break; } } while (performance.now() - start < 1.5);
    const elapsed = performance.now() - start;
    this.buildMs += elapsed; this.maxStepMs = Math.max(this.maxStepMs, elapsed);
    if (!this.work) this.lastBuildMs = this.buildMs;
  }
  private *rasterize(ox: number, oz: number, lamps: readonly Lamp[], footprints: Footprint[]): Generator<void> {
    this.count = 0;
    const linear = this.linear, strongest = this.strongest;
    linear.fill(0); strongest.fill(0);
    yield;
    let visited = 0;
    for (const lamp of lamps) {
      if (lamp.kind !== 'street' && lamp.kind !== 'bridge') continue;
      const head = lamp.light;
      const base = lamp.kind === 'bridge' ? head.y - 4 : lamp.y;
      const height = Math.max(3, head.y - base);
      const peak = Math.max(head.r, head.g, head.b, .001);
      const intensity = lamp.kind === 'bridge' ? 16 : 20;
      const x0 = Math.max(0, Math.floor((head.x - RADIUS - ox) / CELL));
      const x1 = Math.min(SIZE - 1, Math.ceil((head.x + RADIUS - ox) / CELL));
      const z0 = Math.max(0, Math.floor((head.z - RADIUS - oz) / CELL));
      const z1 = Math.min(SIZE - 1, Math.ceil((head.z + RADIUS - oz) / CELL));
      if (x0 > x1 || z0 > z1) continue;
      this.count++;
      // Sight samples stay within RADIUS of the head: grids without a covered cell there can never block (the sight
      // march per texel was 4.3 s of a 6.7-min Windows drive, frame-mp win-drive-2; most lamps have few walls near).
      const local = footprints.filter(f => f.x1 > head.x - RADIUS && f.x0 < head.x + RADIUS
        && f.z1 > head.z - RADIUS && f.z0 < head.z + RADIUS
        && f.coversAny(head.x - RADIUS, head.z - RADIUS, head.x + RADIUS, head.z + RADIUS));
      for (let j = z0; j <= z1; j++) for (let i = x0; i <= x1; i++) {
        if ((++visited & 63) === 0) yield;
        const wx = ox + (i + .5) * CELL, wz = oz + (j + .5) * CELL;
        const r2 = (wx - head.x) ** 2 + (wz - head.z) ** 2;
        if (r2 >= RADIUS * RADIUS || (local.length && sightBlocked(local, head.x, head.z, wx, wz))) continue;
        const falloff = (1 - (r2 / (RADIUS * RADIUS)) ** 2) ** 2;
        // A downward luminaire concentrates emission below its head. Cosine^4
        // suppresses the grazing spill that otherwise washes the whole roadway.
        const beam = (height * height / (r2 + height * height)) ** 2;
        const energy = intensity * height / (r2 + height * height) ** 1.5 * falloff * beam;
        const texel = j * SIZE + i, at = texel * 4;
        linear[at] += energy * head.r / peak;
        linear[at + 1] += energy * head.g / peak;
        linear[at + 2] += energy * head.b / peak;
        if (energy > strongest[texel]) { strongest[texel] = energy; linear[at + 3] = base; }
      }
    }
    for (let i = 0; i < pixels.length; i++) {
      if ((i & 8191) === 0) yield;
      const value = linear[i];
      pixels[i] = value === 0 ? 0 : THREE.DataUtils.toHalfFloat(value);
    }
    groundLampField.origin.value.set(ox, oz);
    map.needsUpdate = true; groundLampField.ready.value = 1;
  }
  dispose() { this.work = null; groundLampField.night.value = 0; groundLampField.ready.value = 0; }
}
