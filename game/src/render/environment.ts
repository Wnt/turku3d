// Analytic Rayleigh/Mie sky with a two-layer cloud deck, PMREM sky IBL, street-scale CSM and the timecycle-driven day/night look.
import * as THREE from 'three/webgpu';
import { SkyMesh } from 'three/addons/objects/SkyMesh.js';
import { CSMShadowNode } from 'three/addons/csm/CSMShadowNode.js';
import { cameraPosition, float, max, mix, normalize, positionWorld, pow, uniform, vec4 } from 'three/tsl';
import { sunPosition, sunVector } from '../core/sun';
import { localHour } from '../core/dayCycle';
import { QUALITY, type QualityTier } from './quality';
import { REFLECTION_LAYER } from './reflections';
import { TILE_SHADOW_LAYER } from '../world/shadowBatch';
import { litFraction, timecycle, type TimecycleSample } from './timecycle';
import { nightUniforms } from './nightLights';
import { CloudLayer } from './clouds';

/** Below this the sky is dark enough that IBL only needs a slow refresh. */
const DEEP_NIGHT = -12;

export class Environment {
  readonly sky = new SkyMesh();
  readonly clouds: CloudLayer;
  readonly sun = new THREE.DirectionalLight(0xfff2e0, 3);
  /** Artificial ambient (cool sky, warm street bounce). Always present; intensity 0 by day. */
  readonly hemisphere = new THREE.HemisphereLight(0xffffff, 0xffffff, 0);
  /** Fixed DynamicLighting point slots (QUALITY[tier].lights), created once with intensity 0. Drive their position,
   *  colour and intensity; never add, remove, hide or shadow-enable lights at runtime (that recompiles). */
  readonly lamps: { point: THREE.PointLight[] };
  readonly sunDir = new THREE.Vector3(0, 1, 0);
  readonly csm: CSMShadowNode;
  screenshotDistance = 0;
  sunElevation = 90;
  sunAzimuth = 0;
  /** Clock hour and timecycle sample of the applied state. */
  hour = 12;
  sample: TimecycleSample = timecycle(90);
  /** True while sun intensity is 0 and the cascades are not re-rendered. */
  shadowsFrozen = false;
  private pmrem: THREE.PMREMGenerator;
  private environmentTarget: THREE.RenderTarget | null = null;
  private skyScene = new THREE.Scene();
  private iblDirty = true;
  private iblElevation = Number.NaN;
  private state = '';
  /** Twilight and night sky colours (timecycle rows), added to the analytic sky. */
  private skyZenith = uniform(new THREE.Color(0, 0, 0));
  private skyHorizon = uniform(new THREE.Color(0, 0, 0));

  /** Reused sky PMREM; mobile water samples it without rendering the city a second time. */
  get environmentMap() { return this.environmentTarget?.texture ?? null; }
  /** 0 by day, 1 at full night: the same value the shaders read from nightUniforms.night. */
  get nightFactor() { return this.sample.night; }

  constructor(private scene: THREE.Scene, private renderer: THREE.WebGPURenderer, tier: QualityTier, deterministic = false) {
    const q = QUALITY[tier];
    this.pmrem = new THREE.PMREMGenerator(renderer);
    this.sky.scale.setScalar(4500);
    this.sky.turbidity.value = 2.5;
    this.sky.rayleigh.value = 1.5;
    // SkyMesh's own single-layer clouds stay off: CloudLayer draws ours over the composed sky.
    this.sky.cloudCoverage.value = 0;
    this.sky.cloudSpeed.value = 0;
    // The analytic sky is black below about -10 degrees. A twilight/night term (the blue hour, then zenith blue to a
    // city-lit horizon) joins its single graph through uniforms; the IBL clone shares the material and the term.
    const scattered = this.sky.material.colorNode as THREE.Node<'vec4'>;
    const view = normalize(positionWorld.sub(cameraPosition));
    const horizon = pow(float(1).sub(max(view.y, 0)), 4);
    const night = mix(this.skyZenith, this.skyHorizon, horizon);
    this.clouds = new CloudLayer(deterministic);
    this.sky.material.colorNode = vec4(this.clouds.composite(view, scattered.rgb.add(night)), 1);
    // Mesh.clone shares the sky material/uniforms; keep one IBL source mesh across time edits.
    this.skyScene.add(this.sky.clone());
    // The sky is a back-faced unit box scaled to a 2250 m half-extent (corners 3.9 km, inside the 6 km far plane).
    // Its colour depends only on the view direction, so it is centred on whichever camera draws it: it stays around
    // the player kilometres from the origin and looks the same at the origin.
    this.sky.frustumCulled = false;
    this.sky.layers.enable(REFLECTION_LAYER);
    this.sky.onBeforeRender = (_renderer, _scene, camera) => {
      this.sky.position.setFromMatrixPosition(camera.matrixWorld);
      this.sky.updateMatrixWorld();
    };
    scene.add(this.sky);
    this.sun.castShadow = true;
    this.sun.layers.enable(REFLECTION_LAYER);
    this.sun.shadow.mapSize.set(q.shadowSize, q.shadowSize);
    this.sun.shadow.camera.near = 0.5;
    this.sun.shadow.camera.far = 600;
    // CSM clones this camera into every cascade, retaining the shadow-only tile batches.
    this.sun.shadow.camera.layers.enable(TILE_SHADOW_LAYER);
    this.sun.shadow.bias = -0.00015;
    this.sun.shadow.normalBias = 0.025;
    const far = q.splits[q.splits.length - 1];
    this.csm = new CSMShadowNode(this.sun, { cascades: q.splits.length, maxFar: far,
      mode: 'custom', lightMargin: 100,
      customSplitsCallback: (_count, _near, cameraFar, breaks) => {
        for (const distance of q.splits) breaks.push(Math.min(1, distance / cameraFar));
      } });
    this.csm.fade = true;
    this.sun.shadow.shadowNode = this.csm;
    scene.add(this.sun, this.sun.target);
    this.hemisphere.position.set(0, 1, 0);
    this.lamps = { point: Array.from({ length: q.lights.point }, () => new THREE.PointLight(0xffd6a0, 0, 40, 2)) };
    for (const light of [this.hemisphere, ...this.lamps.point]) {
      light.layers.enable(REFLECTION_LAYER);
      scene.add(light);
    }
    scene.fog = new THREE.Fog(0xb8cde0, 500, 3500);
    scene.environmentIntensity = 0.35;
  }

