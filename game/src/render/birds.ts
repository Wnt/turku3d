// The birds of world/birds/flocks.ts as one instanced draw: a low-poly bird (body, tail, two two-section wings) placed,
// turned, banked and flapped in the vertex stage from four per-instance attributes the CPU rewrites every frame.
import * as THREE from 'three/webgpu';
import { attribute, cos, cross, float, max, min, mix, normalize, sign, sin, smoothstep, vec3 } from 'three/tsl';
import { BIRD_STRIDE, birdConditions, groupsNear, sampleBirds, type BirdView, type Group } from '../world/birds/flocks';

/** Where the wing bends (the wrist), in half-spans from the body. */
const WRIST = 0.54;
/** Seconds between looking up which groups are near, and between ground probes under the camera. */
const GROUPS_EVERY = 0.5, GROUND_EVERY = 1;

/** One bird with a unit wingspan, nose towards +z, in the xz plane. `birdPart`: x = signed position along the span
 *  (-1 left tip .. 1 right tip; 0 on the body), y = 1 on the body and tail. */
function birdGeometry() {
  const position: number[] = [], part: number[] = [];
  const tri = (a: number[], b: number[], c: number[], body: number) => {
    for (const v of [a, b, c]) { position.push(v[0], v[1], v[2]); part.push(body ? 0 : v[0] * 2, body); }
  };
  // Body: a stretched octahedron, thickest ahead of the middle; then a tail fan.
  const nose = [0, 0, 0.2], rear = [0, 0, -0.16], ring = [[0.045, 0, 0.04], [0, 0.04, 0.04], [-0.045, 0, 0.04], [0, -0.035, 0.04]];
  for (let i = 0; i < 4; i++) { tri(nose, ring[i], ring[(i + 1) % 4], 1); tri(rear, ring[(i + 1) % 4], ring[i], 1); }
  tri([0, 0, -0.1], [0.05, 0, -0.23], [-0.05, 0, -0.23], 1);
  // Wings: root to wrist (a quad), wrist to a swept-back tip.
  for (const side of [1, -1]) {
    const root = 0.03 * side, wrist = WRIST * 0.5 * side, tip = 0.5 * side;
    tri([root, 0, 0.1], [wrist, 0, 0.085], [wrist, 0, -0.045], 0); tri([root, 0, 0.1], [wrist, 0, -0.045], [root, 0, -0.07], 0);
    tri([wrist, 0, 0.085], [tip, 0, -0.03], [wrist, 0, -0.045], 0);
  }
  const base = new THREE.BufferGeometry(), geometry = new THREE.InstancedBufferGeometry();
  base.setAttribute('position', new THREE.Float32BufferAttribute(position, 3));
  base.setAttribute('birdPart', new THREE.Float32BufferAttribute(part, 2));
  base.computeVertexNormals();
  geometry.attributes = { ...base.attributes };
  return geometry;
}

export class Birds {
  readonly mesh: THREE.Mesh;
  private readonly geometry: THREE.InstancedBufferGeometry;
  private readonly data: Float32Array;
  private readonly buffer: THREE.InstancedInterleavedBuffer;
  private readonly cache = new Map<number, Group | null>();
  private groups: Group[] = [];
  private groupsAt = -Infinity;
  private ground: number | null = null;
  private groundTarget: number | null = null;
  private groundAt = -Infinity;
  private last = 0;
  private shown = 0;

  /** Ground height under (x, z), or null where it is not known yet (set once physics runs). Birds fly relative to
   *  the ground under the camera, so they clear the hills without a terrain model of their own. */
  probe: ((x: number, z: number) => Promise<number | null>) | null = null;

