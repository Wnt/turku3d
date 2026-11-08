// Every resident street lamp lights the street through one shared irradiance field: the ground under and between
// the lamps, and the walls beside them. It is a data node of the session's fixed light set (render/lighting.ts), so
// every lit material receives it whatever its family: asphalt, pavers, grass, facades, posts, cars and people. The
// near point slots (pools.ts) still add specular highlights and exact pools near the camera; the field has no
// per-lamp draw and no per-lamp shader loop, so the far end of a street stays lit at the cost of two texture reads.
//
// The field is one RGBA half-float texture of two 512 x 512 halves over the same 1024 m square around the camera
// (2 m texels): the left half holds the ground irradiance (rgb) and the ground height of the lamp that lights the
// texel most (a); the right half holds the horizontal light vector at a wall's ground floor (x, z), its magnitude
// summed over lamps (b) and the head height of the lamp lighting it most (a). The ground term lights up-facing
// surfaces within a few metres of that lamp's ground; the wall term lights vertical surfaces up to a little above
// the lamp's head, wrapped (half-Lambert) so a face between two lamps keeps light from both.
//
// Luminaire: a street light's batwing distribution (light thrown sideways along the street, full cut-off above
// ~80 degrees from nadir), so pools under lamps 25-35 m apart overlap instead of leaving black gaps, and a lamp's
// nadir illuminance is the same for every mounting height (taller lamps spread wider, not brighter).
import * as THREE from 'three/webgpu';
import { Fn, If, abs, cameraWorldMatrix, clamp, float, max, normalWorld, positionView, renderGroup, select, smoothstep, texture, uniform, vec2, vec3, vec4 } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';
import type { Lamp } from './lampField';
import { sightBlocked } from './pools';
import type { Footprint } from './tileScan';

const SIZE = 512, SPAN = 1024, CELL = SPAN / SIZE, WIDTH = SIZE * 2;

export const LAMP_FIELD = {
  /** No lamp lights anything farther than this horizontally, metres; its tail fades as (1 - (r/radius)^4)^2. */
  radius: 34,
  /** Ground irradiance straight below a street lamp at full night: an 8 m lamp; lower path lamps a little less. */
  nadir: 0.4,
  /** Batwing: intensity grows as 1 + batwing * sin^2 of the angle from nadir, then cuts off between these cosines. */
  batwing: 1.6, cutoff: [0.17, 0.42] as const,
  /** Bridge-deck lamps: low LED heads 4 m over the deck, brighter for their height. */
  bridge: { height: 4, gain: 1.6 },
  /** A lamp hung on a span wire over the street hangs this far above it (the survey's Kaskenkatu lamps: 8.7-9.9 m). */
  hang: 9,
  /** Wall term: reference height above the ground (a ground-floor facade) and its strength relative to the ground's. */
  wallHeight: 2.5, wall: 0.7,
} as const;

const pixels = new Uint16Array(WIDTH * SIZE * 4);
const map = new THREE.DataTexture(pixels, WIDTH, SIZE, THREE.RGBAFormat, THREE.HalfFloatType);
map.minFilter = map.magFilter = THREE.LinearFilter;
map.needsUpdate = true;
// One value for the whole frame: render-group uniforms, so a draw's own (object) group holds none of them and an
// unchanged draw skips its refresh (render/staticRefresh.ts) as the weather uniforms let it.
export const groundLampField = {
  map, origin: uniform(new THREE.Vector2()).setGroup(renderGroup), night: uniform(0).setGroup(renderGroup), ready: uniform(0).setGroup(renderGroup),
};

