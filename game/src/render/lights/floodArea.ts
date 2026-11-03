// Floodlit sports venues light their surroundings through one analytic area light in the session's fixed light set
// (render/lighting.ts), so every lit material receives it whatever its family: grass pitches and rubber tracks are
// dry surfaces that never sample the ground irradiance map (groundField.ts), and stands, facades, trees, cars and
// people are not ground at all. The nearest few active venues fill a handful of render-group uniforms (no new
// buffer binding, no program per object); with none active the shader skips the whole block.
//
// Model of one venue (engine frame, metres): a field rectangle (centre c on the ground, long axis u, half extents
// a x b) under lamp heads at height H. A point at horizontal distance r outside the rectangle receives
//   E = E0 * D^2 / (D^2 + r^2) * window(r / reach),  D = spill * H,
// from a source point above the field (the point's projection onto the rectangle, pulled toward the centre, at
// height H), plus an unshadowed share of E from all directions (vertical faces of players and stands, the glow
// of a lit bowl). On the field E is flat apart from a slightly brighter middle, where beams overlap.
// window(q) = (1 - q^4)^2 keeps the tail out to about 300 m and ends it smoothly at `reach`.
import * as THREE from 'three/webgpu';
import { If, abs, clamp, float, int, max, min, positionView, renderGroup, smoothstep, uniform, vec3 } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';
import { FLOOD, type FloodMast, type FloodVenue } from './floodlights';
import { nearest } from './pools';

export const AREA = {
  /** Venues lit at once (uniform slots). */
  slots: 4,
  /** Direct irradiance on the field of a level-1 venue at full night. A street pool under its lamp is about 0.6
   *  (point slot plus the ground map), open ground in daylight about 5: a floodlit pitch is the brightest ground of a
   *  night view, with daylight colour. */
  field: 2.6,
  /** Unshadowed share of the venue's light arriving from all directions: players' and stands' vertical faces. */
  ambient: 0.3,
  /** Spill length as a share of the lamp height: E halves this far outside the field (stands, track, fences). */
  spill: 1.0,
  /** The venue lights nothing beyond this distance outside its field (metres); the tail fades as (1 - (r/reach)^4)^2. */
  reach: 400,
  /** Field share between the centre and the point's projection where the light comes from. */
  source: 0.85,
  /** Rank distance (camera to field) beyond which a venue gets no slot, and the fade before a slot is handed on. */
  range: 1600, margin: 200,
  /** Lamp height above the field of a venue whose masts are not resident (the generator's layout heights). */
  height: { stadium: 30, track: 24, large: 20, field: 16, court: 10 } as Record<string, number>,
} as const;

/** One venue as the area light sees it. */
export interface AreaVenue {
  id: string; x: number; y: number; z: number;
  /** Unit long axis (engine x/z) and half extents along and across it. */
  ux: number; uz: number; a: number; b: number;
  /** Lamp heads above the field. */
  height: number;
  /** Relative brightness (stadium 1). */
  level: number;
}

/** Area-light records of the resident venues; the lamp height is the mean of their resident masts' tops. */
export function areaVenues(venues: readonly FloodVenue[], masts: readonly FloodMast[]): AreaVenue[] {
  const tops = new Map<string, number[]>();
  for (const m of masts) { const list = tops.get(m.venue); if (list) list.push(m.top); else tops.set(m.venue, [m.top]); }
  const seen = new Set<string>(), out: AreaVenue[] = [];
  for (const v of venues) {
    if (seen.has(v.id)) continue;
    seen.add(v.id);
    const h = Math.hypot(v.ux, v.uz) || 1;
    const list = tops.get(v.id);
    const mean = list ? list.reduce((s, t) => s + t, 0) / list.length - v.y : NaN;
    out.push({ id: v.id, x: v.x, y: v.y, z: v.z, ux: v.ux / h, uz: v.uz / h, a: v.length / 2, b: v.width / 2,
      height: Math.max(6, Number.isFinite(mean) ? mean : AREA.height[v.kind] ?? 16), level: v.level });
  }
  return out;
}

/** Horizontal distance from (x, z) to the venue's field (0 on it). */
export function fieldDistance(v: AreaVenue, x: number, z: number) {
  const px = x - v.x, pz = z - v.z;
  const s = Math.abs(px * v.ux + pz * v.uz) - v.a, t = Math.abs(-px * v.uz + pz * v.ux) - v.b;
  return Math.hypot(Math.max(s, 0), Math.max(t, 0));
}

/** Slot weights of the venues nearest (x, z): 1 well inside the set, falling to 0 as a venue's distance nears the
 *  first one left out (or `range`), so the set changes only where the handed-over venue is already dark. */
