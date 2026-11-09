import * as THREE from 'three/webgpu';
import {
  attribute, bool, cameraPosition, clamp, diffuseColor, dot, float, floor, Fn, fract, fwidth, instanceColor, log2, max, mod, modelWorldMatrix,
  normalize, normalViewGeometry, positionLocal, positionPrevious, positionWorld, pow, select, sin, smoothstep,
  renderGroup, texture, uniform, uv, vec2, vec3, vec4, vertexColor, mix, luminance,
} from 'three/tsl';
import { cloneNodeMaterial, ditherThreshold } from '../../render/lodFade';
import { FoliageMaterial } from '../../render/foliageMaterial';
import { viewClearance } from '../../render/viewClearance';
import type { NatureKind } from './library';
import { OPEN_STEP, UNMEASURED_OPENNESS } from './species';
import { impostorNodes } from './impostor';

/** Tree LOD cross-fade bands as fractions of each boundary distance (near, mid, far): the fade's half width, and the
 *  per-tree stagger (from its stable tint) that keeps a tile's trees from switching together. A band lies wholly
 *  inside its boundary: the costlier level never draws farther than before; only the cheaper one starts earlier. */
export const VEGETATION_FADE = { width: [0.12, 0.08, 0.05], stagger: [0.08, 0.06, 0.03] };
/** The impostor atlas bakes the leaves' crown-depth AO into its albedo at full strength (scripts/nature/raster.mjs:
 *  texture x vertex colour), where the live leaves take it at crownLight.x 0.5 and draw sun through the outer leaves.
 *  The old side/top atlas needed 1.6 for the harbour's forest shore to read 0.059 of the sky's green with haze
 *  (panorama 0.059; natureref evidence/far-canopy.md). The hemi-octahedral views carry the leaves' own normals and
 *  light brighter: at 1.6 they read 1.25-1.45x LOD1's linear luminance at the 120 m hand-over (birch, spruce, oak in
 *  the nature viewer), and 1.2 matches it (natureref evidence/impostors.md). */
const IMPOSTOR_LIGHT = 1.2;
/** Impostor alpha scale at the mid boundary, rising to 1 at `ramp` times the boundary's distance (the impostor
 *  hand-over in NatureMaterials). */
export const IMPOSTOR_HANDOVER = { alpha: 0.65, ramp: 1.6 } as const;
/** Impostor alpha scale by mip level: 1 through mip 4, where the atlas keeps its alpha-test coverage, rising by `gain`
 *  over mips 4-5, where it thins. `impostorAlphaScale` is the CPU mirror of the shader's ramp. */
export const IMPOSTOR_BOOST = { from: 4, to: 5, gain: 0.3 } as const;
export function impostorAlphaScale(level: number) {
  const t = Math.min(1, Math.max(0, (level - IMPOSTOR_BOOST.from) / (IMPOSTOR_BOOST.to - IMPOSTOR_BOOST.from)));
  return 1 + IMPOSTOR_BOOST.gain * t * t * (3 - 2 * t);
}

/**
 * Crown openness: one measured value per crown (trees-lidar@3 `open`, the ground share of the lidar returns under it;
 * species.ts encodeOpenness carries it per instance) sets both how much sun its shadow lets through and how see-through
 * the crown is.
 * - Sun gaps (sunflecks): the shadow opens on `clamp(sun * openness, range)` of the ground. The leaf cards' alpha cut
 *   alone left 0.4-6 % of the ground under groves and forests sunlit, against 27.5 % of the lawn under the closed
 *   Puolanpuisto grove (panorama: shade 0.018, flecks 0.14, open lawn 0.21 linear luminance) and 13-37 % (mean 25 %) of
 *   the forest floor in the k4 frames. That grove's crowns measure 0.21 (median, t0_-1_1): `sun` = 0.275 / 0.21. With
 *   the sun share equal to the openness the grove's sunlit floor fell from 15.8 to 13.8 % of the view (overlay,
 *   puolala-park-down) against 25 % for every crown (44c02e08). A Kurala roadside row measures 0.12-0.20, solitary
 *   park trees 0.5-0.67; an unmeasured crown keeps the 25 % (species.ts UNMEASURED_OPENNESS). A gap is the stand's,
 *   not one crown's: every crown along a sun ray shares the noise, so overlapping crowns open where the densest of
 *   them does (independent per-crown gaps multiplied: 1dce0af7).
 * - Fill: a crown denser than UNMEASURED_OPENNESS scales its leaf (and impostor) alpha up to `fill` at `dense`,
 *   closing the see-through between leaves that the library's park-tree models keep; open crowns keep theirs (Kurala
 *   row, x2: the row reads as solid crowns instead of a lattice of separate leaf clumps). The view only: the shadow
 *   takes the measured share.
 * `size` (m) is the larger fleck scale on the ground.
 */