/** Where a lamp's light comes from and the ground it stands on (engine metres), or null for lamps that light no field. */
export function fieldLamp(lamp: Lamp): { x: number; y: number; z: number; base: number; gain: number } | null {
  const head = lamp.light;
  if (lamp.kind === 'bridge') return { x: head.x, y: head.y, z: head.z, base: head.y - LAMP_FIELD.bridge.height, gain: LAMP_FIELD.bridge.gain };
  if (lamp.kind !== 'street') return null;
  // A post stands on its base; a lamp hung over the street (span wire, register "hung") carries no ground height.
  const base = lamp.post ? lamp.y : head.y - LAMP_FIELD.hang;
  return { x: head.x, y: head.y, z: head.z, base, gain: 1 };
}

/** Relative luminous intensity at `c`, the cosine of the angle from nadir (0 above the cut-off). */
export function batwing(c: number) {
  const [lo, hi] = LAMP_FIELD.cutoff;
  const t = Math.min(1, Math.max(0, (c - lo) / (hi - lo)));
  return (1 + LAMP_FIELD.batwing * (1 - c * c)) * t * t * (3 - 2 * t);
}

/** Peak intensity of a lamp `height` metres above its ground: its nadir irradiance times height squared. */
export function lampIntensity(height: number, gain = 1) {
  return LAMP_FIELD.nadir * gain * Math.min(1.15, Math.max(0.7, height / 8)) * height * height;
}