export function selectVenues(venues: readonly AreaVenue[], x: number, z: number, slots: number = AREA.slots) {
  const ranked = nearest(venues, v => fieldDistance(v, x, z) ** 2, slots);
  const cut = Math.min(AREA.range, ranked.length > slots ? ranked[slots].d : Infinity);
  return ranked.slice(0, slots).map(({ item, d }) => ({ venue: item, weight: Math.min(1, Math.max(0, (cut - d) / AREA.margin)) }))
    .filter(s => s.weight > 0);
}

const smooth = (a: number, b: number, x: number) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };

/** CPU mirror of the shader for one venue at world point p with unit normal n, for an intensity scale `k`
 *  (level x weight x night): direct irradiance (N.L applied), the all-direction share, and the light direction. */
export function venueLight(v: AreaVenue, k: number, p: { x: number; y: number; z: number }, n: { x: number; y: number; z: number }) {
  const px = p.x - v.x, py = p.y - v.y, pz = p.z - v.z;
  const along = px * v.ux + pz * v.uz, across = -px * v.uz + pz * v.ux;
  const ox = Math.max(Math.abs(along) - v.a, 0), oz = Math.max(Math.abs(across) - v.b, 0);
  const r2 = ox * ox + oz * oz, d2 = (AREA.spill * v.height) ** 2;
  const window = Math.max(0, 1 - (r2 / AREA.reach ** 2) ** 2) ** 2;
  const inner = Math.min(1, Math.max(0, Math.min(v.a - Math.abs(along), v.b - Math.abs(across)) / 15));
  const e = AREA.field * k * d2 / (d2 + r2) * window * (0.88 + 0.12 * inner);
  const sa = Math.min(v.a, Math.max(-v.a, along)) * AREA.source, sb = Math.min(v.b, Math.max(-v.b, across)) * AREA.source;
  const lx = v.ux * sa - v.uz * sb - px, ly = v.height - py, lz = v.uz * sa + v.ux * sb - pz;
  const l = Math.hypot(lx, ly, lz) || 1;
  const dir = { x: lx / l, y: ly / l, z: lz / l };
  const ndl = Math.max(0, dir.x * n.x + dir.y * n.y + dir.z * n.z);
  return { direct: e * ndl, ambient: e * AREA.ambient * (1 - smooth(v.height, 2 * v.height, py)), dir };
}

/** Slot records written each frame (world space); FloodAreaData moves them into each render's view space. */
interface Slot { venue: AreaVenue | null; scale: number }
export const floodAreaState = { slots: Array.from({ length: AREA.slots }, (): Slot => ({ venue: null, scale: 0 })), count: 0 };

/** The venues' area light: picks the nearest active venues for the shader slots each frame. */
export class FloodArea {
  private venues: readonly AreaVenue[] = [];
  readonly state = floodAreaState;
  setVenues(venues: readonly AreaVenue[]) { this.venues = venues; }
  get size() { return this.venues.length; }
  /** Per frame: camera ground position and the shared night level. */
  update(x: number, z: number, night: number) {
    const chosen = night > 1e-3 ? selectVenues(this.venues, x, z) : [];
    floodAreaState.count = chosen.length;
    floodAreaState.slots.forEach((slot, i) => {
      slot.venue = chosen[i]?.venue ?? null;
      slot.scale = chosen[i] ? chosen[i].venue.level * chosen[i].weight * night : 0;
    });
  }
  snapshot() { return floodAreaState.slots.filter(s => s.venue).map(s => ({ id: s.venue!.id, scale: Math.round(s.scale * 1000) / 1000 })); }
  dispose() { floodAreaState.count = 0; for (const s of floodAreaState.slots) { s.venue = null; s.scale = 0; } }
}

type Frame = Parameters<THREE.Node['update']>[0];
interface LightingContext {
  positionView?: Node<'vec3'>;
  irradiance: ReturnType<typeof vec3>;
  lightingModel: { direct(data: Record<string, unknown>, builder: THREE.NodeBuilder): void };
  reflectedLight: { directDiffuse: ReturnType<typeof vec3>; directSpecular: ReturnType<typeof vec3> };
}

const v4 = (name: string) => uniform(new THREE.Vector4()).setGroup(renderGroup).setName(name);
const _p = new THREE.Vector3(), _d = new THREE.Vector3();

/**
 * The shader side, one data node of the session's light set. Uniforms (view space, render group, written once per
 * render for its camera): field centre and half length, long axis and half width, across axis and lamp height,
 * colour x intensity and reach, plus the view-space up axis and the active count. The venues' light is summed into
 * one light (colour, luminance-weighted direction) for a single BRDF evaluation; the all-direction share joins the
 * material's indirect irradiance, so ambient occlusion still shapes it.
 */