  /** Keep the sky behind distant geometry and move haze beyond the requested photo scenery. */
  setScreenshotDistance(camera: THREE.PerspectiveCamera, distance: number) {
    this.screenshotDistance = Number.isFinite(distance) ? Math.max(0, Math.min(8000, distance)) : 0;
    camera.far = Math.max(6000, this.screenshotDistance * 4);
    camera.updateProjectionMatrix();
    this.sky.scale.setScalar(Math.max(4500, this.screenshotDistance * 4));
    this.applyFog();
    this.resize();
  }

  /** Astronomical sun at a calendar instant (QA poses, ?time=ISO): the same timecycle rows apply. */
  setTime(date: Date) {
    const { azimuth, elevation } = sunPosition(date);
    this.setSun(azimuth, elevation, localHour(date));
  }

  /** Apply one sun position (true azimuth/elevation, degrees) and clock hour: lights, sky, fog, exposure, windows. */
  setSun(azimuth: number, elevation: number, hour: number) {
    const state = `${azimuth},${elevation},${hour}`;
    if (state === this.state) return;
    this.state = state;
    this.sunElevation = elevation; this.sunAzimuth = azimuth; this.hour = hour;
    const d = sunVector(azimuth, elevation);
    this.sunDir.set(d.x, d.y, d.z).normalize();
    this.sky.sunPosition.value.copy(this.sunDir);
    const s = this.sample = timecycle(elevation);
    // The sun stays visible and shadowed at night with zero intensity: toggling a light recompiles every material.
    this.sun.intensity = s.sunIntensity;
    this.sun.color.setRGB(...s.sunColor);
    this.hemisphere.intensity = s.hemisphereIntensity;
    this.hemisphere.color.setRGB(...s.hemisphereSky);
    this.hemisphere.groundColor.setRGB(...s.hemisphereGround);
    this.scene.environmentIntensity = s.environmentIntensity;
    this.renderer.toneMappingExposure = s.exposure;
    if (this.scene.fog instanceof THREE.Fog) this.scene.fog.color.setRGB(...s.fogColor);
    this.skyZenith.value.setRGB(...s.skyZenith);
    this.skyHorizon.value.setRGB(...s.skyHorizon);
    this.clouds.apply(s, this.sunDir);
    this.applyFog();
    nightUniforms.night.value = s.night;
    nightUniforms.hour.value = hour;
    nightUniforms.litFraction.value = litFraction(hour);
    this.iblDirty = true;
  }

  private applyFog() {
    if (!(this.scene.fog instanceof THREE.Fog)) return;
    const radius = this.screenshotDistance;
    this.scene.fog.near = radius || this.sample.fogNear;
    this.scene.fog.far = radius ? radius * 1.5 : this.sample.fogFar;
  }

  /** Degrees the visible sky moved since the last IBL. Below -12 degrees the sky no longer changes. */
  skyDrift() {
    if (Number.isNaN(this.iblElevation)) return Infinity;
    return Math.abs(Math.max(this.sunElevation, DEEP_NIGHT) - Math.max(this.iblElevation, DEEP_NIGHT));
  }

  /** Regenerate only after a time edit, not every frame. Reuse the target so time edits keep material bindings and shader programs warm. */
  /** Browser only: fetch the cloud noise; the sky IBL is renewed once the clouds appear. */
  async loadClouds() {
    await this.clouds.load();
    this.iblDirty = true;
  }

  updateIBL() {
    if (!this.iblDirty) return;
    this.iblDirty = false;
    this.iblElevation = this.sunElevation;
    this.environmentTarget = this.pmrem.fromScene(this.skyScene, 0, 0.1, 6000,
      { size: 128, renderTarget: this.environmentTarget });
    this.scene.environment = this.environmentTarget.texture;
  }

  /** While the sun has no intensity its cascades are not re-rendered (3 passes on medium, 4 on high). Startup
   *  warmup passes `allowFreeze = false` so every shadow pipeline compiles before play, whatever the hour. */
  updateShadows(allowFreeze: boolean) {
    this.shadowsFrozen = allowFreeze && this.sun.intensity === 0;
    for (const light of this.csm.lights) light.shadow!.autoUpdate = !this.shadowsFrozen;
  }

  /** CSM itself snaps each cascade's light-space centre to shadow texels. */
  follow(focus: THREE.Vector3) {
    this.sun.target.position.copy(focus);
    this.sun.position.copy(focus).addScaledVector(this.sunDir, 200);
    this.sun.target.updateMatrixWorld();
    this.sun.updateMatrixWorld();
  }

  resize() { if (this.csm.camera) this.csm.updateFrustums(); }

  dispose() {
    this.csm.dispose();
    this.environmentTarget?.dispose();
    this.pmrem.dispose();
    this.sky.geometry.dispose();
    this.sky.material.dispose();
    this.clouds.dispose();
    this.scene.remove(this.sky, this.sun, this.sun.target, this.hemisphere, ...this.lamps.point);
    this.scene.environment = null;
  }
}
