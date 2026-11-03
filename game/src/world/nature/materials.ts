import * as THREE from 'three/webgpu';
import {
  abs, attribute, bool, cameraPosition, diffuseColor, dot, float, Fn, fract, fwidth, instanceColor, log2, max, modelWorldMatrix,
  normalize, normalViewGeometry, positionLocal, positionView, positionWorld, pow, select, sin, smoothstep,
  renderGroup, texture, uniform, uv, vec2, vec3, vec4,
} from 'three/tsl';
import { cloneNodeMaterial, ditherThreshold } from '../../render/lodFade';

/** Tree LOD cross-fade bands as fractions of each boundary distance (near, mid, far): the fade's half width, and the
 *  per-tree stagger (from its stable tint) that keeps a tile's trees from switching together. A band lies wholly
 *  inside its boundary: the costlier level never draws farther than before; only the cheaper one starts earlier. */
export const VEGETATION_FADE = { width: [0.12, 0.08, 0.05], stagger: [0.08, 0.06, 0.03] };
/** Impostor alpha scale by mip level: 1 through mip 4, where the atlas keeps its alpha-test coverage, rising by `gain`
 *  over mips 4-5, where it thins. `impostorAlphaScale` is the CPU mirror of the shader's ramp. */
export const IMPOSTOR_BOOST = { from: 4, to: 5, gain: 0.3 } as const;
export function impostorAlphaScale(level: number) {
  const t = Math.min(1, Math.max(0, (level - IMPOSTOR_BOOST.from) / (IMPOSTOR_BOOST.to - IMPOSTOR_BOOST.from)));
  return 1 + IMPOSTOR_BOOST.gain * t * t * (3 - 2 * t);
}

/** Bench lab `treefade=S`: scale the bands before the materials are built; 0 gives the old instant swaps. */
export function scaleVegetationFade(scale: number) {
  for (let j = 0; j < 3; j++) { VEGETATION_FADE.width[j] = Math.max(1e-4, VEGETATION_FADE.width[j] * scale); VEGETATION_FADE.stagger[j] *= scale; }
}

/** ?leaves=single|alpha65|noshadow: cheaper leaf paths for the reduced tier (A/B, owner winperf). In-page probes on a
 *  Radeon Pro 560X (reduced, 910x569, riverside, beauty 11.0 ms) saved: single-sided cards 1.3 ms, unshadowed leaves
 *  1.0 ms (vegetationLod pools), alpha test 0.65 0.4 ms; all leaves together cost 2.7 ms. Decided by live replays and
 *  Jonni's look check; the losing paths go within ten merges. */