export class FloodAreaData extends THREE.Node {
  private readonly slots = Array.from({ length: AREA.slots }, (_, i) => ({
    centre: v4(`floodCentre${i}`), axis: v4(`floodAxis${i}`), across: v4(`floodAcross${i}`), tint: v4(`floodTint${i}`) }));
  private readonly upAxis = v4('floodUp');
  private readonly up = this.upAxis.xyz;
  private readonly count = uniform(0, 'int').setGroup(renderGroup).setName('floodCount');
  constructor() { super(); this.updateType = THREE.NodeUpdateType.RENDER; }
  /** The light set hands batched lights to every data node; this one follows floodAreaState instead. */
  setLights() { return this; }
  override update({ camera }: Frame): boolean | undefined {
    if (!camera) return;
    const view = camera.matrixWorldInverse;
    this.count.value = floodAreaState.count;
    _d.set(0, 1, 0).transformDirection(view);
    this.upAxis.value.set(_d.x, _d.y, _d.z, 0);
    const [r, g, b] = FLOOD.color;
    floodAreaState.slots.forEach((slot, i) => {
      const u = this.slots[i], v = slot.venue;
      if (!v) { u.tint.value.set(0, 0, 0, 1); return; }
      _p.set(v.x, v.y, v.z).applyMatrix4(view);
      u.centre.value.set(_p.x, _p.y, _p.z, v.a);
      _d.set(v.ux, 0, v.uz).transformDirection(view);
      u.axis.value.set(_d.x, _d.y, _d.z, v.b);
      _d.set(-v.uz, 0, v.ux).transformDirection(view);
      u.across.value.set(_d.x, _d.y, _d.z, v.height);
      const k = AREA.field * slot.scale;
      u.tint.value.set(r * k, g * k, b * k, AREA.reach);
    });
    return undefined;
  }
  override setup(builder: THREE.NodeBuilder) {
    // Unlit-model materials (basic) take no direct light and ignore irradiance: no code for them.
    if ((builder.material as { isMeshBasicNodeMaterial?: boolean } | null)?.isMeshBasicNodeMaterial) return null;
    const context = builder.context as unknown as LightingContext;
    const surface = context.positionView ?? positionView;
    If(this.count.greaterThan(int(0)), () => {
      const colour = vec3(0).toVar(), direction = vec3(0).toVar(), ambient = vec3(0).toVar();
      this.slots.forEach((s, i) => {
        If(this.count.greaterThan(int(i)), () => {
          const a = s.centre.w, b = s.axis.w, height = s.across.w, reach = s.tint.w;
          const d = surface.sub(s.centre.xyz);
          const along = d.dot(s.axis.xyz), across = d.dot(s.across.xyz), up = d.dot(this.up);
          const ox = max(abs(along).sub(a), 0), oz = max(abs(across).sub(b), 0);
          const r2 = ox.mul(ox).add(oz.mul(oz)), d2 = height.mul(AREA.spill).pow(2);
          const q2 = r2.div(reach.mul(reach));
          const window = clamp(float(1).sub(q2.mul(q2)), 0, 1).pow(2);
          const inner = clamp(min(a.sub(abs(along)), b.sub(abs(across))).div(15), 0, 1);
          const e = s.tint.xyz.mul(d2.div(d2.add(r2)).mul(window).mul(inner.mul(0.12).add(0.88)));
          const source = s.axis.xyz.mul(clamp(along, a.negate(), a).mul(AREA.source))
            .add(s.across.xyz.mul(clamp(across, b.negate(), b).mul(AREA.source))).add(this.up.mul(height));
          const toward = source.sub(d).normalize();
          colour.addAssign(e);
          direction.addAssign(toward.mul(e.x.add(e.y).add(e.z)));
          ambient.addAssign(e.mul(float(1).sub(smoothstep(height, height.mul(2), up))));
        });
      });
      // A point no venue reaches keeps a valid direction with no colour.
      const lightDirection = direction.add(this.up.mul(1e-6)).normalize();
      const diffuse = vec3(0).toVar(), specular = vec3(0).toVar();
      context.lightingModel.direct({ lightDirection, lightColor: colour, lightNode: { light: {}, shadowNode: null },
        reflectedLight: { directDiffuse: diffuse, directSpecular: specular } }, builder);
      context.reflectedLight.directDiffuse.addAssign(diffuse);
      context.reflectedLight.directSpecular.addAssign(specular);
      context.irradiance.addAssign(ambient.mul(AREA.ambient));
    });
    return null;
  }
}
