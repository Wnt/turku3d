// The air over a floodlit venue: one soft light dome per venue (a Gaussian glow whose line integral the fragment
// evaluates in closed form, clipped by the scene's depth) and faint beams from the lamp heads toward the field.
// Both draw in the light sprites' overlay pass (sprites.ts: composited after TRAA, no depth buffer, each fragment
// tests the resolved scene depth), as two instanced additive draws with fixed programs: venues and masts only
// rewrite instance data, and night, mist and visibility are uniforms. Fog and rain thicken the air: the dome and
// the beams grow stronger, and the scene's fog distance dims a far venue's dome.
import * as THREE from 'three/webgpu';
import { attribute, cameraFar, cameraNear, cameraViewMatrix, clamp, cross, exp, float, length, max, normalize,
  perspectiveDepthToViewZ, positionGeometry, pow, screenUV, sign, renderGroup, smoothstep, sqrt, uniform, varying, vec3, vec4 } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';
import { FLOOD, type FloodMast } from './floodlights';
import type { AreaVenue } from './floodArea';

export const GLOW = {
  /** Venue domes and beams drawn at once. */
  domes: 16, beams: 192,
  /** Dome radiance through its middle (linear HDR, level 1, full night) in clear air, and added at full mist. The
   *  clear night sky near the horizon is about 0.02. */
  clear: 0.035, misty: 0.2,
  /** Beam radiance at the head end, clear and added at full mist. */
  beamClear: 0.01, beamMisty: 0.09,
  /** Dome extents (Gaussian sigma): field share plus a lamp-height share; height of its centre and vertical sigma. */
  spread: 0.7, overhang: 0.5, lift: 0.4, rise: 0.9,
  /** Proxy box half size in sigmas. */
  extent: 2.5,
  /** A beam ends this share of the way from its head to the venue centre on the ground; its far width is this share
   *  of its length (a 20-degree spread). */
  beamReach: 0.75, beamSpread: 0.36, beamWidth: 1.2,
} as const;

/** Mist of the shared weather: fog, rain and a little of the overcast's humidity, 0..1. */
export function mistOf(weather: { fog: number; rain: number; overcast: number } | undefined) {
  return weather ? Math.min(1, Math.max(0, weather.fog + 0.35 * weather.rain + 0.1 * weather.overcast)) : 0;
}

/** Dome of one venue: centre (world) and Gaussian sigmas along its long axis, across it and vertically. */
export function domeOf(v: AreaVenue) {
  return { x: v.x, y: v.y + GLOW.lift * v.height, z: v.z, ux: v.ux, uz: v.uz,
    sa: GLOW.spread * v.a + GLOW.overhang * v.height, sb: GLOW.spread * v.b + GLOW.overhang * v.height, sy: GLOW.rise * v.height };
}
type Dome = ReturnType<typeof domeOf>;

/** Winitzki's erf, within 2e-4: the shader uses the same form. */
export function erf(x: number) {
  const x2 = x * x;
  return Math.sign(x) * Math.sqrt(1 - Math.exp(-x2 * (4 / Math.PI + 0.147 * x2) / (1 + 0.147 * x2)));
}

/** Line integral (metres) of the dome's unit Gaussian density along origin + t * dir (unit), t in [0, tEnd]. */
export function domeIntegral(d: Dome, o: { x: number; y: number; z: number }, dir: { x: number; y: number; z: number }, tEnd: number) {
  const px = o.x - d.x, py = o.y - d.y, pz = o.z - d.z;
  const oa = (px * d.ux + pz * d.uz) / d.sa, ob = (-px * d.uz + pz * d.ux) / d.sb, oy = py / d.sy;
  const da = (dir.x * d.ux + dir.z * d.uz) / d.sa, db = (-dir.x * d.uz + dir.z * d.ux) / d.sb, dy = dir.y / d.sy;
  const dd = da * da + db * db + dy * dy, od = oa * da + ob * db + oy * dy, oo = oa * oa + ob * ob + oy * oy;
  const t0 = -od / dd, k = Math.sqrt(dd);
  return Math.exp(-(oo - od * od / dd)) * Math.sqrt(Math.PI) / (2 * k) * (erf(k * (tEnd - t0)) - erf(-k * t0));
}

