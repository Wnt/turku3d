// Analytic Rayleigh/Mie sky, PMREM sky IBL, street-scale CSM and the timecycle-driven day/night look.
import * as THREE from 'three/webgpu';
import { AnalyticSky } from './analyticSky';
import { CSMShadowNode } from 'three/addons/csm/CSMShadowNode.js';
import { Atmosphere } from './atmosphere';
import { sunPosition, sunVector } from '../core/sun';
import { localHour } from '../core/dayCycle';
import { QUALITY, type QualityTier } from './quality';
import { REFLECTION_LAYER } from './reflections';
import { TILE_SHADOW_CHUNK_LAYER, TILE_SHADOW_LAYER, TILE_SHADOW_WHOLE_LAYER } from '../world/shadowBatch';
import type { CasterVolumes } from '../world/vegetationLod';

/** Layers a sun cascade's camera sees: everything on layer 0, the shadow-only layer (vegetation proxies), and of a
 *  tile's shadow batch the per-chunk casters in the nearest cascade (fine culling in a 16 m map), the whole-tile
 *  caster in every other one (one draw per tile instead of one per 62.5 m chunk). `chunked`: every cascade takes the
 *  chunks, when the shadows end close (Environment.setShadowReach) and the outer cascades span a few chunks each. */
export function cascadeLayerMask(cascade: number, chunked = false) {
  const chunks = cascade === 0 || chunked || tileShadows.chunksEverywhere;
  return 1 | 1 << TILE_SHADOW_LAYER | 1 << (chunks ? TILE_SHADOW_CHUNK_LAYER : TILE_SHADOW_WHOLE_LAYER);
}
/** A/B (?tileShadows=chunks, owner perf3): every cascade draws the per-chunk casters, as before 2026-11-03. */
export const tileShadows = { chunksEverywhere: false };
import { litFraction, sampleMoved, windowUseLevels, timecycle, type TimecycleSample } from './timecycle';
import { nightUniforms } from './nightLights';
import { WEATHER_PRESETS, type WeatherSample } from './weather/schedule';
import { weatherLighting } from './weather/lighting';

/** Below this the sky is dark enough that IBL only needs a slow refresh. */
const DEEP_NIGHT = -12;
const liveEnvironments = new Set<Environment>();
const corner = new THREE.Vector3();

/** True while every corner of a cascade's view slice (camera view-space `vertices`, CSMFrustum) lies inside the
 *  cascade camera's last rendered volume, keeping `margin` of the map width clear at the sides: at least 0.7 m on the
 *  16-50 m cascade and 5 m on the 50-200 m one, wider than the CSM fade band beyond each slice (0.1 m and 1.7 m). */
export function cascadeCovers(shadowCamera: THREE.OrthographicCamera, camera: THREE.Camera,
  vertices: { near: THREE.Vector3[]; far: THREE.Vector3[] }, margin = 0.01) {
  const mx = (shadowCamera.right - shadowCamera.left) * margin, my = (shadowCamera.top - shadowCamera.bottom) * margin;
  for (const plane of [vertices.near, vertices.far]) for (const vertex of plane) {
    corner.copy(vertex).applyMatrix4(camera.matrixWorld).applyMatrix4(shadowCamera.matrixWorldInverse);
    if (corner.x < shadowCamera.left + mx || corner.x > shadowCamera.right - mx
      || corner.y < shadowCamera.bottom + my || corner.y > shadowCamera.top - my
      || -corner.z < shadowCamera.near || -corner.z > shadowCamera.far) return false;
  }
  return true;
}

export class Environment {
  readonly sky = new AnalyticSky();
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
  private baseSample = this.sample;
  /** The timecycle sample last applied by setSun (render/timecycle.ts sampleMoved). */
  private lit: TimecycleSample | null = null;
  private weather: Readonly<WeatherSample> = WEATHER_PRESETS['partly cloudy'];
  private weatherState = '0,0,0,0.38,0.28';
  /** True while sun intensity is 0 and the cascades are not re-rendered. */
  shadowsFrozen = false;
  private pmrem: THREE.PMREMGenerator;
  private environmentTarget: THREE.RenderTarget | null = null;
  private skyScene = new THREE.Scene();
  private iblDirty = true;
  private iblElevation = Number.NaN;
  private state = '';
  atmosphere: Atmosphere;

  /** Reused sky PMREM; mobile water samples it without rendering the city a second time. */
  get environmentMap() { return this.environmentTarget?.texture ?? null; }
  /** 0 by day, 1 at full night: the same value the shaders read from nightUniforms.night. */
  get nightFactor() { return this.sample.night; }