export const CROWN_GAPS = { sun: 1.3, size: 0.9, range: [0.05, 0.6], dense: 0.1, fill: 2 } as const;
/** The share of the ground a crown's shadow lets the sun through (CROWN_GAPS sun gaps). */
export function crownSunShare(open: number) {
  return Math.min(CROWN_GAPS.range[1], Math.max(CROWN_GAPS.range[0], open * CROWN_GAPS.sun));
}

/** CPU mirror of the shader's crown-gap noise at the ground point (x, z) a sun ray meets: two octaves of value noise in
 *  [0, 1]; the shadow keeps the fragments at or above `crownGapCut()`. */
export function crownGapNoise(x: number, z: number) {
  const lattice = (cx: number, cz: number) => { const v = Math.sin((cx % 289) * 127.1 + (cz % 289) * 311.7) * 43758.5453; return v - Math.floor(v); };
  const value = (px: number, pz: number) => {
    const ix = Math.floor(px), iz = Math.floor(pz), fx = px - ix, fz = pz - iz;
    const ux = fx * fx * (3 - 2 * fx), uz = fz * fz * (3 - 2 * fz);
    const a = lattice(ix, iz), b = lattice(ix + 1, iz), c = lattice(ix, iz + 1), d = lattice(ix + 1, iz + 1);
    return (a + (b - a) * ux) * (1 - uz) + (c + (d - c) * ux) * uz;
  };
  return value(x / CROWN_GAPS.size, z / CROWN_GAPS.size) * 0.65 + value(x / (CROWN_GAPS.size * 0.45) + 17.3, z / (CROWN_GAPS.size * 0.45) + 5.1) * 0.35;
}
let gapSample: number[] | null = null;
/** The noise value below which a crown's shadow opens on `share` of the ground: that quantile of the noise over a
 *  fixed grid. */
export function crownGapCut(share: number = crownSunShare(UNMEASURED_OPENNESS)) {
  if (!gapSample) {
    gapSample = [];
    for (let i = 0; i < 160; i++) for (let j = 0; j < 160; j++) gapSample.push(crownGapNoise(i * 0.37 + 0.11, j * 0.41 + 0.07));
    gapSample.sort((a, b) => a - b);
  }
  return gapSample[Math.min(gapSample.length - 1, Math.max(0, Math.floor(share * gapSample.length)))];
}
let gapCurve: [number, number, number, number] | null = null;
/** The shader's cut as a cubic in the sun share: least squares to `crownGapCut` over CROWN_GAPS.range. */
export function crownGapCurve(): [number, number, number, number] {
  if (gapCurve) return gapCurve;
  const [lo, hi] = CROWN_GAPS.range, n = 4, ata = Array.from({ length: n }, () => new Array(n).fill(0)), atb = new Array(n).fill(0);
  for (let k = 0; k <= 64; k++) {
    const p = lo + (hi - lo) * k / 64, y = crownGapCut(p), row = [1, p, p * p, p * p * p];
    for (let i = 0; i < n; i++) { atb[i] += row[i] * y; for (let j = 0; j < n; j++) ata[i][j] += row[i] * row[j]; }
  }
  for (let i = 0; i < n; i++) for (let r = i + 1; r < n; r++) {
    const f = ata[r][i] / ata[i][i];
    for (let j = i; j < n; j++) ata[r][j] -= f * ata[i][j];
    atb[r] -= f * atb[i];
  }
  const c = new Array(n).fill(0);
  for (let i = n - 1; i >= 0; i--) { let v = atb[i]; for (let j = i + 1; j < n; j++) v -= ata[i][j] * c[j]; c[i] = v / ata[i][i]; }
  return gapCurve = [c[0], c[1], c[2], c[3]];
}
/** CPU mirror of the shader's leaf alpha scale for a crown's openness (CROWN_GAPS fill). */
export function crownFill(open: number) {
  const t = Math.min(1, Math.max(0, (UNMEASURED_OPENNESS - open) / (UNMEASURED_OPENNESS - CROWN_GAPS.dense)));
  return 1 + (CROWN_GAPS.fill - 1) * t * t * (3 - 2 * t);
}

