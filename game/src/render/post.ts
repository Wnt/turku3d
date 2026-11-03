import { decodePrepassNormal } from './normalDecode';
import { bakedAmbientContext } from './bakedVisibility';
import { installRigidVelocity } from './rigidVelocity';
import { labelGpuPass } from './gpuPasses';
import { PuddleReflections } from './weather/puddleReflections';
import { GameRenderer } from './shadowMaterials';
import { compileModel, nextRenderDepth, isPrecompiling } from './compileModel';
import * as THREE from 'three/webgpu';
import { pass, mrt, output as sceneColor, sample, screenUV, materialReference, normalView, normalViewGeometry, negateOnBackSide, velocity, packNormalToRGB,
  context, convertToTexture, float, mix, vec3, vec4, dot, renderOutput, smoothstep, uniform } from 'three/tsl';
import { ao } from 'three/addons/tsl/display/GTAONode.js';
import { traa } from 'three/addons/tsl/display/TRAANode.js';
import { fxaa } from 'three/addons/tsl/display/FXAANode.js';
import { bloom } from 'three/addons/tsl/display/BloomNode.js';
import { QUALITY, type QualityTier } from './quality';
import { MESOPIC, type TimecycleSample } from './timecycle';
import { WATER_HISTORY } from './waterHistory';

// r186 exposes this public method; the corresponding DefinitelyTyped declaration omits it.
const flowContext = (node: THREE.WebGPURenderer['contextNode']) =>
  (node as unknown as { getFlowContextData(): Record<string, unknown> }).getFlowContextData();

export type Antialiasing = 'off' | 'taa' | 'fxaa';

/** An additive HDR layer composited after the temporal resolve; it tests its own fragments against `sceneDepth`. */
export interface PostOverlay {
  overlay(renderer: THREE.WebGPURenderer, camera: THREE.Camera, sceneDepth: THREE.Texture): { color: THREE.Node<'vec3'>; resource: { dispose(): void } };
}

export class PostProcessing {
  private pipeline: THREE.RenderPipeline;
  private pre: ReturnType<typeof pass> | null;
  private beauty: ReturnType<typeof pass>;
  private beautyContext: THREE.WebGPURenderer['contextNode'];
  private baseContext: THREE.WebGPURenderer['contextNode'];
  private preDepth = 2;
  private beautyDepth = 2;
  private resources: { dispose(): void }[];
  private glow: ReturnType<typeof bloom> | null;
  /** The timecycle's bloom strength is in medium-tier units. */
  private bloomScale: number;
  /** Grading saturation of dim light (mesopic vision); the tier's day saturation by day. */
  private dimSaturation: THREE.UniformNode<'float', number>;
  private daySaturation: number;
  readonly puddles: PuddleReflections | null;