export class GroundLampField {
  /** Active shader state for the dev harness; use the instance, not a second HMR module import. */
  readonly state = groundLampField;
  /** The luminaire constants, for live tuning from the dev console with rebuild(). */
  readonly tuning = LAMP_FIELD;
  private lamps: readonly Lamp[] = [];
  private dirty = true;
  private x = Infinity;
  private z = Infinity;
  private readonly linear = new Float32Array(pixels.length);
  private readonly strongest = new Float32Array(SIZE * SIZE * 2);
  private work: Generator<void> | null = null;
  /** What the current (or last complete) map was built from: lamps and footprint grids that reach its square. */
  private built = '';
  private buildMs = 0;
  lastBuildMs = 0;
  maxStepMs = 0;
  count = 0;
  constructor(private footprints: () => Iterable<Footprint>) {}
  get pending() { return this.work !== null; }
  setLamps(lamps: readonly Lamp[]) { this.lamps = lamps; this.dirty = true; }
  /** Rebuild from the same inputs (after a tuning change). */
  rebuild() { this.built = ''; this.dirty = true; }
  update(x: number, z: number, night: number) {
    groundLampField.night.value = night;
    const ox = Math.floor(x / 128) * 128 - SPAN / 2;
    const oz = Math.floor(z / 128) * 128 - SPAN / 2;
    if (ox !== this.x || oz !== this.z) this.dirty = true;
    if (night <= .001) {
      if (this.work) { this.work = null; this.dirty = true; this.built = ''; }
      return;
    }
    if (this.dirty) {
      this.x = ox; this.z = oz; this.dirty = false;
      // Tiles stream in and out far beyond the square all the time: only a change that reaches it restarts the build
      // (a build takes a few hundred frames of 1.5 ms; restarting it per streamed tile could starve it).
      const footprints = [...this.footprints()];
      const inputs = this.inputs(ox, oz, footprints);
      if (inputs !== this.built) {
        this.built = inputs;
        this.buildMs = 0; this.maxStepMs = 0;
        this.work = this.rasterize(ox, oz, this.lamps, footprints);
      }
    }
    if (!this.work) return;
    const start = performance.now();
    // Keep the previous complete map on the GPU while producing its replacement.
    do { if (this.work.next().done) { this.work = null; break; } } while (performance.now() - start < 1.5);
    const elapsed = performance.now() - start;
    this.buildMs += elapsed; this.maxStepMs = Math.max(this.maxStepMs, elapsed);
    if (!this.work) this.lastBuildMs = this.buildMs;
  }
  private inputs(ox: number, oz: number, footprints: readonly Footprint[]) {
    const R = LAMP_FIELD.radius, x0 = ox - R, z0 = oz - R, x1 = ox + SPAN + R, z1 = oz + SPAN + R;
    let n = 0, sum = 0;
    for (const lamp of this.lamps) {
      const head = fieldLamp(lamp);
      if (!head || head.x < x0 || head.x > x1 || head.z < z0 || head.z > z1) continue;
      n++; sum = (sum + Math.imul(Math.round(head.x * 10) ^ Math.imul(Math.round(head.z * 10), 73856093), 19349663)
        + Math.round((head.y + head.base) * 10) + Math.round((lamp.light.r + lamp.light.g * 3 + lamp.light.b * 7) * 100)) | 0;
    }
    const grids = footprints.filter(f => f.x1 > x0 && f.x0 < x1 && f.z1 > z0 && f.z0 < z1);
    return `${ox},${oz}:${n}:${sum}:${grids.length}:${grids.map(f => `${f.x0},${f.z0}`).join(';')}`;
  }
  private *rasterize(ox: number, oz: number, lamps: readonly Lamp[], footprints: Footprint[]): Generator<void> {
    this.count = 0;
    const linear = this.linear, strongest = this.strongest, R = LAMP_FIELD.radius, R2 = R * R;
    linear.fill(0); strongest.fill(0);
    yield;
    let visited = 0;
    for (const lamp of lamps) {
      const head = fieldLamp(lamp);
      if (!head) continue;
      const peak = Math.max(lamp.light.r, lamp.light.g, lamp.light.b, .001);
      const cr = lamp.light.r / peak, cg = lamp.light.g / peak, cb = lamp.light.b / peak;
      const height = Math.max(3, head.y - head.base);
      const intensity = lampIntensity(height, head.gain);
      const wallDrop = Math.max(.5, height - LAMP_FIELD.wallHeight);
      const x0 = Math.max(0, Math.floor((head.x - R - ox) / CELL));
      const x1 = Math.min(SIZE - 1, Math.ceil((head.x + R - ox) / CELL));
      const z0 = Math.max(0, Math.floor((head.z - R - oz) / CELL));
      const z1 = Math.min(SIZE - 1, Math.ceil((head.z + R - oz) / CELL));
      if (x0 > x1 || z0 > z1) continue;
      this.count++;
      // Sight samples stay within the radius of the head: grids without a covered cell there can never block (the
      // sight march per texel was 4.3 s of a 6.7-min Windows drive, frame-mp win-drive-2; most lamps have few walls near).
      const local = footprints.filter(f => f.x1 > head.x - R && f.x0 < head.x + R && f.z1 > head.z - R && f.z0 < head.z + R
        && f.coversAny(head.x - R, head.z - R, head.x + R, head.z + R));
      for (let j = z0; j <= z1; j++) for (let i = x0; i <= x1; i++) {
        if ((++visited & 63) === 0) yield;
        const wx = ox + (i + .5) * CELL, wz = oz + (j + .5) * CELL;
        const dx = head.x - wx, dz = head.z - wz, r2 = dx * dx + dz * dz;
        if (r2 >= R2 || (local.length && sightBlocked(local, head.x, head.z, wx, wz))) continue;
        const window = (1 - (r2 / R2) ** 2) ** 2;
        // Ground: E = I(theta) cos(theta) / d^2 on a horizontal surface at the lamp's ground.
        const d2 = r2 + height * height, c = height / Math.sqrt(d2);
        const ground = intensity * batwing(c) * c / d2 * window;
        // Wall: the horizontal component at a ground-floor facade, as a vector toward the lamp.
        const w2 = r2 + wallDrop * wallDrop, w = Math.sqrt(w2), r = Math.sqrt(r2);
        const wall = r > 1e-3 ? intensity * batwing(wallDrop / w) * (r / w) / w2 * window * LAMP_FIELD.wall : 0;
        const texel = j * SIZE + i, at = (j * WIDTH + i) * 4, wat = at + SIZE * 4;
        linear[at] += ground * cr;
        linear[at + 1] += ground * cg;
        linear[at + 2] += ground * cb;
        if (ground > strongest[texel]) { strongest[texel] = ground; linear[at + 3] = head.base; }
        if (wall > 0) {
          linear[wat] += wall * dx / r;
          linear[wat + 1] += wall * dz / r;
          linear[wat + 2] += wall;
          if (wall > strongest[SIZE * SIZE + texel]) { strongest[SIZE * SIZE + texel] = wall; linear[wat + 3] = head.y; }
        }
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
  dispose() { this.work = null; this.built = ''; groundLampField.night.value = 0; groundLampField.ready.value = 0; }
}

/** Warm white for a wall texel whose ground half is dark (the chroma of the hung LED lamps, wires.ts). */
const WALL_WHITE = vec3(1, .86, .68);

/** The field's light on the shaded point: diffuse irradiance (albedo and occlusion still shape it). World position
 *  and normal come from the view-space ones lighting already has: no new varying (facade programs sit at WebGPU's 16). */
export function lampFieldIrradiance(view: Node<'vec3'> = positionView) {
  return Fn(() => {
    const result = vec3(0).toVar();
    const world = cameraWorldMatrix.mul(vec4(view, 1)).xyz;
    const coord = world.xz.sub(groundLampField.origin).div(SPAN);
    If(groundLampField.night.greaterThan(.001).and(groundLampField.ready.greaterThan(0))
      .and(coord.x.greaterThan(0)).and(coord.x.lessThan(1)).and(coord.y.greaterThan(0)).and(coord.y.lessThan(1)), () => {
      // Texel centres only: bilinear filtering never mixes the two halves.
      const c = clamp(coord, .5 / SIZE, 1 - .5 / SIZE);
      const ground = texture(map, vec2(c.x.mul(.5), c.y)).level(float(0));
      const n = normalWorld, y = world.y;
      // Ground: up-facing surfaces near the lamp's ground; no light through upper floors or on high roofs.
      const near = float(1).sub(smoothstep(1.5, 3.5, abs(y.sub(ground.a))));
      const up = smoothstep(.65, .95, n.y);
      result.assign(ground.rgb.mul(near).mul(up));
      // Walls: vertical faces from just below the ground to a little above the lamp head, half-Lambert toward the lamps.
      // Only they read the wall half (streets and roofs, most of a night frame, skip the second fetch).
      const upright = float(1).sub(smoothstep(.35, .8, abs(n.y))).mul(smoothstep(ground.a.sub(2), ground.a.sub(.5), y));
      If(upright.greaterThan(0), () => {
        const wall = texture(map, vec2(c.x.mul(.5).add(.5), c.y)).level(float(0));
        const span = float(1).sub(smoothstep(wall.a.sub(1), wall.a.add(3), y));
        const facing = max(wall.b.add(n.x.mul(wall.r)).add(n.z.mul(wall.g)).mul(.5), 0);
        const peak = max(ground.r, max(ground.g, ground.b));
        const tint = select(peak.greaterThan(1e-4), ground.rgb.div(max(peak, 1e-4)), WALL_WHITE);
        result.addAssign(tint.mul(facing.mul(upright).mul(span)));
      });
      result.mulAssign(groundLampField.night);
    });
    return result;
  })();
}

interface LightingContext { positionView?: Node<'vec3'>; irradiance: ReturnType<typeof vec3> }

/** The field as one data node of the session's light set: it adds to every lit material's diffuse irradiance. */
export class LampFieldData extends THREE.Node {
  /** The light set hands batched lights to every data node; this one reads the shared field instead. */
  setLights() { return this; }
  override setup(builder: THREE.NodeBuilder) {
    // Unlit-model materials (basic) take no light and ignore irradiance: no code for them.
    if ((builder.material as { isMeshBasicNodeMaterial?: boolean } | null)?.isMeshBasicNodeMaterial) return null;
    const context = builder.context as unknown as LightingContext;
    context.irradiance.addAssign(lampFieldIrradiance(context.positionView ?? positionView));
    return null;
  }
}
