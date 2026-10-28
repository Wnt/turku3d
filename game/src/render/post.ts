import * as THREE from 'three/webgpu';
import { pass, mrt, sample, screenUV, normalView, velocity, packNormalToRGB, unpackRGBToNormal,
  builtinAOContext, mix, vec3, vec4, dot, renderOutput } from 'three/tsl';
import { ao } from 'three/addons/tsl/display/GTAONode.js';
import { traa } from 'three/addons/tsl/display/TRAANode.js';
import { fxaa } from 'three/addons/tsl/display/FXAANode.js';
import { bloom } from 'three/addons/tsl/display/BloomNode.js';
import { QUALITY, type QualityTier } from './quality';

export type Antialiasing = 'off' | 'taa' | 'fxaa';

export class PostProcessing {
  private pipeline: THREE.RenderPipeline;
  private resources: { dispose(): void }[];

  constructor(renderer: THREE.WebGPURenderer, private scene: THREE.Scene, camera: THREE.PerspectiveCamera, tier: QualityTier, aa: Antialiasing = 'taa') {
    const q = QUALITY[tier];
    const pre = pass(scene, camera);
    pre.transparent = false;
    pre.setMRT(mrt({ output: packNormalToRGB(normalView), velocity }));
    pre.getTexture('output').type = THREE.UnsignedByteType;
    // PassNode lazily clones the output texture for new MRT attachments. Normals fit in
    // UNORM8; NDC motion needs signed subpixel precision or TRAA leaves old car/wheel edges.
    pre.getTexture('velocity').type = THREE.HalfFloatType;
    const depth = pre.getTextureNode('depth');
    const normals = sample(uv => unpackRGBToNormal(pre.getTextureNode().sample(uv)));
    const ambient = ao(depth, normals, camera);
    ambient.resolutionScale = q.aoScale;
    ambient.samples.value = q.aoSamples;
    ambient.radius.value = 0.6;
    ambient.thickness.value = 0.2;
    ambient.useTemporalFiltering = aa === 'taa';
    const beauty = pass(scene, camera);
    // Apply light GTAO to indirect light only: facade albedo already contains some occlusion.
    beauty.contextNode = builtinAOContext(mix(1, ambient.getTextureNode().sample(screenUV).r, q.aoStrength));
    const temporal = aa === 'taa' ? traa(beauty, depth, pre.getTextureNode('velocity'), camera) : null;
    if (temporal) temporal.useSubpixelCorrection = q.temporalSubpixel;
    const color = temporal ?? beauty;
    const glow = bloom(color, q.bloomStrength, 0.3, 1.5);
    glow.setResolutionScale(q.bloomScale);
    const hdr = color.rgb.add(glow.rgb);
    const luminance = dot(hdr, vec3(0.2126, 0.7152, 0.0722));
    const graded = mix(vec3(luminance), hdr, q.saturation).mul(vec3(1.012, 1, 0.99));
    const output = vec4(graded, 1);
    // FXAA's contrast thresholds require display-referred sRGB, unlike temporal HDR resolve.
    const spatial = aa === 'fxaa' ? fxaa(renderOutput(output)) : null;
    this.pipeline = new THREE.RenderPipeline(renderer, spatial ?? output);
    this.pipeline.outputColorTransform = !spatial;
    this.resources = [pre, ambient, beauty, glow, this.pipeline];
    if (temporal) this.resources.push(temporal);
    if (spatial) this.resources.push(spatial);
  }

  render() {
    // Prepass, beauty, cascades and river reflection share world transforms for this frame.
    // Avoid recursively recomputing every object's matrices in each Renderer.render call.
    this.scene.updateMatrixWorld();
    const autoUpdate = this.scene.matrixWorldAutoUpdate;
    this.scene.matrixWorldAutoUpdate = false;
    try { this.pipeline.render(); } finally { this.scene.matrixWorldAutoUpdate = autoUpdate; }
  }
  dispose() { for (const resource of this.resources) resource.dispose(); }
}