/** Bench lab `treefade=S`: scale the bands before the materials are built; 0 gives the old instant swaps. */
export function scaleVegetationFade(scale: number) {
  for (let j = 0; j < 3; j++) { VEGETATION_FADE.width[j] = Math.max(1e-4, VEGETATION_FADE.width[j] * scale); VEGETATION_FADE.stagger[j] *= scale; }
}

const imageWidth = (map: THREE.Texture) => (map.image as { width?: number } | null)?.width ?? 2048;

/** What the materials read from the scene's lighting (Environment satisfies it). */
export interface NatureLighting { sunDir: THREE.Vector3; sun: THREE.DirectionalLight }

/** The glTF materials of nature.glb, by name; their textures feed the shared node materials. */
export type NatureSources = Record<'nature_bark' | 'nature_leaves' | 'nature_impostor' | 'nature_wood' | 'nature_metal' | 'nature_granite' | 'nature_chain', THREE.MeshStandardMaterial>;

/**
 * One node material per surface type, shared by every species, tile and LOD: the pipeline count does not grow with
 * species or instance counts. Wind is vertex animation from the per-vertex `_wind` weights (x sway, y flutter, z phase)
 * driven by the shared scene clock; deterministic (QA) renders freeze it.
 */
export class NatureMaterials {
  /** All wind frequencies are multiples of 0.01 rad/s, so this wrap preserves their phases. */
  readonly windTime = uniform(0).setGroup(renderGroup);
  /** The previous frame's `windTime`, for the swaying vertices' motion vectors. */
  readonly windTimePrevious = uniform(0).setGroup(renderGroup);
  /** 0 = calm, 1 = a breezy Turku afternoon. */
  readonly windStrength = uniform(1).setGroup(renderGroup);
  /** Unit vector in the XZ plane, the direction the wind blows towards. */
  readonly windDirection = uniform(new THREE.Vector2(0.8, 0.6)).setGroup(renderGroup);
  /** LOD boundaries (m, 3D): full mesh -> reduced mesh, reduced -> impostor (or the end of a two-level asset),
   *  impostor -> nothing. VegetationLod sets them each update, with budgets folded in (world/vegetationLod.ts). */
  readonly lodBands = uniform(new THREE.Vector3(50, 120, 650)).setGroup(renderGroup);
  /** 0 while the camera moves (gradual cross-fades), ramping to 1 once it stands still: every band narrows to its
   *  per-tree boundary, so a settled frame keeps no dither (VegetationLod.update drives it). */
  readonly lodSharpness = uniform(0).setGroup(renderGroup);
  private variants = new Map<THREE.Material, THREE.MeshStandardNodeMaterial[]>();
  private bases = new WeakMap<THREE.Material, THREE.MeshStandardNodeMaterial>();
  /** Grass clumps fade out between these camera distances (m); GrassField sets them from the quality tier. */
  readonly grassRange = uniform(new THREE.Vector2(20, 35)).setGroup(renderGroup);
  /** Crown light from the baked crown-depth AO in the leaf vertex colour (0.18 in the core .. 1 on the shell,
   *  scripts/nature/trees.mjs): x albedo exponent, y ambient (indirect light) exponent, z alpha gain of the core cards
   *  (closes the sky gaps inside a crown while the rim keeps the 0.5 cut), w translucency exponent (only the outer
   *  leaves let the sun through). x and y were 1.7 and 1.55, calibrated against 18 summer Turku panoramas while the
   *  daytime bloom still veiled the frame; without the veil the Kansanpuisto crown in shade fell to 0.024 of the sky's
   *  luminance (panorama 0.064). At 0.5 it is 0.061, and its ~250 m treeline 0.12 (panorama 0.13). */
  readonly crownLight = uniform(new THREE.Vector4(0.5, 0.5, 1.5, 2.1)).setGroup(renderGroup);
  /** Bark albedo multiplier for mid and dark barks; the white birch stays white (luminance above ~0.6). */
  readonly barkTint = uniform(new THREE.Vector3(0.78, 0.71, 0.64)).setGroup(renderGroup);
  /** Leaf and impostor tone: an rgb multiplier and w the saturation around the luminance. The panoramas' greens sit at
   *  hue 81 deg and saturation 0.41; our crowns were cool and grey (hue 90-99, 0.27-0.31 from the air). */
  readonly leafTone = uniform(new THREE.Vector4(1.09, 1, 0.82, 1.25)).setGroup(renderGroup);
  readonly bark: THREE.MeshStandardNodeMaterial;
  readonly leaves: THREE.MeshStandardNodeMaterial;
  /** The leaves of the trees' shadow proxies: `leaves` whose sun shadow opens at the crown gaps (CROWN_GAPS). */
  readonly crownShadow: THREE.MeshStandardNodeMaterial;
  readonly grass: THREE.MeshStandardNodeMaterial;
  /** Opaque blade tufts (grassBlades.ts): their root-to-tip albedo in the vertex colour, the cards' wind and baked
   *  ambient. No alpha test: between grassRange.x and .y the blades sink into their ground instead of dissolving. */
  readonly grassBlade: THREE.MeshStandardNodeMaterial;
  readonly impostor: THREE.MeshStandardNodeMaterial;
  readonly wood: THREE.MeshStandardNodeMaterial;
  readonly metal: THREE.MeshStandardNodeMaterial;
  readonly granite: THREE.MeshStandardNodeMaterial;
  readonly chain: THREE.MeshStandardNodeMaterial;

