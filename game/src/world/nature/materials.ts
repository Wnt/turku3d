import * as THREE from 'three/webgpu';
import {
  abs, attribute, cameraPosition, diffuseColor, dot, float, Fn, fwidth, interleavedGradientNoise, log2, max, modelWorldMatrix,
  normalize, normalViewGeometry, positionLocal, positionView, positionWorld, pow, screenCoordinate, select, sin, smoothstep,
  texture, uniform, uv, vec2, vec3, vec4,
} from 'three/tsl';

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
  readonly windTime = uniform(0);
  /** 0 = calm, 1 = a breezy Turku afternoon. */
  readonly windStrength = uniform(1);
  /** Unit vector in the XZ plane, the direction the wind blows towards. */
  readonly windDirection = uniform(new THREE.Vector2(0.8, 0.6));
  /** Grass clumps fade out between these camera distances (m); GrassField sets them from the quality tier. */
  readonly grassRange = uniform(new THREE.Vector2(20, 35));
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
    // Alpha-tested mips lose coverage; scale alpha with the mip level so far crowns keep their density.
    const mipAlpha = (alpha: THREE.Node<'float'>, size: number) => alpha.mul(log2(max(max(fwidth(uv().x), fwidth(uv().y)).mul(size), 1)).mul(0.3).add(1));

    const bark = sources.nature_bark;
    this.bark = new THREE.MeshStandardNodeMaterial({ name: 'nature-bark', map: bark.map, normalMap: bark.normalMap, normalScale: bark.normalScale.clone(),
      roughness: 0.92, metalness: 0, vertexColors: true });
    this.bark.positionNode = wind(0);

    const leafMap = sources.nature_leaves.map!;
    const leaf = texture(leafMap, uv());
    this.leaves = new THREE.MeshStandardNodeMaterial({ name: 'nature-leaves', side: THREE.DoubleSide, alphaTest: 0.5, roughness: 0.92, metalness: 0, vertexColors: true });
    this.leaves.colorNode = vec4(leaf.rgb, mipAlpha(leaf.a, imageWidth(leafMap)));
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
      const sunDir = uniform(lighting.sunDir), sunColor = uniform(lighting.sun.color);
      const sunIntensity = uniform('float').onRenderUpdate(() => lighting.sun.intensity);
      const back = pow(max(dot(normalize(positionWorld.sub(cameraPosition)), sunDir), 0), 2);
      this.leaves.emissiveNode = diffuseColor.rgb.mul(sunColor).mul(sunIntensity).mul(back).mul(0.4);
      this.grass.emissiveNode = this.leaves.emissiveNode;
    }

    const imp = sources.nature_impostor;
    const impMap = imp.map!, impTex = texture(impMap, uv());
    this.impostor = new THREE.MeshStandardNodeMaterial({ name: 'nature-impostor', side: THREE.DoubleSide, alphaTest: 0.5, roughness: 0.95, metalness: 0,
      normalMap: imp.normalMap, normalScale: imp.normalScale.clone() });
    // Crossed planes fade out edge-on through a screen-space dither that temporal AA resolves into a blend.
    const facing = abs(dot(normalViewGeometry, normalize(positionView.negate())));
    const visible = select(smoothstep(0.2, 0.55, facing).greaterThan(interleavedGradientNoise(screenCoordinate.xy)), float(1), float(0));
    this.impostor.colorNode = vec4(impTex.rgb, mipAlpha(impTex.a, imageWidth(impMap)).mul(visible));
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

  /** The shared material for a glTF material name of nature.glb. */
  forSource(name: string): THREE.MeshStandardNodeMaterial {
    const material = ({ nature_bark: this.bark, nature_leaves: this.leaves, nature_impostor: this.impostor, nature_wood: this.wood,
      nature_metal: this.metal, nature_granite: this.granite, nature_chain: this.chain } as Record<string, THREE.MeshStandardNodeMaterial>)[name];
    if (!material) throw new Error(`nature.glb: unknown material ${name}`);
    return material;
  }

  dispose() { for (const m of [this.bark, this.leaves, this.grass, this.impostor, this.wood, this.metal, this.granite, this.chain]) m.dispose(); }
}
