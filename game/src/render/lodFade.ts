// Screen-door LOD dissolves for far-band representations (distant scenery cells, block LOD shells).
//
// A far-band model that hands over to, or takes over from, a nearer representation is drawn for a
// moment on top of it: a "dissolve twin" of its material carries a depth bias towards the camera and
// keeps only the pixels whose ordered-dither threshold lies below the object's coverage. Coverage
// ramps over a fixed time, so the far model appears or vanishes pixel by pixel instead of popping.
// The nearer model stays opaque underneath with its own unchanged programs: only the cheap far-band
// materials ever gain a discard, and only while a fade runs. The dither offset changes every frame,
// so TRAA resolves it into a smooth cross-fade (GTA V's LOD-pair dither, without touching HD shaders).
import * as THREE from 'three/webgpu';
import { bool, dot, float, floor, fract, frameId, screenCoordinate, uniform, vec2 } from 'three/tsl';

/** Seconds a far-band model takes to dissolve in or out. */
export const LOD_FADE_SECONDS = 0.6;
/** Depth-bias units of the twin. A bias, not a vertex pull: positionNode would move facade programs onto r186's
 *  deformed velocity path and past the 16 inter-stage varyings (render/rigidVelocity.ts). With the 0.1 m near
 *  plane one unit is about 0.6e-6 * d^2 metres: 0.3 m at 100 m, 2.6 m at 300 m, 7 m at 500 m, enough to sit in
 *  front of facade relief and the far terrain's 0.5 m simplification error at each band edge. */
export const LOD_FADE_BIAS = { units: -48, slope: -2 } as const;

/** Per-draw coverage; meshes outside a fade never carry the twin, so its value is 1 for them. */
const coverage = uniform(1).onObjectUpdate(({ object }) => (object?.userData.lodCoverage as number | undefined) ?? 1);

/** 4x4 ordered dither in [0, 1): 16 distinct thresholds, every 2x2 quad covered evenly. */
function bayer2(a: THREE.Node<'vec2'>) {
  const cell = floor(a);
  return fract(dot(cell, vec2(.5, cell.y.mul(.75))));
}
export function ditherThreshold() {
  const pixel = screenCoordinate.xy;
  const ordered = bayer2(pixel.mul(.5)).mul(.25).add(bayer2(pixel));
  // A golden-ratio offset per frame visits every threshold; TRAA averages the pattern into coverage.
  return fract(ordered.add(float(frameId).mod(1024).mul(.61803398875)));
}

const twins = new WeakMap<THREE.Material, THREE.Material>();
const twinSet = new WeakSet<THREE.Material>();
/** Twins belong to their base material, never to a model that happens to hold one while it fades. */
export const isDissolveTwin = (material: THREE.Material) => twinSet.has(material);

/** The dissolve twin of a far-band node material; cached per material and disposed with it. */
export function dissolveTwin(material: THREE.Material): THREE.Material {
  let twin = twins.get(material);
  if (twin) return twin;
  const base = material as THREE.NodeMaterial;
  if (!base.isNodeMaterial) return material;
  const node = base.clone() as THREE.NodeMaterial;
  node.name = `${base.name}:dissolve`;
  const keep = ditherThreshold().lessThan(coverage);
  node.maskNode = base.maskNode ? bool(base.maskNode as THREE.Node<'bool'>).and(keep) : keep;
  // Shadows stay whole while the pixels dissolve; the nearer model casts the same shape meanwhile.
  node.maskShadowNode = base.maskShadowNode ?? base.maskNode ?? bool(true);
  node.polygonOffset = true;
  node.polygonOffsetUnits = LOD_FADE_BIAS.units; node.polygonOffsetFactor = LOD_FADE_BIAS.slope;
  twin = node;
  twins.set(material, twin); twinSet.add(twin);
  material.addEventListener('dispose', () => { twins.delete(material); node.dispose(); });
  return twin;
}

interface Fade {
  from: number; to: number; start: number; seconds: number;
  meshes: { mesh: THREE.Mesh; material: THREE.Material | THREE.Material[] }[];
  done?: () => void;
}

/** Drives coverage for dissolving roots. A root fades as a whole: every mesh below it shares one coverage. */
export class LodFades {
  private fades = new Map<THREE.Object3D, Fade>();
  private clock = 0;
  get size() { return this.fades.size; }

  has(root: THREE.Object3D) { return this.fades.has(root); }

  /** Current coverage of a fading root (1 when it is not fading). */
  coverage(root: THREE.Object3D) {
    const fade = this.fades.get(root);
    return fade ? this.value(fade) : 1;
  }

  /** Start (or reverse from its current coverage) a dissolve of `root` towards `to`; `done` runs once it lands. */
  start(root: THREE.Object3D, from: number, to: number, done?: () => void, now = performance.now(), seconds = LOD_FADE_SECONDS) {
    this.clock = now;
    const running = this.fades.get(root);
    if (running) {
      from = this.value(running);
      running.done = undefined;
      this.restore(root, running);
    }
    if (from === to || seconds <= 0) { done?.(); return; }
    const meshes: Fade['meshes'] = [];
    root.traverse(object => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh || (mesh as THREE.InstancedMesh).isInstancedMesh) return;
      meshes.push({ mesh, material: mesh.material });
      mesh.material = Array.isArray(mesh.material) ? mesh.material.map(dissolveTwin) : dissolveTwin(mesh.material);
      mesh.userData.lodCoverage = from;
    });
    this.fades.set(root, { from, to, start: this.clock, seconds, meshes, done });
  }

  /** Abort a dissolve and restore its materials, without running its completion. Call before disposing `root`. */
  cancel(root: THREE.Object3D) {
    const fade = this.fades.get(root);
    if (fade) this.restore(root, fade);
  }

  /** Abort every dissolve on `root` or below it (a model about to be disposed). */
  cancelWithin(root: THREE.Object3D) {
    for (const [fading, fade] of [...this.fades]) {
      for (let node: THREE.Object3D | null = fading; node; node = node.parent)
        if (node === root) { this.restore(fading, fade); break; }
    }
  }

  clear() { for (const [root, fade] of [...this.fades]) this.restore(root, fade); }

  /** Advance every dissolve to `now` (ms). Completions run after their materials are restored. */
  update(now = performance.now()) {
    this.clock = now;
    if (!this.fades.size) return;
    const finished: (() => void)[] = [];
    for (const [root, fade] of [...this.fades]) {
      const value = this.value(fade);
      if (value === fade.to) {
        this.restore(root, fade);
        if (fade.done) finished.push(fade.done);
        continue;
      }
      for (const { mesh } of fade.meshes) mesh.userData.lodCoverage = value;
    }
    for (const done of finished) done();
  }

  private value(fade: Fade) {
    const t = Math.min(1, Math.max(0, (this.clock - fade.start) / (fade.seconds * 1000)));
    return t >= 1 ? fade.to : fade.from + (fade.to - fade.from) * t;
  }

  private restore(root: THREE.Object3D, fade: Fade) {
    for (const { mesh, material } of fade.meshes) { mesh.material = material; delete mesh.userData.lodCoverage; }
    this.fades.delete(root);
  }
}

/** The world's dissolves, advanced once per frame by the streaming update. */
export const lodFades = new LodFades();