  constructor(sources: NatureSources, lighting: NatureLighting | null, private deterministic: boolean) {
    // Gust fronts travel across the city; each branch sways with its own phase; leaf cards flutter at their tips.
    const offset = (position: THREE.Node<'vec3'>, clock: THREE.Node<'float'>) => {
      const w = attribute('_wind', 'vec4');
      const world = modelWorldMatrix.mul(vec4(position, 1)).xyz;
      const dir = this.windDirection;
      const gust = sin(clock.mul(0.42).sub(dot(world.xz, dir).mul(0.035))).mul(0.3).add(0.75);
      const sway = sin(clock.mul(1.15).add(w.z.mul(6.283)).add(dot(world.xz, vec2(0.031, 0.027)))).mul(0.45).add(0.55);
      const bend = w.x.mul(sway).mul(gust).mul(this.windStrength).mul(0.3);
      return (flutterScale: number) => {
        const flutter = sin(clock.mul(7.3).add(w.z.mul(37)).add(world.y.mul(2.1)).add(world.x.mul(1.7)))
          .mul(w.y).mul(gust).mul(this.windStrength).mul(flutterScale);
        return vec3(dir.x.mul(bend).add(flutter), bend.mul(-0.12).add(flutter.mul(0.6)), dir.y.mul(bend).sub(flutter.mul(0.8)));
      };
    };
    // TRAA's motion vectors compare this frame's position with `positionPrevious`. Vegetation instances never move
    // and share programs without previous instance data (render/rigidVelocity.ts), so the wind writes it: the same
    // instance position swayed at the previous frame's time. Without it every swaying vertex reported its whole
    // wind offset (up to 0.3 m) as motion and TRAA fetched the crowns' history from the wrong place.
    // `settle` moves the swayed vertex further (the blade tufts sink into their ground at the edge of the grass range).
    const wind = (flutterScale: number, settle?: () => THREE.Node<'vec3'>) => Fn(builder => {
      const mrt = (builder as unknown as { renderer: { getMRT(): { has(name: string): boolean } | null } }).renderer.getMRT();
      const extra = settle ? settle().toVar() : vec3(0);
      if (mrt?.has('velocity')) positionPrevious.assign(positionLocal.add(offset(positionLocal, this.windTimePrevious)(flutterScale)).add(extra));
      return positionLocal.add(offset(positionLocal, this.windTime)(flutterScale)).add(extra);
    })();
    // Chain-link mips still need runtime coverage compensation; foliage is calibrated offline per atlas cell.
    const mipAlpha = (alpha: THREE.Node<'float'>, size: number) => alpha.mul(log2(max(max(fwidth(uv().x), fwidth(uv().y)).mul(size), 1)).mul(0.3).add(1));

    const bark = sources.nature_bark;
    this.bark = new THREE.MeshStandardNodeMaterial({ name: 'nature-bark', map: bark.map, normalMap: bark.normalMap, normalScale: bark.normalScale.clone(),
      roughness: 0.92, metalness: 0, vertexColors: true });
    // The calibrated scans read pale and blue-grey under the sky light: warm and darken them, sparing the birch white.
    const barkTex = texture(bark.map!, uv());
    this.bark.colorNode = vec4(barkTex.rgb.mul(mix(this.barkTint, vec3(1), smoothstep(0.45, 0.68, luminance(barkTex.rgb)))), 1);
    this.bark.positionNode = wind(0);

    const leafMap = sources.nature_leaves.map!;
    const leaf = texture(leafMap, uv());
    // Double-sided, alpha test 0.5, shadow-receiving on every tier: Jonni's pick (2026-11-05, treelook2, live A/B after
    // the TRAA fix) over single-sided cards and alpha test 0.65. None differed beyond noise on the M1 Max (medium).
    // The vertex colour is the crown-depth AO; crownLight shapes it (the material applies it, not vertexColors).
    this.leaves = new FoliageMaterial({ name: 'nature-leaves', side: THREE.DoubleSide,
      alphaTest: 0.5, roughness: 0.92, metalness: 0 });
    const crownAo = vertexColor().r;
    const shell = smoothstep(0.3, 0.85, crownAo);
    // The saturation gain drove the blue of the greenest texels below zero; clamped, a leaf never absorbs other light.
    const tone = (rgb: THREE.Node<'vec3'>) => { const warm = rgb.mul(this.leafTone.xyz); return max(mix(vec3(luminance(warm)), warm, this.leafTone.w), vec3(0)); };
    // The crown's openness (species.ts encodeOpenness) from the instance tint: dense crowns fill (CROWN_GAPS).
    const step = fract(instanceColor.x.mul(OPEN_STEP));
    const openness = select(step.lessThan(0.05), float(UNMEASURED_OPENNESS), clamp(step.sub(0.1).div(0.8), 0, 1));
    const fill = smoothstep(UNMEASURED_OPENNESS, CROWN_GAPS.dense, openness).mul(CROWN_GAPS.fill - 1).add(1);
    const leafRgb = tone(leaf.rgb).mul(pow(crownAo, this.crownLight.x)), leafAlpha = leaf.a.mul(mix(this.crownLight.z, float(1), shell));
    this.leaves.colorNode = vec4(leafRgb, leafAlpha.mul(fill));
    this.leaves.aoNode = pow(crownAo, this.crownLight.y);
    // Crown normals (bent towards the crown's outside) light both card faces alike: no back-face flip.
    this.leaves.normalNode = normalViewGeometry;
    this.leaves.positionNode = wind(0.045);
    // Ground grass: the leaf atlas and wind, dissolving through the alpha test between grassRange.x and .y metres.
    this.grass = new THREE.MeshStandardNodeMaterial({ name: 'nature-grass', side: THREE.DoubleSide, alphaTest: 0.5, roughness: 0.95, metalness: 0, vertexColors: true });
    const fade = smoothstep(this.grassRange.y, this.grassRange.x, positionWorld.sub(cameraPosition).length());
    // Grass cards are seen edge-on: averaged (and boosted) alpha of dense blade bands would turn whole cards opaque.
    // Sample sharper mips and keep the alpha unboosted, so blades stay separate; TRAA resolves the shimmer.
    const blade = texture(leafMap, uv()).bias(float(-1.5));
    // GrassField's per-instance vec4 (grass.ts grassGeometry): shade, dryness, the ground's baked ambient occlusion
    // (COLOR_1, canopy shade included) and scale; the tint is the shade, yellower or bluer by the dryness.
    const clump = attribute('grassInstance', 'vec4');
    const tint = vec3(clump.y.mul(0.16).add(1), clump.y.mul(0.05).add(1), clump.y.mul(-0.1).add(0.96)).mul(clump.x);
    this.grass.colorNode = vec4(blade.rgb.mul(tint), blade.a.mul(fade));
    this.grass.aoNode = clump.z;
    this.grass.normalNode = normalViewGeometry;
    this.grass.positionNode = wind(0.06);
    this.grassBlade = new THREE.MeshStandardNodeMaterial({ name: 'nature-grass-blade', side: THREE.DoubleSide, roughness: 0.9, metalness: 0, vertexColors: true });
    this.grassBlade.colorNode = vec4(tint, 1);
    this.grassBlade.aoNode = clump.z;
    this.grassBlade.normalNode = normalViewGeometry;
    this.grassBlade.positionNode = wind(0.06, () => {
      const world = modelWorldMatrix.mul(vec4(positionLocal, 1)).xyz;
      const shown = smoothstep(this.grassRange.y, this.grassRange.x, world.sub(cameraPosition).length());
      // The vertex's height above the tuft root in metres: its model height times the instance's scale.
      const lift = max(attribute('position', 'vec3').y, 0).mul(attribute('grassInstance', 'vec4').w);
      return vec3(0, lift.mul(shown.oneMinus()).negate(), 0);
    });
    let transmitted: THREE.Node<'vec3'> | null = null;
    if (lighting) {
      // Sunlight through thin leaves when looking towards the sun.
      // Scene-wide values in the shared render group: the per-object default rewrote each tree pool's buffer per pass.
      const sunDir = uniform(lighting.sunDir).setGroup(renderGroup), sunColor = uniform(lighting.sun.color).setGroup(renderGroup);
      const sunIntensity = uniform('float').onRenderUpdate(() => lighting.sun.intensity).setGroup(renderGroup);
      const back = pow(max(dot(normalize(positionWorld.sub(cameraPosition)), sunDir), 0), 2);
      transmitted = diffuseColor.rgb.mul(sunColor).mul(sunIntensity).mul(back).mul(0.4);
      this.leaves.emissiveNode = transmitted.mul(pow(crownAo, this.crownLight.w));
      this.grass.emissiveNode = transmitted;
      this.grassBlade.emissiveNode = transmitted;
    }
    // Each fragment's sun ray meets the ground at q: every leaf card along that ray, in every crown, shares q, so a
    // gap goes through the whole stand (CROWN_GAPS).
    this.crownShadow = cloneNodeMaterial(this.leaves);
    this.crownShadow.name = 'nature-leaves@shadow';
    this.crownShadow.colorNode = vec4(leafRgb, leafAlpha);
    if (lighting) {
      const toSun = uniform(lighting.sunDir).setGroup(renderGroup);
      const lattice = (c: THREE.Node<'vec2'>) => fract(sin(dot(mod(c, 289), vec2(127.1, 311.7))).mul(43758.5453));
      const value = (p: THREE.Node<'vec2'>) => {
        const i = floor(p), f = fract(p), u = f.mul(f).mul(f.mul(-2).add(3));
        return mix(mix(lattice(i), lattice(i.add(vec2(1, 0))), u.x), mix(lattice(i.add(vec2(0, 1))), lattice(i.add(vec2(1, 1))), u.x), u.y);
      };
      const q = positionWorld.xz.sub(toSun.xz.mul(positionWorld.y.div(max(toSun.y, 0.05))));
      const size = CROWN_GAPS.size, fine = size * 0.45;
      const noise = value(q.div(size)).mul(0.65).add(value(q.div(fine).add(vec2(17.3, 5.1))).mul(0.35));
      const [c0, c1, c2, c3] = crownGapCurve(), share = clamp(openness.mul(CROWN_GAPS.sun), CROWN_GAPS.range[0], CROWN_GAPS.range[1]);
      this.crownShadow.maskShadowNode = noise.greaterThanEqual(share.mul(c3).add(c2).mul(share).add(c1).mul(share).add(c0));
    }
    this.bases.set(this.crownShadow, this.leaves);

    // Hemi-octahedral impostors (nature/impostor.ts): one camera-facing quad per tree blending the three baked views
    // nearest the view direction, each reprojected with its depth.
    const imp = sources.nature_impostor;
    const views = impostorNodes(imp.map!, imp.normalMap!);
    this.impostor = new FoliageMaterial({ name: 'nature-impostor', side: THREE.DoubleSide, alphaTest: 0.5, roughness: 0.95, metalness: 0 });
    // The atlas keeps its alpha-test coverage through mip 4; only mips 5 and finer-than-pixel need the 1.3x that
    // restores mip-0 coverage (IMPOSTOR_BOOST).
    const boost = smoothstep(IMPOSTOR_BOOST.from, IMPOSTOR_BOOST.to, views.level).mul(IMPOSTOR_BOOST.gain).add(1);
    // A dense crown's impostor fills as its leaf cards do (CROWN_GAPS fill), so it does not thin at the far band.
    // At the hand-over the impostor's coverage follows the reduced mesh's: alpha-tested leaf cards thin with distance
    // and TRAA resolves their sub-pixel leaves into a see-through crown (birch, spruce and oak drew 0.55-0.9 of the
    // impostor's coverage at 130 m in the nature viewer), where the impostor's baked crown stays solid. Its alpha
    // scales by IMPOSTOR_HANDOVER.alpha at the mid boundary and back to 1 over the next IMPOSTOR_HANDOVER.ramp of the
    // boundary's distance. (A per-frame coverage dither there instead left a stipple TRAA did not resolve.)
    const handover = mix(float(IMPOSTOR_HANDOVER.alpha), float(1),
      smoothstep(this.lodBands.y, this.lodBands.y.mul(IMPOSTOR_HANDOVER.ramp), positionWorld.sub(cameraPosition).length()));
    this.impostor.colorNode = vec4(tone(views.color.rgb).mul(IMPOSTOR_LIGHT), views.color.a.mul(boost).mul(fill).mul(handover));
    // The same sun through the outer leaves as the leaf cards (whose shell takes it at crown AO 1).
    if (transmitted) this.impostor.emissiveNode = transmitted;
    this.impostor.normalNode = views.normal;
    this.impostor.positionNode = views.position;

    const plain = (source: THREE.MeshStandardMaterial, name: string) => {
      const m = new THREE.MeshStandardNodeMaterial({ name, map: source.map, normalMap: source.normalMap, roughness: source.roughness, metalness: source.metalness });
      if (source.normalMap) m.normalScale.copy(source.normalScale);
      if (!source.map) m.color.copy(source.color);
      return m;
    };
    this.wood = plain(sources.nature_wood, 'nature-wood');
    this.metal = plain(sources.nature_metal, 'nature-metal');
    this.granite = plain(sources.nature_granite, 'nature-granite');
    // Chain-link wire: an alpha-tested tiling panel; the mip-scaled alpha keeps distant mesh from vanishing at once.
    const chainMap = sources.nature_chain.map!, wire = texture(chainMap, uv());
    this.chain = new THREE.MeshStandardNodeMaterial({ name: 'nature-chain', side: THREE.DoubleSide, alphaTest: 0.5, roughness: 0.5, metalness: 0.7 });
    this.chain.colorNode = vec4(wire.rgb, mipAlpha(wire.a, imageWidth(chainMap)));
  }