/** Density scale giving `peak` radiance on a horizontal line through the dome's middle along its mean sigma. */
export const domeScale = (d: Dome, peak: number) => peak / (Math.sqrt(Math.PI) * (d.sa + d.sb) / 2);

/** Beam segments of the resident masts: head to a point toward the venue centre on the ground. */
export function beamsOf(masts: readonly FloodMast[], venues: ReadonlyMap<string, AreaVenue>) {
  return masts.map(m => {
    const v = venues.get(m.venue);
    const ground = v?.y ?? m.y, level = v?.level ?? 0.85;
    const x = m.x + (m.aim[0] - m.x) * GLOW.beamReach, z = m.z + (m.aim[1] - m.z) * GLOW.beamReach;
    const y = m.top + (ground - m.top) * GLOW.beamReach;
    const length = Math.hypot(x - m.x, y - m.top, z - m.z);
    return { from: [m.x, m.top - 0.4, m.z, GLOW.beamWidth] as const, to: [x, y, z, Math.max(GLOW.beamWidth, length * GLOW.beamSpread)] as const,
      level: level * Math.min(1, m.lamps / 8) };
  });
}

/** Winitzki's erf in TSL. */
function erfNode(x: Node<'float'>) {
  const x2 = x.mul(x);
  return sign(x).mul(sqrt(float(1).sub(exp(x2.negate().mul(x2.mul(0.147).add(4 / Math.PI)).div(x2.mul(0.147).add(1))))));
}

export class FloodGlow {
  readonly domes: THREE.Mesh<THREE.InstancedBufferGeometry, THREE.NodeMaterial>;
  readonly beams: THREE.Mesh<THREE.InstancedBufferGeometry, THREE.NodeMaterial>;
  /** Night x mist strength of domes (x) and beams (y), the fog distance (z) and 1 at night (w): by day the proxies
   *  collapse, so the draws (and their programs, built at the first frame) cost no fragments. */
  private readonly level = uniform(new THREE.Vector4(0, 0, 2600, 0)).setGroup(renderGroup);
  // These and level are set per frame before the render, so one value for every draw: render group (static refresh skips unchanged draws).
  private readonly projection = uniform(new THREE.Matrix4()).setGroup(renderGroup);
  private readonly eye = uniform(new THREE.Vector3()).setGroup(renderGroup);
  private readonly forward = uniform(new THREE.Vector3(0, 0, -1)).setGroup(renderGroup);
  private readonly dome = { centre: this.instanced(GLOW.domes), axis: this.instanced(GLOW.domes), size: this.instanced(GLOW.domes) };
  private readonly beam = { from: this.instanced(GLOW.beams), to: this.instanced(GLOW.beams), tint: this.instanced(GLOW.beams) };
  domeCount = 0;
  beamCount = 0;

  /** `scene` and `depth` are the light sprites' overlay scene and its resolved scene depth node. */
  constructor(scene: THREE.Scene, private readonly depth: THREE.TextureNode) {
    const box = new THREE.InstancedBufferGeometry();
    box.copy(new THREE.BoxGeometry(2, 2, 2) as unknown as THREE.InstancedBufferGeometry);
    box.deleteAttribute('normal'); box.deleteAttribute('uv');
    box.setAttribute('domeCentre', this.dome.centre); box.setAttribute('domeAxis', this.dome.axis); box.setAttribute('domeSize', this.dome.size);
    box.instanceCount = 1;
    this.domes = new THREE.Mesh(box, this.domeMaterial());
    const quad = new THREE.InstancedBufferGeometry();
    quad.setAttribute('position', new THREE.Float32BufferAttribute([0, -1, 0, 1, -1, 0, 1, 1, 0, 0, 1, 0], 3));
    quad.setIndex([0, 1, 2, 0, 2, 3]);
    quad.setAttribute('beamFrom', this.beam.from); quad.setAttribute('beamTo', this.beam.to); quad.setAttribute('beamTint', this.beam.tint);
    quad.instanceCount = 1;
    this.beams = new THREE.Mesh(quad, this.beamMaterial());
    for (const mesh of [this.domes, this.beams]) { mesh.frustumCulled = false; scene.add(mesh); }
    this.domes.name = 'floodlight-domes'; this.beams.name = 'floodlight-beams';
  }