  constructor(private scene: THREE.Scene, private renderer: THREE.WebGPURenderer, tier: QualityTier) {
    const q = QUALITY[tier];
    this.pmrem = new THREE.PMREMGenerator(renderer);
    this.sky.scale.setScalar(4500);
    this.sky.turbidity.value = 2.5;
    this.sky.rayleigh.value = 1.5;
    this.atmosphere = new Atmosphere(this.sky);
    liveEnvironments.add(this);
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
    this.sun.shadow.camera.layers.enable(TILE_SHADOW_CHUNK_LAYER);
    this.sun.shadow.bias = -0.00015;
    this.sun.shadow.normalBias = 0.025;
    const far = this.shadowFar = q.splits[q.splits.length - 1];
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

  /** Keep the sky behind distant geometry; weather is independent of streaming distance. */
  setScreenshotDistance(camera: THREE.PerspectiveCamera, distance: number) {
    this.screenshotDistance = Number.isFinite(distance) ? Math.max(0, Math.min(15000, distance)) : 0;
    this.setCityDistance(camera, this.screenshotDistance);
    this.applyFog();
  }

  /** Clip the distant city and sky together without changing vegetation LOD or weather. */
  setCityDistance(camera: THREE.PerspectiveCamera, distance: number) {
    const radius = Number.isFinite(distance) ? Math.max(0, Math.min(15000, distance)) : 0;
    camera.far = Math.max(6000, radius * 4);
    camera.updateProjectionMatrix();
    this.sky.scale.setScalar(Math.max(4500, radius * 4));
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
    // A frozen QA camera and developer time edits may not call follow() again.
    // Keep the shadow-casting light aligned with the sky at the existing focus.
    this.follow(this.sun.target.position);
    this.sky.sunPosition.value.copy(this.sunDir);
    this.baseSample = timecycle(elevation);
    // The sun direction follows every frame (shadows, sky); lighting values only when they moved visibly.
    if (!this.lit || sampleMoved(this.lit, this.baseSample)) { this.lit = this.baseSample; this.applyLighting(); }
    this.atmosphere.setSun(elevation, azimuth, this.sample);
    nightUniforms.night.value = this.sample.night;
    nightUniforms.hour.value = hour;
    nightUniforms.litFraction.value = litFraction(hour);
    nightUniforms.useLevels.value.fromArray(windowUseLevels(hour));
    this.iblDirty = true;
  }

  /** Shared weather changes uniforms without accumulating multipliers or changing the light set. */
  setWeather(sample: Readonly<WeatherSample>) {
    this.weather = sample;
    const state = `${sample.overcast},${sample.rain},${sample.fog},${sample.cloudCover},${sample.wind}`;
    if (state !== this.weatherState) {
      this.weatherState = state;
      this.applyLighting();
      this.iblDirty = true;
    }
    // Pass the final fog colour after modulation, including on a fixed QA sun/time.
    this.atmosphere.setWeather(sample, this.scene.fog?.color);
  }

  private applyLighting() {
    const s = this.sample = weatherLighting(this.baseSample, this.weather);
    // The sun stays visible and shadowed at night with zero intensity: toggling a light recompiles every material.
    this.sun.intensity = s.sunIntensity;
    this.sun.color.setRGB(...s.sunColor);
    this.hemisphere.intensity = s.hemisphereIntensity;
    this.hemisphere.color.setRGB(...s.hemisphereSky);
    this.hemisphere.groundColor.setRGB(...s.hemisphereGround);
    this.scene.environmentIntensity = s.environmentIntensity;
    this.renderer.toneMappingExposure = s.exposure;
    if (this.scene.fog instanceof THREE.Fog) this.scene.fog.color.setRGB(...s.fogColor);
    this.applyFog();
  }

  private applyFog() {
    if (!(this.scene.fog instanceof THREE.Fog)) return;
    this.scene.fog.near = this.sample.fogNear;
    this.scene.fog.far = this.sample.fogFar;
  }

  /** Degrees the visible sky moved since the last IBL. Below -12 degrees the sky no longer changes. */
  skyDrift() {
    if (Number.isNaN(this.iblElevation)) return Infinity;
    return Math.abs(Math.max(this.sunElevation, DEEP_NIGHT) - Math.max(this.iblElevation, DEEP_NIGHT));
  }

  /** Regenerate only after a time edit, not every frame. Reuse the target so time edits keep material bindings and shader programs warm. */
  updateIBL() {
    if (!this.iblDirty) return;
    this.iblDirty = false;
    this.iblElevation = this.sunElevation;
    // Visible daytime scattering is calibrated separately from the lighting owner's ambient radiance.
    const visibleBrightness = this.atmosphere.skyBrightness.value;
    const visibleDisc = this.sky.showSunDisc.value;
    this.atmosphere.skyBrightness.value = 1;
    // The directional light already supplies shadowed sunlight. The PMREM disc would add
    // it again as unshadowed light; retain the established twilight bake below 6 degrees.
    this.sky.showSunDisc.value = Number(visibleDisc) * (1 - THREE.MathUtils.smoothstep(this.sunElevation, 6, 12));
    try {
      this.environmentTarget = this.pmrem.fromScene(this.skyScene, 0, 0.1, 6000,
        { size: 128, renderTarget: this.environmentTarget });
      this.scene.environment = this.environmentTarget.texture;
    } finally {
      this.atmosphere.skyBrightness.value = visibleBrightness;
      this.sky.showSunDisc.value = visibleDisc;
    }
  }

  /** While the sun has no intensity its cascades are not re-rendered (3 passes on medium, 4 on high). Startup
   *  warmup passes `allowFreeze = false` so every shadow pipeline compiles before play, whatever the hour.
   *  In play the nearest cascade (the player's and nearby actors' shadows) renders every frame; the farther ones take
   *  alternate frames while the camera's view of them still lies inside their last map. r186 changes a cascade's
   *  shadow matrix only when it renders, so receivers always sample a map with its own matrix; distant moving casters
   *  lag one frame. A resize renders every cascade, as its bounds change. */
  updateShadows(allowFreeze: boolean) {
    this.shadowsFrozen = allowFreeze && this.sun.intensity === 0;
    const lights = this.csm.lights, camera = this.csm.camera;
    const alternate = allowFreeze && !this.shadowsFrozen && camera !== null && this.fullShadowFrames <= 0;
    this.fullShadowFrames--;
    this.shadowFrame++;
    if (alternate) camera.updateMatrixWorld();
    for (let i = 0; i < lights.length; i++) {
      const shadow = lights[i].shadow!;
      // CSM clones the sun's shadow camera into each cascade once it builds them.
      const mask = cascadeLayerMask(i, this.shortReach);
      if (shadow.camera.layers.mask !== mask) shadow.camera.layers.mask = mask;
      const skip = alternate && i > 0 && (this.shadowFrame + i) % 2 === 0
        && cascadeCovers(shadow.camera as THREE.OrthographicCamera, camera, this.csm.frustums[i].vertices);
      shadow.autoUpdate = !this.shadowsFrozen && !skip;
    }
  }
  private shadowFrame = 0;
  /** Frames that render every cascade (after construction and resize). */
  private fullShadowFrames = 2;

  /** The sun's shadow volumes for caster selection (VegetationLod.selectCasters): the direction towards the sun and
   *  each cascade camera's last volume (r186 updates it when the cascade renders; none before the first render). */
  casterVolumes(): CasterVolumes {
    const lights = this.csm.camera ? this.csm.lights : [];
    while (this.casterFrusta.length < lights.length) this.casterFrusta.push(new THREE.Frustum());
    this.casterFrusta.length = lights.length;
    for (let i = 0; i < lights.length; i++) {
      const camera = lights[i].shadow!.camera;
      this.casterMatrix.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
      this.casterFrusta[i].setFromProjectionMatrix(this.casterMatrix, camera.coordinateSystem, camera.reversedDepth);
    }
    return { toSun: this.sunDir, frusta: this.casterFrusta };
  }
  private casterFrusta: THREE.Frustum[] = [];
  private casterMatrix = new THREE.Matrix4();

  /** CSM itself snaps each cascade's light-space centre to shadow texels. */
  follow(focus: THREE.Vector3) {
    this.sun.target.position.copy(focus);
    this.sun.position.copy(focus).addScaledVector(this.sunDir, 200);
    this.sun.target.updateMatrixWorld();
    this.sun.updateMatrixWorld();
  }

  /** Replace only the sky graph during local iteration; the city and its GPU resources stay resident. */
  reloadAtmosphere(AtmosphereClass: typeof Atmosphere) {
    const seconds = this.atmosphere.seconds;
    this.atmosphere.dispose();
    this.atmosphere = new AtmosphereClass(this.sky);
    this.atmosphere.setTime(seconds);
    this.atmosphere.setSun(this.sunElevation, this.sunAzimuth, this.sample);
    this.setWeather(this.weather);
    this.iblDirty = true;
    this.updateIBL();
  }

  resize() { if (this.csm.camera) this.csm.updateFrustums(); this.fullShadowFrames = 2; }

  /** Sun shadows end at `metres` instead of the tier's last split (the setting governor's last step; null restores
   *  it). The cascades keep their near splits; the farthest one shrinks, so its casters (the tile shadow batches of
   *  every block out to 200 m: ~1M triangles a frame on the Radeon Pro 560X) are culled to the nearer band. With the
   *  short reach every cascade draws the tile shadows per 62.5 m chunk: the whole-tile casters still drew 1.0-1.5M
   *  shadow triangles at 100 m (win-after3), a 250 m tile for a cascade a few chunks wide. */
  setShadowReach(metres: number | null) {
    this.shortReach = metres !== null;
    const far = metres ?? this.shadowFar;
    if (this.csm.maxFar === far) return;
    this.csm.maxFar = far;
    this.resize();
  }
  private shadowFar = 0;
  /** setShadowReach shortened the cascades: they draw tile shadows per chunk. */
  private shortReach = false;

  dispose() {
    liveEnvironments.delete(this);
    this.atmosphere.dispose();
    this.csm.dispose();
    this.environmentTarget?.dispose();
    this.pmrem.dispose();
    this.sky.geometry.dispose();
    this.sky.material.dispose();
    this.scene.remove(this.sky, this.sun, this.sun.target, this.hemisphere, ...this.lamps.point);
    this.scene.environment = null;
  }
}

if (import.meta.hot) import.meta.hot.accept('./atmosphere', next => {
  if (next) for (const env of liveEnvironments) env.reloadAtmosphere(next.Atmosphere);
});
