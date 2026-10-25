import * as THREE from 'three/webgpu';
import { pass, mrt, sample, screenUV, normalView, velocity, packNormalToRGB, unpackRGBToNormal,
  builtinAOContext, mix, vec3, vec4, dot } from 'three/tsl';
import { ao } from 'three/addons/tsl/display/GTAONode.js';
import { traa } from 'three/addons/tsl/display/TRAANode.js';
import { bloom } from 'three/addons/tsl/display/BloomNode.js';
import { QUALITY, type QualityTier } from './quality';

export class PostProcessing {
  private pipeline: THREE.RenderPipeline;
  private resources: { dispose(): void }[];

  constructor(renderer: THREE.WebGPURenderer, scene: THREE.Scene, camera: THREE.PerspectiveCamera, tier: QualityTier) {
    const q = QUALITY[tier];
    const pre = pass(scene, camera);
    pre.transparent = false;
    pre.setMRT(mrt({ output: packNormalToRGB(normalView), velocity }));
    pre.getTexture('output').type = THREE.UnsignedByteType;
    const depth = pre.getTextureNode('depth');
    const normals = sample(uv => unpackRGBToNormal(pre.getTextureNode().sample(uv)));
    const ambient = ao(depth, normals, camera);
    ambient.resolutionScale = q.aoScale;
    ambient.samples.value = q.aoSamples;
    ambient.radius.value = 0.6;
    ambient.thickness.value = 0.2;
    ambient.useTemporalFiltering = true;
    const beauty = pass(scene, camera);
    // Apply light GTAO to indirect light only: facade albedo already contains some occlusion.
    beauty.contextNode = builtinAOContext(mix(1, ambient.getTextureNode().sample(screenUV).r, q.aoStrength));
    const temporal = traa(beauty, depth, pre.getTextureNode('velocity'), camera);
    temporal.useSubpixelCorrection = q.temporalSubpixel;
    const glow = bloom(temporal, q.bloomStrength, 0.3, 1.5);
    glow.setResolutionScale(q.bloomScale);
    const hdr = temporal.rgb.add(glow.rgb);
    const luminance = dot(hdr, vec3(0.2126, 0.7152, 0.0722));
    const graded = mix(vec3(luminance), hdr, q.saturation).mul(vec3(1.012, 1, 0.99));
    this.pipeline = new THREE.RenderPipeline(renderer, vec4(graded, 1));
    this.resources = [pre, ambient, beauty, temporal, glow, this.pipeline];
  }

  render() { this.pipeline.render(); }
  dispose() { for (const resource of this.resources) resource.dispose(); }
}