  constructor(scene: THREE.Object3D, private readonly budget = 384) {
    const geometry = this.geometry = birdGeometry();
    this.data = new Float32Array(budget * BIRD_STRIDE);
    this.buffer = new THREE.InstancedInterleavedBuffer(this.data, BIRD_STRIDE);
    this.buffer.setUsage(THREE.DynamicDrawUsage);
    geometry.setAttribute('birdAt', new THREE.InterleavedBufferAttribute(this.buffer, 3, 0));
    geometry.setAttribute('birdFlight', new THREE.InterleavedBufferAttribute(this.buffer, 4, 3));
    geometry.setAttribute('birdWings', new THREE.InterleavedBufferAttribute(this.buffer, 4, 7));
    geometry.setAttribute('birdTone', new THREE.InterleavedBufferAttribute(this.buffer, 3, 11));
    // One hidden bird keeps the draw alive, so its pipeline is built during loading and not at the first gull.
    geometry.instanceCount = 1;

    const local = attribute('position', 'vec3'), part = attribute('birdPart', 'vec2');
    const at = attribute('birdAt', 'vec3'), flight = attribute('birdFlight', 'vec4'), wings = attribute('birdWings', 'vec4'), tone = attribute('birdTone', 'vec3');
    const span = wings.x, phase = wings.y, beat = wings.z, chord = wings.w;
    // The wing: the inner section swings about the shoulder, the outer about the wrist, a quarter beat behind; a
    // gliding wing is held a little above level with drooping tips.
    const reach = part.x.abs().mul(0.5), inner = min(reach, WRIST * 0.5), outer = max(reach.sub(WRIST * 0.5), 0);
    const gliding = float(1).sub(smoothstep(0, 0.25, beat));
    const shoulder = sin(phase).mul(beat).add(mix(0.06, 0.16, gliding));
    const wrist = shoulder.add(sin(phase.sub(1.3)).mul(beat).mul(0.7)).sub(gliding.mul(0.3));
    const isBody = part.y;
    const across = mix(sign(part.x).mul(inner.mul(cos(shoulder)).add(outer.mul(cos(wrist)))), local.x, isBody);
    // The body rises as the wings come down.
    const lift = mix(inner.mul(sin(shoulder)).add(outer.mul(sin(wrist))), local.y, isBody).sub(sin(phase).mul(beat).mul(0.025));
    const along = local.z.mul(mix(chord, 1, isBody));
    // Flight frame: forward along the velocity, the wings rolled into the turn.
    const forward = normalize(flight.xyz), level = normalize(cross(forward, vec3(0, 1, 0))), top = cross(level, forward);
    const right = level.mul(cos(flight.w)).add(top.mul(sin(flight.w))), up = top.mul(cos(flight.w)).sub(level.mul(sin(flight.w)));
    const material = new THREE.MeshStandardNodeMaterial({ side: THREE.DoubleSide, flatShading: true, roughness: 1, metalness: 0 });
    material.positionNode = at.add(right.mul(across).add(up.mul(lift)).add(forward.mul(along)).mul(span));
    const feathers = mix(mix(tone.y, tone.z, smoothstep(0.66, 0.8, part.x.abs())), tone.x, isBody);
    material.colorNode = vec3(feathers, feathers, feathers.mul(0.97));
    material.name = 'birds';
    this.mesh = new THREE.Mesh(geometry, material);
    this.mesh.name = 'birds'; this.mesh.frustumCulled = false;
    this.mesh.castShadow = false; this.mesh.receiveShadow = false;
    scene.add(this.mesh);
  }

  /** Once per frame. `seconds`: the shared clock, so every player sees the same birds; `sunElevation` in degrees. */
  update(camera: THREE.Camera, seconds: number, sunElevation: number, rain: number) {
    const p = camera.position;
    if (this.probe && Math.abs(seconds - this.groundAt) >= GROUND_EVERY) {
      this.groundAt = seconds;
      void this.probe(p.x, p.z).then(y => { if (y !== null && Number.isFinite(y)) this.groundTarget = y; }, () => {});
    }
    // Follow the ground slowly: a roof or a bridge under the camera must not lift the whole sky's birds at once.
    const step = Math.min(1, Math.abs(seconds - this.last) / 4); this.last = seconds;
    if (this.groundTarget !== null) this.ground = this.ground === null ? this.groundTarget : this.ground + (this.groundTarget - this.ground) * step;
    const view: BirdView = { x: p.x, y: p.y, z: p.z, ground: this.ground ?? Math.min(p.y - 2, 40) };
    if (Math.abs(seconds - this.groupsAt) >= GROUPS_EVERY) { this.groupsAt = seconds; this.groups = groupsNear(view, this.cache); }
    this.shown = sampleBirds(this.groups, view, seconds, birdConditions(sunElevation, rain), this.data, this.budget);
    if (this.shown === 0) this.data.fill(0, 0, BIRD_STRIDE);
    this.geometry.instanceCount = Math.max(1, this.shown);
    this.buffer.clearUpdateRanges(); this.buffer.addUpdateRange(0, Math.max(1, this.shown) * BIRD_STRIDE);
    this.buffer.needsUpdate = true;
  }

  /** Birds drawn this frame, and the groups near (window.__game diagnostics, tests). */
  snapshot() { return { birds: this.shown, groups: this.groups.length, ground: this.ground }; }

  dispose() { this.mesh.removeFromParent(); this.geometry.dispose(); (this.mesh.material as THREE.Material).dispose(); this.cache.clear(); }
}
