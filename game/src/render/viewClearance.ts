import * as THREE from 'three/webgpu';
import { clamp, dot, float, fract, frameId, max, mix, positionView, renderGroup, screenCoordinate, smoothstep, uniform, vec2 } from 'three/tsl';

/**
 * View clearance: vegetation between the camera and the player (and the road just ahead of them) fades to see-through,
 * as in most third-person games. Client-local rendering only.
 *
 * The test runs per fragment in view space, so it ignores TRAA's projection jitter and holds for any lens:
 * - a tapered capsule in tangent space (view x/y over depth) from the player's body to a point ahead of them along
 *   the direction of travel; each end's radius is a world radius over its own depth, so the region keeps its size in
 *   the world as the camera zooms;
 * - only fragments nearer to the camera than the capsule's depth at that point, less a margin, fade, so the trees
 *   behind the player and the road keep drawing;
 * - everywhere on screen, foliage within NEAR metres of the camera fades (the huge leaf cards a low or tree-side
 *   camera otherwise fills the screen with).
 *
 * The fade is a coverage fraction for a per-frame screen door (interleaved gradient noise, which TRAA resolves into a
 * fine grain where the LOD fades' ordered pattern left a visible grid over a lasting fade). It is independent of the
 * LOD level, so the tree cross-fade stays complementary inside the cleared region. The cleared region moves
 * continuously with the player and eases in and out over `easeSeconds`, so trees never pop.
 * Three vec4 uniforms in the shared render group, written once per frame; no per-instance CPU work. Renders from other
 * cameras (reflections, probes) read zero, and shadow passes keep the unfaded mask (`maskShadowNode`), so faded trees
 * still cast their full shadows.
 */
export const VIEW_CLEARANCE = {
  /** Clear radius around the body (m) on foot and in a vehicle; inside `core` of it the fade reaches `maxFade`. */
  radius: { foot: 1.5, car: 3.2 },
  /** The point ahead: base metres, seconds of travel at the current speed, and a cap (m). */
  ahead: { foot: [4, 0.7, 9], car: [7, 0.8, 26] },
  /** The ahead end's world radius relative to the body's. */
  aheadRadius: 1.4,
  core: 0.6,
  maxFade: 1,
  /** Fragments fade only when nearer than the capsule's depth less `depthMargin` (m), over a further `depthBand`. */
  depthMargin: 0.3,
  depthBand: 0.8,
  /** Foliage nearer than near[0] metres fades by `maxFade`, ramping out by near[1]. */
  near: [1.4, 2.6],
  easeSeconds: 0.35,
};

export type ClearanceMode = 'foot' | 'car';

export interface ClearanceTarget {
  /** Body centre (world): the character's chest, or the vehicle's centre. */
  body: THREE.Vector3;
  /** World velocity (m/s); the camera's horizontal view direction stands in below walking pace. */
  velocity: THREE.Vector3;
  mode: ClearanceMode;
}

const ZERO = new THREE.Vector4();

class ViewClearance {
  /** Tangent-space body end: x, y, depth (m), radius. */
  readonly start = uniform(new THREE.Vector4(0, 0, 1, 0)).setGroup(renderGroup);
  /** Tangent-space ahead end: x, y, depth (m), radius. */
  readonly end = uniform(new THREE.Vector4(0, 0, 1, 0)).setGroup(renderGroup);
  private readonly live = new THREE.Vector4();
  /** Player strength (eased), near-fade strength, unused, unused; zero for every camera but the play camera. */
  readonly strength = uniform(new THREE.Vector4()).setGroup(renderGroup)
    .onRenderUpdate(({ camera }) => camera !== null && camera === this.camera ? this.live : ZERO);
  /** Diagnostics (`__game.viewClearance.enabled = false`): the same programs with nothing faded, for before/after. */
  enabled = true;
  private camera: THREE.Camera | null = null;
  private eased = 0;
  private readonly direction = new THREE.Vector3(0, 0, -1);
  private aheadLength = 0;
  private radius = VIEW_CLEARANCE.radius.foot;
  private readonly a = new THREE.Vector3();
  private readonly b = new THREE.Vector3();
  private readonly want = new THREE.Vector3();