  private instanced(count: number) {
    const a = new THREE.InstancedBufferAttribute(new Float32Array(count * 4), 4);
    a.setUsage(THREE.DynamicDrawUsage);
    return a;
  }

  private base(name: string, side: THREE.Side) {
    const material = new THREE.NodeMaterial();
    material.name = name;
    material.transparent = true; material.depthTest = false; material.depthWrite = false;
    material.blending = THREE.AdditiveBlending; material.side = side;
    material.fog = false; material.lights = false;
    return material;
  }

  /** Scene distance along a unit world ray through this fragment (sky: the far plane). */
  private sceneDistance(dir: Node<'vec3'>) {
    const sceneZ = perspectiveDepthToViewZ(this.depth.sample(screenUV).x, cameraNear, cameraFar).negate();
    return sceneZ.div(max(dir.dot(this.forward), 1e-3));
  }

  private domeMaterial() {
    // Back faces only: the camera may stand inside a dome's proxy box.
    const material = this.base('floodlight-domes', THREE.BackSide);
    const centre = attribute('domeCentre', 'vec4'), axis = attribute('domeAxis', 'vec4'), size = attribute('domeSize', 'vec4');
    const u = vec3(axis.x, 0, axis.y), w = vec3(axis.y.negate(), 0, axis.x);
    const corner = positionGeometry.mul(GLOW.extent).mul(this.level.w);
    const world = centre.xyz.add(u.mul(corner.x.mul(axis.z))).add(vec3(0, corner.y.mul(axis.w), 0)).add(w.mul(corner.z.mul(size.x)));
    material.vertexNode = this.projection.mul(cameraViewMatrix.mul(vec4(world, 1)));
    const at = varying(world, 'vDomeWorld'), c = varying(centre, 'vDomeCentre'), ax = varying(axis, 'vDomeAxis'), sz = varying(size, 'vDomeSize');
    const dir = normalize(at.sub(this.eye));
    const o = this.eye.sub(c.xyz);
    const fu = vec3(ax.x, 0, ax.y), fw = vec3(ax.y.negate(), 0, ax.x);
    const oa = o.dot(fu).div(ax.z), ob = o.dot(fw).div(sz.x), oy = o.y.div(ax.w);
    const da = dir.dot(fu).div(ax.z), db = dir.dot(fw).div(sz.x), dy = dir.y.div(ax.w);
    const dd = da.mul(da).add(db.mul(db)).add(dy.mul(dy)), od = oa.mul(da).add(ob.mul(db)).add(oy.mul(dy));
    const oo = oa.mul(oa).add(ob.mul(ob)).add(oy.mul(oy));
    const t0 = od.negate().div(dd), k = sqrt(dd);
    const tEnd = this.sceneDistance(dir);
    const integral = exp(oo.sub(od.mul(od).div(dd)).negate()).mul(Math.sqrt(Math.PI)).div(k.mul(2))
      .mul(erfNode(k.mul(tEnd.sub(t0))).sub(erfNode(k.mul(t0.negate()))));
    // Fog between the camera and the venue dims a far dome.
    const seen = exp(length(o).negate().div(max(this.level.z, 1)));
    const [r, g, b] = FLOOD.color;
    material.fragmentNode = vec4(vec3(r, g, b).mul(c.w.mul(sz.y).mul(this.level.x).mul(integral).mul(seen)), 1);
    return material;
  }

