import * as THREE from 'three/webgpu';
import {
  abs, attribute, bool, cameraPosition, diffuseColor, dot, float, Fn, fract, fwidth, instanceColor, log2, max, modelWorldMatrix,
  normalize, normalViewGeometry, positionLocal, positionPrevious, positionView, positionWorld, pow, select, sin, smoothstep,
  renderGroup, texture, uniform, uv, vec2, vec3, vec4, vertexColor, mix, luminance,
} from 'three/tsl';
import { cloneNodeMaterial, ditherThreshold } from '../../render/lodFade';
import { viewClearance } from '../../render/viewClearance';
import type { NatureKind } from './library';

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
  readonly lodBands = uniform(new THREE.Vector3(50, 180, 650)).setGroup(renderGroup);
  /** 0 while the camera moves (gradual cross-fades), ramping to 1 once it stands still: every band narrows to its
   *  per-tree boundary, so a settled frame keeps no dither (VegetationLod.update drives it). */
  readonly lodSharpness = uniform(0).setGroup(renderGroup);
  private variants = new Map<THREE.Material, THREE.MeshStandardNodeMaterial[]>();
  private bases = new WeakMap<THREE.Material, THREE.MeshStandardNodeMaterial>();
  private prepasses = new Map<THREE.Material, THREE.MeshBasicNodeMaterial>();
  /** Grass clumps fade out between these camera distances (m); GrassField sets them from the quality tier. */
  readonly grassRange = uniform(new THREE.Vector2(20, 35)).setGroup(renderGroup);
  /** Crown light from the baked crown-depth AO in the leaf vertex colour (0.18 in the core .. 1 on the shell,
   *  scripts/nature/trees.mjs): x albedo exponent, y ambient (indirect light) exponent, z alpha gain of the core cards
   *  (closes the sky gaps inside a crown while the rim keeps the 0.5 cut), w translucency exponent (only the outer
   *  leaves let the sun through). Calibrated against 18 summer Turku panoramas (green value p10 0.17, 6 % darker than
   *  0.15): before, forest and aerial views had p10 0.22-0.27 and no darks at all; now 0.13-0.22 and 3-13 %. */
  readonly crownLight = uniform(new THREE.Vector4(1.7, 1.55, 1.5, 2.1)).setGroup(renderGroup);
  /** Bark albedo multiplier for mid and dark barks; the white birch stays white (luminance above ~0.6). */
  readonly barkTint = uniform(new THREE.Vector3(0.78, 0.71, 0.64)).setGroup(renderGroup);
  /** Leaf and impostor tone: an rgb multiplier and w the saturation around the luminance. The panoramas' greens sit at
   *  hue 81 deg and saturation 0.41; our crowns were cool and grey (hue 90-99, 0.27-0.31 from the air). */
  readonly leafTone = uniform(new THREE.Vector4(1.09, 1, 0.82, 1.25)).setGroup(renderGroup);
  readonly bark: THREE.MeshStandardNodeMaterial;
  readonly leaves: THREE.MeshStandardNodeMaterial;
  /** The leaf cards' coverage (atlas alpha thinned in the crown core), shared by the depth prepass. */
  private readonly leafAlpha: THREE.Node<'float'>;
  readonly grass: THREE.MeshStandardNodeMaterial;
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
    const wind = (flutterScale: number) => Fn(builder => {
      const mrt = (builder as unknown as { renderer: { getMRT(): { has(name: string): boolean } | null } }).renderer.getMRT();
      if (mrt?.has('velocity')) positionPrevious.assign(positionLocal.add(offset(positionLocal, this.windTimePrevious)(flutterScale)));
      return positionLocal.add(offset(positionLocal, this.windTime)(flutterScale));
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
    this.leaves = new THREE.MeshStandardNodeMaterial({ name: 'nature-leaves', side: THREE.DoubleSide,
      alphaTest: 0.5, roughness: 0.92, metalness: 0 });
    const crownAo = vertexColor().r;
    const shell = smoothstep(0.3, 0.85, crownAo);
    const tone = (rgb: THREE.Node<'vec3'>) => { const warm = rgb.mul(this.leafTone.xyz); return mix(vec3(luminance(warm)), warm, this.leafTone.w); };
    this.leafAlpha = leaf.a.mul(mix(this.crownLight.z, float(1), shell));
    this.leaves.colorNode = vec4(tone(leaf.rgb).mul(pow(crownAo, this.crownLight.x)), this.leafAlpha);
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
    this.grass.colorNode = vec4(blade.rgb, blade.a.mul(fade));
    // The ground's baked ambient occlusion at each clump (GrassField: COLOR_1, canopy shade included).
    this.grass.aoNode = attribute('grassAmbient', 'float');
    this.grass.normalNode = normalViewGeometry;
    this.grass.positionNode = wind(0.06);
    if (lighting) {
      // Sunlight through thin leaves when looking towards the sun.
      // Scene-wide values in the shared render group: the per-object default rewrote each tree pool's buffer per pass.
      const sunDir = uniform(lighting.sunDir).setGroup(renderGroup), sunColor = uniform(lighting.sun.color).setGroup(renderGroup);
      const sunIntensity = uniform('float').onRenderUpdate(() => lighting.sun.intensity).setGroup(renderGroup);
      const back = pow(max(dot(normalize(positionWorld.sub(cameraPosition)), sunDir), 0), 2);
      const transmitted = diffuseColor.rgb.mul(sunColor).mul(sunIntensity).mul(back).mul(0.4);
      this.leaves.emissiveNode = transmitted.mul(pow(crownAo, this.crownLight.w));
      this.grass.emissiveNode = transmitted;
    }

    const imp = sources.nature_impostor;
    const impMap = imp.map!, impTex = texture(impMap, uv());
    this.impostor = new THREE.MeshStandardNodeMaterial({ name: 'nature-impostor', side: THREE.DoubleSide, alphaTest: 0.5, roughness: 0.95, metalness: 0,
      normalMap: imp.normalMap, normalScale: imp.normalScale.clone() });
    // Crossed planes fade out edge-on through a screen door that changes every frame, so temporal AA resolves it
    // into a blend (a screen-fixed pattern crawled over the crowns as the camera moved: the speckle flicker).
    // It narrows to a step as the camera settles (lodSharpness), so a settled frame keeps no dither here either.
    const facing = abs(dot(normalViewGeometry, normalize(positionView.negate())));
    const edge = max(float(1).sub(this.lodSharpness), float(1e-4)).mul(0.175);
    const visible = select(smoothstep(float(0.375).sub(edge), float(0.375).add(edge), facing).greaterThan(ditherThreshold()), float(1), float(0));
    // The impostor atlas keeps its alpha-test coverage through mip 4 (work/trees/coverage.mjs: 0.95-1.03 of mip 0);
    // the chain-link boost (1 + 0.3 per mip) made distant crowns 8-61 % denser than the meshes they replace. Only
    // mips 5 and finer-than-pixel need the 1.3x that restores mip-0 coverage.
    const level = log2(max(max(fwidth(uv().x), fwidth(uv().y)).mul(imageWidth(impMap)), 1));
    const boost = smoothstep(IMPOSTOR_BOOST.from, IMPOSTOR_BOOST.to, level).mul(IMPOSTOR_BOOST.gain).add(1);
    this.impostor.colorNode = vec4(tone(impTex.rgb), impTex.a.mul(boost).mul(visible));
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

  /** Whether a drawn part takes the foliage depth prepass (depthPrepass): the leaf cards of tree meshes, whose
   *  alpha-tested overdraw is the forests' main fragment cost; impostors and solid parts do not. */
  takesPrepass(kind: NatureKind, material: THREE.Material, impostor: boolean) {
    return kind === 'tree' && !impostor && this.baseOf(material) === this.leaves;
  }

  /** Depth-only twin of a leaf draw (a lodVariant of `leaves`): the same wind, alpha cut and screen-door mask, no
   *  lighting and no colour writes. Drawn first (renderOrder -1), it lays the crowns' nearest depth, so the lit pass
   *  (`variant`, now without depth writes) shades each visible leaf pixel about once instead of every card behind it:
   *  with depth writes off, its alpha cut no longer stops early depth rejection (IMR) or hidden-surface removal (TBDR).
   *  The plan's EqualDepth colour pass without the cut needs invariant clip positions, which r186's WGSL does not
   *  declare; instead the twin's depth sits a few depth steps back (polygon offset) and the lit pass keeps its cut and
   *  tests less-or-equal, so its own fragments always pass and no transparent texel turns opaque. */
  depthPrepass(variant: THREE.MeshStandardNodeMaterial): THREE.MeshBasicNodeMaterial {
    const cached = this.prepasses.get(variant);
    if (cached) return cached;
    const depth = new THREE.MeshBasicNodeMaterial({ name: `${variant.name}+depth`, side: variant.side, alphaTest: variant.alphaTest,
      colorWrite: false, fog: false, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 2 });
    depth.colorNode = vec4(0, 0, 0, this.leafAlpha);
    depth.positionNode = variant.positionNode;
    depth.maskNode = variant.maskNode;
    variant.depthWrite = false;
    this.prepasses.set(variant, depth); this.bases.set(depth, this.baseOf(variant) as THREE.MeshStandardNodeMaterial);
    return depth;
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
    for (const m of this.prepasses.values()) m.dispose();
    this.prepasses.clear();
    for (const m of [this.bark, this.leaves, this.grass, this.impostor, this.wood, this.metal, this.granite, this.chain]) m.dispose();
  }
}