  /** Once per frame before rendering, after the camera has its final pose. A null target (no player: fly mode, QA
   *  poses) eases the player fade out; the near fade follows `camera` alone. */
  update(camera: THREE.PerspectiveCamera, target: ClearanceTarget | null, dt: number) {
    this.camera = this.enabled ? camera : null;
    const k = 1 - Math.exp(-Math.max(0, dt) / VIEW_CLEARANCE.easeSeconds);
    this.eased += ((target ? 1 : 0) - this.eased) * k;
    this.live.set(this.eased, 1, 0, 0);
    if (!target) return;
    camera.updateMatrixWorld();
    const v = target.velocity, speed = Math.hypot(v.x, v.z);
    const [base, seconds, cap] = VIEW_CLEARANCE.ahead[target.mode];
    if (speed > 1) this.want.set(v.x / speed, 0, v.z / speed);
    else {
      camera.getWorldDirection(this.want).setY(0);
      if (this.want.lengthSq() < 1e-6) this.want.set(0, 0, -1);
      this.want.normalize();
    }
    // Direction, length and radius ease too: a turn or a mode change sweeps the region instead of jumping it.
    // By angle: a lerp through a reversal would pass through zero length and lose the direction.
    const from = Math.atan2(this.direction.x, this.direction.z), to = Math.atan2(this.want.x, this.want.z);
    const turn = Math.atan2(Math.sin(to - from), Math.cos(to - from)), heading = from + turn * k;
    this.direction.set(Math.sin(heading), 0, Math.cos(heading));
    this.aheadLength += (Math.min(cap, base + seconds * speed) - this.aheadLength) * k;
    this.radius += (VIEW_CLEARANCE.radius[target.mode] - this.radius) * k;
    const view = camera.matrixWorldInverse;
    this.a.copy(target.body).applyMatrix4(view);
    // The ahead end sits on the road: the body point carried forward, at foot (or wheel) height.
    this.b.copy(target.body).addScaledVector(this.direction, this.aheadLength);
    this.b.y -= target.mode === 'car' ? 0.5 : 1;
    this.b.applyMatrix4(view);
    project(this.a, this.radius, this.start.value);
    // Walking towards the camera can carry the ahead point behind it: the capsule then shrinks to the body.
    if (-this.b.z < 0.5) this.end.value.copy(this.start.value);
    else project(this.b, this.radius * VIEW_CLEARANCE.aheadRadius, this.end.value);
  }

  /** Whether this fragment survives the clearance screen door. */
  keep(): THREE.Node<'bool'> {
    // Interleaved gradient noise (Jimenez 2014), shifted every frame.
    const pixel = screenCoordinate.xy.add(float(frameId).mod(64).mul(5.588238));
    const threshold = fract(fract(dot(pixel, vec2(0.06711056, 0.00583715))).mul(52.9829189));
    return threshold.greaterThanEqual(this.fade());
  }

  /** How much of this fragment to remove (0 keeps it, 1 removes it). */
  fade(): THREE.Node<'float'> {
    const p = positionView, depth = p.z.negate();
    const q = p.xy.div(max(depth, float(1e-3)));
    const a = this.start, b = this.end;
    const ab = b.xy.sub(a.xy);
    const t = clamp(dot(q.sub(a.xy), ab).div(max(dot(ab, ab), float(1e-8))), 0, 1);
    const radius = mix(a.w, b.w, t), along = mix(a.z, b.z, t);
    const distance = q.sub(mix(a.xy, b.xy, t)).length();
    const screen = float(1).sub(smoothstep(radius.mul(VIEW_CLEARANCE.core), radius, distance));
    const nearer = float(1).sub(smoothstep(along.sub(VIEW_CLEARANCE.depthMargin + VIEW_CLEARANCE.depthBand),
      along.sub(VIEW_CLEARANCE.depthMargin), depth));
    const player = screen.mul(nearer).mul(this.strength.x);
    const near = float(1).sub(smoothstep(VIEW_CLEARANCE.near[0], VIEW_CLEARANCE.near[1], depth)).mul(this.strength.y);
    return max(player, near).mul(VIEW_CLEARANCE.maxFade);
  }
}

/** A view-space point (in front of the camera) to tangent space: x, y over depth, depth, and a world radius over depth. */
function project(view: THREE.Vector3, radius: number, out: THREE.Vector4) {
  const depth = Math.max(0.3, -view.z);
  out.set(view.x / depth, view.y / depth, depth, radius / depth);
}

export const viewClearance = new ViewClearance();