  private beamMaterial() {
    const material = this.base('floodlight-beams', THREE.DoubleSide);
    const from = attribute('beamFrom', 'vec4'), to = attribute('beamTo', 'vec4'), tint = attribute('beamTint', 'vec4');
    const corner = positionGeometry.xy;
    const axis = to.xyz.sub(from.xyz);
    const along = from.xyz.add(axis.mul(corner.x));
    const view = along.sub(this.eye);
    const side = normalize(cross(axis, view).add(vec3(0, 1e-6, 0)));
    const world = along.add(side.mul(corner.y.mul(this.level.w)).mul(from.w.add(to.w.sub(from.w).mul(corner.x))));
    const clipView = cameraViewMatrix.mul(vec4(world, 1));
    material.vertexNode = this.projection.mul(clipView);
    // Seen end-on a beam is the head's glare (sprites.ts), not a flat fan.
    const broadside = length(cross(normalize(axis), normalize(view)));
    const uv = varying(corner, 'vBeamUv');
    const depth = varying(clipView.z.negate(), 'vBeamDepth');
    const across = clamp(float(1).sub(uv.y.mul(uv.y)), 0, 1);
    const profile = across.mul(across).mul(pow(clamp(float(1).sub(uv.x), 0, 1), 1.5)).mul(smoothstep(0, 0.06, uv.x));
    const sceneZ = perspectiveDepthToViewZ(this.depth.sample(screenUV).x, cameraNear, cameraFar).negate();
    const unoccluded = smoothstep(depth.sub(1.5), depth.add(1.5), sceneZ);
    const near = smoothstep(4, 12, depth);
    material.fragmentNode = vec4(varying(tint.xyz.mul(smoothstep(0.15, 0.6, broadside)), 'vBeamTint')
      .mul(profile).mul(unoccluded).mul(near).mul(this.level.y), 1);
    return material;
  }

  /** Resident venues (their domes) and masts (their beams). */
  setVenues(venues: readonly AreaVenue[], masts: readonly FloodMast[]) {
    const domes = venues.slice(0, GLOW.domes);
    const c = this.dome.centre.array as Float32Array, a = this.dome.axis.array as Float32Array, s = this.dome.size.array as Float32Array;
    domes.forEach((v, i) => {
      const d = domeOf(v);
      c.set([d.x, d.y, d.z, domeScale(d, 1)], i * 4);
      a.set([d.ux, d.uz, d.sa, d.sy], i * 4);
      s.set([d.sb, v.level, 0, 0], i * 4);
    });
    if (!domes.length) { c.fill(0, 0, 4); a.set([1, 0, 0, 0], 0); s.fill(0, 0, 4); }
    this.domeCount = domes.length;
    this.domes.geometry.instanceCount = Math.max(1, domes.length);
    const byId = new Map(venues.map(v => [v.id, v]));
    const beams = beamsOf(masts, byId).slice(0, GLOW.beams);
    const f = this.beam.from.array as Float32Array, t = this.beam.to.array as Float32Array, k = this.beam.tint.array as Float32Array;
    const [r, g, b] = FLOOD.color;
    beams.forEach((beam, i) => { f.set(beam.from, i * 4); t.set(beam.to, i * 4); k.set([r * beam.level, g * beam.level, b * beam.level, 0], i * 4); });
    if (!beams.length) { f.fill(0, 0, 4); t.fill(0, 0, 4); k.fill(0, 0, 4); }
    this.beamCount = beams.length;
    this.beams.geometry.instanceCount = Math.max(1, beams.length);
    for (const attribute of [...Object.values(this.dome), ...Object.values(this.beam)]) {
      attribute.clearUpdateRanges();
      attribute.addUpdateRange(0, Math.max(1, attribute === this.dome.centre || attribute === this.dome.axis || attribute === this.dome.size ? domes.length : beams.length) * 4);
      attribute.needsUpdate = true;
    }
  }

  /** Per frame, before the pipeline renders: camera, night level, mist (0..1) and the fog distance (m). */
  update(camera: THREE.PerspectiveCamera, night: number, mist: number, fogFar: number) {
    this.projection.value.copy(camera.projectionMatrix);
    this.eye.value.setFromMatrixPosition(camera.matrixWorld);
    camera.getWorldDirection(this.forward.value);
    const on = night > 1e-3;
    this.level.value.set(on ? night * (GLOW.clear + GLOW.misty * mist) : 0, on ? night * (GLOW.beamClear + GLOW.beamMisty * mist) : 0,
      fogFar, on ? 1 : 0);
  }

  dispose() {
    for (const mesh of [this.domes, this.beams]) { mesh.removeFromParent(); mesh.geometry.dispose(); mesh.material.dispose(); }
  }
}