export type LeafVariant = 'single' | 'alpha65' | 'noshadow' | null;
export function parseLeafVariant(value: string | null): LeafVariant {
  if (value === null || value === '') return null;
  if (value === 'single' || value === 'alpha65' || value === 'noshadow') return value;
  throw new Error(`bad ?leaves= (single|alpha65|noshadow): ${value}`);
}
export const leafVariant: LeafVariant = typeof location === 'undefined' ? null : parseLeafVariant(new URLSearchParams(location.search).get('leaves'));

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
  /** 0 = calm, 1 = a breezy Turku afternoon. */
  readonly windStrength = uniform(1).setGroup(renderGroup);
  /** Unit vector in the XZ plane, the direction the wind blows towards. */
  readonly windDirection = uniform(new THREE.Vector2(0.8, 0.6)).setGroup(renderGroup);
  /** LOD boundaries (m, 3D): full mesh -> reduced mesh, reduced -> impostor (or the end of a two-level asset),
   *  impostor -> nothing. VegetationLod sets them each update, with budgets folded in (world/vegetationLod.ts). */
  readonly lodBands = uniform(new THREE.Vector3(50, 180, 650)).setGroup(renderGroup);
  private variants = new Map<THREE.Material, THREE.MeshStandardNodeMaterial[]>();
  private bases = new WeakMap<THREE.Material, THREE.MeshStandardNodeMaterial>();
  /** Grass clumps fade out between these camera distances (m); GrassField sets them from the quality tier. */
  readonly grassRange = uniform(new THREE.Vector2(20, 35)).setGroup(renderGroup);
  readonly bark: THREE.MeshStandardNodeMaterial;
  readonly leaves: THREE.MeshStandardNodeMaterial;
  readonly grass: THREE.MeshStandardNodeMaterial;
  readonly impostor: THREE.MeshStandardNodeMaterial;
  readonly wood: THREE.MeshStandardNodeMaterial;
  readonly metal: THREE.MeshStandardNodeMaterial;
  readonly granite: THREE.MeshStandardNodeMaterial;
  readonly chain: THREE.MeshStandardNodeMaterial;

  constructor(sources: NatureSources, lighting: NatureLighting | null, private deterministic: boolean) {
    const clock = this.windTime;
    const wind = (flutterScale: number) => Fn(() => {
      const w = attribute('_wind', 'vec4');
      const world = modelWorldMatrix.mul(vec4(positionLocal, 1)).xyz;
      const dir = this.windDirection;
      // Gust fronts travel across the city; each branch sways with its own phase; leaf cards flutter at their tips.
      const gust = sin(clock.mul(0.42).sub(dot(world.xz, dir).mul(0.035))).mul(0.3).add(0.75);
      const sway = sin(clock.mul(1.15).add(w.z.mul(6.283)).add(dot(world.xz, vec2(0.031, 0.027)))).mul(0.45).add(0.55);
      const bend = w.x.mul(sway).mul(gust).mul(this.windStrength).mul(0.3);
      const flutter = sin(clock.mul(7.3).add(w.z.mul(37)).add(world.y.mul(2.1)).add(world.x.mul(1.7)))
        .mul(w.y).mul(gust).mul(this.windStrength).mul(flutterScale);
      return positionLocal.add(vec3(dir.x.mul(bend).add(flutter), bend.mul(-0.12).add(flutter.mul(0.6)), dir.y.mul(bend).sub(flutter.mul(0.8))));
    })();
    // Chain-link mips still need runtime coverage compensation; foliage is calibrated offline per atlas cell.
    const mipAlpha = (alpha: THREE.Node<'float'>, size: number) => alpha.mul(log2(max(max(fwidth(uv().x), fwidth(uv().y)).mul(size), 1)).mul(0.3).add(1));

    const bark = sources.nature_bark;
    this.bark = new THREE.MeshStandardNodeMaterial({ name: 'nature-bark', map: bark.map, normalMap: bark.normalMap, normalScale: bark.normalScale.clone(),
      roughness: 0.92, metalness: 0, vertexColors: true });
    this.bark.positionNode = wind(0);

    const leafMap = sources.nature_leaves.map!;
    const leaf = texture(leafMap, uv());
    this.leaves = new THREE.MeshStandardNodeMaterial({ name: 'nature-leaves', side: leafVariant === 'single' ? THREE.FrontSide : THREE.DoubleSide,
      alphaTest: leafVariant === 'alpha65' ? 0.65 : 0.5, roughness: 0.92, metalness: 0, vertexColors: true });
    this.leaves.colorNode = vec4(leaf.rgb, leaf.a);
    // Crown normals (bent towards the crown's outside) light both card faces alike: no back-face flip.
    this.leaves.normalNode = normalViewGeometry;
    this.leaves.positionNode = wind(0.045);
    // Ground grass: the leaf atlas and wind, dissolving through the alpha test between grassRange.x and .y metres.
    this.grass = new THREE.MeshStandardNodeMaterial({ name: 'nature-grass', side: THREE.DoubleSide, alphaTest: 0.5, roughness: 0.95, metalness: 0, vertexColors: true });
    const fade = smoothstep(this.grassRange.y, this.grassRange.x, positionWorld.sub(cameraPosition).length());
    // Grass cards are seen edge-on: averaged (and boosted) alpha of dense blade bands would turn whole cards opaque.
    // Sample sharper mips and keep the alpha unboosted, so blades stay separate; TRAA resolves the shimmer.
    const blade = texture(leafMap, uv()).bias(float(-1.5));
    this.grass.colorNode = vec4(blade.rgb, blade.a.mul(fade));
    this.grass.normalNode = normalViewGeometry;
    this.grass.positionNode = wind(0.06);
    if (lighting) {
      // Sunlight through thin leaves when looking towards the sun.
      // Scene-wide values in the shared render group: the per-object default rewrote each tree pool's buffer per pass.
      const sunDir = uniform(lighting.sunDir).setGroup(renderGroup), sunColor = uniform(lighting.sun.color).setGroup(renderGroup);
      const sunIntensity = uniform('float').onRenderUpdate(() => lighting.sun.intensity).setGroup(renderGroup);
      const back = pow(max(dot(normalize(positionWorld.sub(cameraPosition)), sunDir), 0), 2);
      this.leaves.emissiveNode = diffuseColor.rgb.mul(sunColor).mul(sunIntensity).mul(back).mul(0.4);
      this.grass.emissiveNode = this.leaves.emissiveNode;
    }

    const imp = sources.nature_impostor;
    const impMap = imp.map!, impTex = texture(impMap, uv());
    this.impostor = new THREE.MeshStandardNodeMaterial({ name: 'nature-impostor', side: THREE.DoubleSide, alphaTest: 0.5, roughness: 0.95, metalness: 0,
      normalMap: imp.normalMap, normalScale: imp.normalScale.clone() });
    // Crossed planes fade out edge-on through a screen door that changes every frame, so temporal AA resolves it
    // into a blend (a screen-fixed pattern crawled over the crowns as the camera moved: the speckle flicker).
    const facing = abs(dot(normalViewGeometry, normalize(positionView.negate())));
    const visible = select(smoothstep(0.2, 0.55, facing).greaterThan(ditherThreshold()), float(1), float(0));
    // The impostor atlas keeps its alpha-test coverage through mip 4 (work/trees/coverage.mjs: 0.95-1.03 of mip 0);
    // the chain-link boost (1 + 0.3 per mip) made distant crowns 8-61 % denser than the meshes they replace. Only
    // mips 5 and finer-than-pixel need the 1.3x that restores mip-0 coverage.
    const level = log2(max(max(fwidth(uv().x), fwidth(uv().y)).mul(imageWidth(impMap)), 1));
    const boost = smoothstep(IMPOSTOR_BOOST.from, IMPOSTOR_BOOST.to, level).mul(IMPOSTOR_BOOST.gain).add(1);
    this.impostor.colorNode = vec4(impTex.rgb, impTex.a.mul(boost).mul(visible));
    this.impostor.positionNode = wind(0);

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
    this.windTime.value = this.deterministic ? 0 : ((seconds % period) + period) % period;
  }

  /** The material a vegetation draw of LOD `level` uses: `base` with a complementary screen-door cross-fade over the
   *  LOD boundaries on either side (lodBands). At a pixel, every level of one tree compares the same per-frame dither
   *  threshold with the same fade value, so exactly one level keeps it: no double coverage, no gap, and TRAA resolves
   *  the door into a cross-fade. The fade is per pixel (3D distance), each tree's band offset by a hash of its tint.
   *  Shadow casters (the LOD 1 proxies) never fade. Cached per material and level; disposed with the base. */
  lodVariant(base: THREE.MeshStandardNodeMaterial, level: number): THREE.MeshStandardNodeMaterial {
    let list = this.variants.get(base);
    if (!list) this.variants.set(base, list = []);
    const cached = list[level];
    if (cached) return cached;
    const variant = cloneNodeMaterial(base);
    variant.name = `${base.name}@L${level}`;
    const distance = positionWorld.sub(cameraPosition).length();
    const seed = fract(sin(dot(instanceColor, vec3(12.9898, 78.233, 37.719))).mul(43758.5453));
    // 0 -> 1 between end - 2 * width and end, where end = boundary * (1 - stagger * seed) <= boundary.
    const fade = (j: number) => {
      const band = [this.lodBands.x, this.lodBands.y, this.lodBands.z][j];
      const end = band.mul(seed.mul(-VEGETATION_FADE.stagger[j]).add(1));
      return smoothstep(end.sub(band.mul(2 * VEGETATION_FADE.width[j])), end, distance);
    };
    const threshold = ditherThreshold();
    let keep: THREE.Node<'bool'> = threshold.greaterThanEqual(fade(level));
    if (level > 0) keep = keep.and(threshold.lessThan(fade(level - 1)));
    variant.maskNode = base.maskNode ? bool(base.maskNode as THREE.Node<'bool'>).and(keep) : keep;
    variant.maskShadowNode = base.maskShadowNode ?? base.maskNode ?? bool(true);
    list[level] = variant; this.bases.set(variant, base);
    return variant;
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
    for (const m of [this.bark, this.leaves, this.grass, this.impostor, this.wood, this.metal, this.granite, this.chain]) m.dispose();
  }
}