  /** Bound UTC seconds before uploading a float: epoch-sized floats lose seconds of wind motion. */
  setTime(seconds: number) {
    const period = Math.PI * 200;
    this.windTimePrevious.value = this.windTime.value;
    this.windTime.value = this.deterministic ? 0 : ((seconds % period) + period) % period;
  }

  /** The material a vegetation draw of LOD `level` uses: `base` with a complementary screen-door cross-fade over the
   *  LOD boundaries on either side (lodBands). At a pixel, every level of one tree compares the same per-frame dither
   *  threshold with the same fade value, so exactly one level keeps it: no double coverage, no gap, and TRAA resolves
   *  the door into a cross-fade. The fade is per pixel (3D distance), each tree's band offset by a hash of its tint.
   *  Shadow casters (the LOD 1 proxies) never fade. With `clears`, view clearance (render/viewClearance.ts) also
   *  dithers it out between the camera and the player; only tree crowns take it (`clearsView`). Cached per material,
   *  level and clearance; disposed with the base. */
  lodVariant(base: THREE.MeshStandardNodeMaterial, level: number, clears = false): THREE.MeshStandardNodeMaterial {
    let list = this.variants.get(base);
    if (!list) this.variants.set(base, list = []);
    const slot = level * 2 + (clears ? 1 : 0);
    const cached = list[slot];
    if (cached) return cached;
    const variant = cloneNodeMaterial(base);
    variant.name = `${base.name}@L${level}${clears ? '+clear' : ''}`;
    const distance = positionWorld.sub(cameraPosition).length();
    const seed = fract(sin(dot(instanceColor, vec3(12.9898, 78.233, 37.719))).mul(43758.5453));
    // 0 -> 1 between end - 2 * width and end, where end = boundary * (1 - stagger * seed) <= boundary; the width
    // shrinks to a step as the camera settles (lodSharpness).
    const softness = max(float(1).sub(this.lodSharpness), float(1e-4));
    const fade = (j: number) => {
      const band = [this.lodBands.x, this.lodBands.y, this.lodBands.z][j];
      const end = band.mul(seed.mul(-VEGETATION_FADE.stagger[j]).add(1));
      return smoothstep(end.sub(band.mul(2 * VEGETATION_FADE.width[j]).mul(softness)), end, distance);
    };
    const threshold = ditherThreshold();
    let keep: THREE.Node<'bool'> = threshold.greaterThanEqual(fade(level));
    if (level > 0) keep = keep.and(threshold.lessThan(fade(level - 1)));
    // View clearance does not depend on the level, so the levels stay complementary inside the cleared region.
    // Shadows keep the unfaded mask below.
    if (clears) keep = keep.and(viewClearance.keep());
    variant.maskNode = base.maskNode ? bool(base.maskNode as THREE.Node<'bool'>).and(keep) : keep;
    variant.maskShadowNode = base.maskShadowNode ?? base.maskNode ?? bool(true);
    list[slot] = variant; this.bases.set(variant, base);
    return variant;
  }