  constructor(private renderer: THREE.WebGPURenderer, private scene: THREE.Scene, private camera: THREE.PerspectiveCamera, tier: QualityTier, aa: Antialiasing = 'taa',
    overlay: PostOverlay | null = null) {
    installRigidVelocity();
    const q = QUALITY[tier];
    const taa = aa === 'taa', wet = tier === 'reduced' || tier === 'medium' || tier === 'high';
    // GTAO darkens beauty's indirect light, so it needs depth and normals before beauty: a second geometry pass.
    // Without GTAO, TRAA depth/velocity and puddle normals come from beauty's own MRT: one geometry pass, not two.
    const pre = this.pre = q.aoSamples > 0 ? pass(scene, camera) : null;
    this.baseContext = renderer.contextNode;
    const receiver = materialReference('userData.weatherReceiver', 'float') as unknown as THREE.Node<'float'>;
    if (pre) {
      labelGpuPass(pre.renderTarget, 'prepass');
      pre.transparent = false;
      if (renderer instanceof GameRenderer) pre.overrideMaterial = renderer.normalPrepass.marker;
      const updatePre = pre.updateBefore.bind(pre);
      pre.updateBefore = frame => {
        if (isPrecompiling(renderer)) return;
        this.preDepth = nextRenderDepth(renderer);
        const previous = renderer.contextNode; renderer.contextNode = this.baseContext;
        try { updatePre(frame); } finally { renderer.contextNode = previous; }
      };
      const normalAndReceiver = vec4(packNormalToRGB(normalView), receiver);
      pre.setMRT(mrt(taa ? { output: normalAndReceiver, velocity } : { output: normalAndReceiver }));
      pre.getTexture('output').type = THREE.UnsignedByteType;
      // PassNode lazily clones the output texture for new MRT attachments. Normals fit in
      // UNORM8; NDC motion needs signed subpixel precision or TRAA leaves old car/wheel edges.
      if (taa) pre.getTexture('velocity').type = THREE.HalfFloatType;
    }
    let ambient: ReturnType<typeof ao> | null = null;
    if (q.aoSamples > 0 && pre) {
      const depth = pre.getTextureNode('depth');
      const normals = sample(uv => decodePrepassNormal(pre.getTextureNode().sample(uv)));
      ambient = ao(depth, normals, camera);
      // r186 setup() replaces GTAO's fullscreen material graph for every surface that references it.
      // This effect belongs to one renderer/camera; retain its graph across streamed surface builders.
      const setupAmbient = ambient.setup.bind(ambient);
      let ambientOutput: ReturnType<typeof ambient.setup> | undefined;
      ambient.setup = builder => ambientOutput ??= setupAmbient(builder);
      const updateAmbient = ambient.updateBefore.bind(ambient);
      ambient.updateBefore = frame => {
        if (isPrecompiling(renderer)) return;
        const previous = renderer.contextNode; renderer.contextNode = this.baseContext;
        try { updateAmbient(frame); } finally { renderer.contextNode = previous; }
      };
      ambient.resolutionScale = q.aoScale;
      ambient.samples.value = q.aoSamples;
      ambient.radius.value = 0.6;
      ambient.thickness.value = 0.2;
      ambient.useTemporalFiltering = aa === 'taa';
    }
    const beauty = this.beauty = pass(scene, camera);
    labelGpuPass(beauty.renderTarget, 'beauty');
    if (!pre && (taa || wet)) {
      // Geometric normals as in the prepass: puddle rays must not follow paving normal maps. Like the opaque-only
      // prepass, a blended surface (river, decals, car glass) must keep the opaque normal and motion beneath it:
      // with material blending these alpha-0 outputs leave the destination; opaque draws still overwrite.
      const normal = vec4(packNormalToRGB(negateOnBackSide(normalViewGeometry)), receiver);
      const motion = vec4(velocity as unknown as THREE.Node<'vec2'>, 0, 0);
      const outputs = mrt({ output: sceneColor, ...(wet ? { normal } : {}), ...(taa ? { velocity: motion } : {}) });
      for (const name of ['normal', 'velocity']) outputs.setBlendMode(name, new THREE.BlendMode(THREE.MaterialBlending));
      beauty.setMRT(outputs);
      if (wet) beauty.getTexture('normal').type = THREE.UnsignedByteType;
      if (taa) beauty.getTexture('velocity').type = THREE.HalfFloatType;
    }
    // Depth, velocity and normal/receiver source for TRAA, puddles and the light sprites.
    const geometry = pre ?? beauty, normals = pre ? pre.getTextureNode() : wet ? beauty.getTextureNode('normal') : null;
    // Indirect light only: baked static visibility everywhere, plus light GTAO where the tier keeps it
    // (facade albedo already contains some occlusion).
    const screenAO = ambient ? mix(1, ambient.getTextureNode().sample(screenUV).r, q.aoStrength) as THREE.Node<'float'> : null;
    this.beautyContext = context({ ...flowContext(renderer.contextNode), ...flowContext(bakedAmbientContext(screenAO)) });
    const updateBeauty = beauty.updateBefore.bind(beauty);
    beauty.updateBefore = frame => {
      if (isPrecompiling(renderer)) return;
      this.beautyDepth = nextRenderDepth(renderer);
      const previous = renderer.contextNode;
      renderer.contextNode = this.beautyContext;
      try { updateBeauty(frame); } finally { renderer.contextNode = previous; }
    };
    const puddles = this.puddles = wet && normals
      ? new PuddleReflections(renderer, beauty.getTextureNode(), geometry.getTextureNode('depth'),
        coord => decodePrepassNormal(normals.sample(coord).level(float(0))),
        coord => normals.sample(coord).level(float(0)).a, camera, this.baseContext) : null;
    const puddleLight = puddles && normals ? puddles.reconstruct(geometry.getTextureNode('depth'),
      coord => decodePrepassNormal(normals.sample(coord).level(float(0))),
      coord => normals.sample(coord).level(float(0)).a) : null;
    const reflected = puddleLight ? convertToTexture(vec4(beauty.rgb.mul(float(1).sub(puddleLight.a))
      .add(puddleLight.rgb), beauty.a), null, null, { depthBuffer: false }) : null;
    const temporal = taa ? traa(reflected ?? beauty, geometry.getTextureNode('depth'), geometry.getTextureNode('velocity'), camera) : null;
    if (temporal) temporal.useSubpixelCorrection = q.temporalSubpixel;
    // River pixels read the previous frame from TRAA's history (render/waterHistory.ts); r186 keeps it private.
    const history = temporal ? (temporal as unknown as { _historyRenderTarget: THREE.RenderTarget })._historyRenderTarget : null;
    WATER_HISTORY.depth = history?.depthTexture ?? null;
    WATER_HISTORY.color = history?.texture ?? null;
    WATER_HISTORY.beautyVelocity = !!temporal && !pre;
    const color = temporal ?? reflected ?? beauty;
    const glow = this.glow = q.bloomStrength > 0 ? bloom(color, q.bloomStrength, 0.3, 1.5) : null;
    this.bloomScale = q.bloomStrength / QUALITY.medium.bloomStrength;
    glow?.setResolutionScale(q.bloomScale);
    const bloomed = glow ? color.rgb.add(glow.rgb) : color.rgb;
    // Light sprites (render/lights) join after TRAA and bloom: no jitter, no history smear.
    const lights = overlay?.overlay(renderer, camera, geometry.getTexture('depth'));
    const hdr = lights ? bloomed.add(lights.color) : bloomed;
    const luminance = dot(hdr, vec3(0.2126, 0.7152, 0.0722));
    // Night vision loses colour in dim light while lamps, windows and their pools keep theirs: below the mesopic
    // range the saturation falls to the timecycle's night value (a uniform, so dusk compiles nothing new).
    this.daySaturation = q.saturation;
    this.dimSaturation = uniform(q.saturation);
    const saturation = mix(this.dimSaturation, q.saturation, smoothstep(MESOPIC.luminance[0], MESOPIC.luminance[1], luminance));
    const graded = mix(vec3(luminance), hdr, saturation).mul(vec3(1.012, 1, 0.99));
    const output = vec4(graded, 1);
    // FXAA's contrast thresholds require display-referred sRGB, unlike temporal HDR resolve.
    const spatial = aa === 'fxaa' ? fxaa(renderOutput(output)) : null;
    this.pipeline = new THREE.RenderPipeline(renderer, spatial ?? output);
    this.pipeline.outputColorTransform = !spatial;
    this.resources = [beauty, this.pipeline];
    if (pre) this.resources.push(pre);
    if (ambient) this.resources.push(ambient);
    if (puddles) this.resources.push(puddles);
    if (reflected) this.resources.push(reflected);
    if (glow) this.resources.push(glow);
    if (temporal) this.resources.push(temporal);
    if (spatial) this.resources.push(spatial);
    if (lights) this.resources.push(lights.resource);
  }