  /** Whether a part clears the view: the leaf cards of a tree's mesh levels. Trunks and branches (bark), fences
   *  (wood, metal, chain), hedges, shrubs and impostors stay solid (Jonni, 2026-11-05: fences and trunks must not hide). */
  clearsView(kind: NatureKind, material: THREE.Material, impostor: boolean) {
    return kind === 'tree' && !impostor && material === this.leaves;
  }

  /** The shared material a LOD variant was made from (itself for a base material). */
  baseOf(material: THREE.Material): THREE.Material { return this.bases.get(material) ?? material; }

  /** The shared material for a glTF material name of nature.glb. */
  forSource(name: string): THREE.MeshStandardNodeMaterial {
    const material = ({ nature_bark: this.bark, nature_leaves: this.leaves, nature_impostor: this.impostor, nature_wood: this.wood,
      nature_metal: this.metal, nature_granite: this.granite, nature_chain: this.chain } as Record<string, THREE.MeshStandardNodeMaterial>)[name];
    if (!material) throw new Error(`nature.glb: unknown material ${name}`);
    return material;
  }

  dispose() {
    for (const list of this.variants.values()) for (const m of list) m?.dispose();
    this.variants.clear();
    for (const m of [this.bark, this.leaves, this.crownShadow, this.grass, this.grassBlade, this.impostor, this.wood, this.metal, this.granite, this.chain]) m.dispose();
  }
}