  /** Models prepared before any shadow pass had drawn, so with no cascade state to compile their casters against. */
  private shadowless = new Set<THREE.Object3D>();

  async prepare(model: THREE.Object3D) {
    if (this.pre) await compileModel(this.renderer, model, this.scene, this.camera, this.pre.renderTarget, this.pre.getMRT(), this.baseContext, true, this.preDepth, this.pre.overrideMaterial);
    await compileModel(this.renderer, model, this.scene, this.camera, this.beauty.renderTarget, this.beauty.getMRT(), this.beautyContext, false, this.beautyDepth);
    // Shadow casters as a cascade last drew them; the cascades share programs (render/shadowMaterials.ts).
    const shadow = this.renderer instanceof GameRenderer ? this.renderer.shadowPasses.values().next().value : undefined;
    if (shadow) await compileModel(this.renderer, model, this.scene, shadow.camera, shadow.target, shadow.mrt, shadow.context, false, shadow.depth, shadow.override, true);
    else if (this.renderer instanceof GameRenderer) this.shadowless.add(model);
  }

  /** Once a shadow pass has drawn: the models `prepare` met before it, to prepare again (their casters only build
   *  now). Startup preparation precedes the first shadow frame: in replay 20261103-160452 every fleet design built its
   *  shadow program inside a frame on first entering a cascade (45 in-frame ShadowMap builds, 3 prepared). */
  takeShadowless(): THREE.Object3D[] {
    if (!this.shadowless.size || !(this.renderer instanceof GameRenderer) || !this.renderer.shadowPasses.size) return [];
    const models = [...this.shadowless];
    this.shadowless.clear();
    return models;
  }

  /** The prepass and scene-pass render targets, so the bench probe (qa/bench.ts) can attribute draws to passes. */
  get passTargets() { return { prepass: this.pre?.renderTarget, beauty: this.beauty.renderTarget }; }
  /** Night grading through uniforms (no new pipeline): dim-light saturation, and bloom stronger with a lower
   *  threshold. Mobile has no bloom pass; its night relies on emissive light alone. */
  grade(sample: TimecycleSample) {
    this.dimSaturation.value = this.daySaturation + (MESOPIC.saturation - this.daySaturation) * sample.night;
    if (!this.glow) return;
    this.glow.strength.value = sample.bloomStrength * this.bloomScale;
    this.glow.threshold.value = sample.bloomThreshold;
  }

  render() {
    // Prepass, beauty, cascades and river reflection share world transforms for this frame.
    // Avoid recursively recomputing every object's matrices in each Renderer.render call.
    this.scene.updateMatrixWorld();
    const autoUpdate = this.scene.matrixWorldAutoUpdate;
    this.scene.matrixWorldAutoUpdate = false;
    // First-use draws of the live frame compile their pipelines asynchronously (render/asyncPipelines.ts).
    const live = this.renderer instanceof GameRenderer ? this.renderer : null;
    if (live) live.liveFrame = true;
    try { this.pipeline.render(); } finally { this.scene.matrixWorldAutoUpdate = autoUpdate; if (live) live.liveFrame = false; }
  }
  dispose() { for (const resource of this.resources) resource.dispose(); }
}
