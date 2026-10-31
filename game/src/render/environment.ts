// Analytic Rayleigh/Mie sky, PMREM sky IBL and texel-snapped street-scale CSM.
import * as THREE from 'three/webgpu';
import { SkyMesh } from 'three/addons/objects/SkyMesh.js';
import { CSMShadowNode } from 'three/addons/csm/CSMShadowNode.js';
import { sunDirection } from '../core/sun';
import { QUALITY, type QualityTier } from './quality';
import { REFLECTION_LAYER } from './reflections';
import { TILE_SHADOW_LAYER } from '../world/shadowBatch';

export class Environment {
  readonly sky = new SkyMesh();
  readonly sun = new THREE.DirectionalLight(0xfff2e0, 3);
  readonly sunDir = new THREE.Vector3(0, 1, 0);
  readonly csm: CSMShadowNode;
  screenshotDistance = 0;
  sunElevation = 90;
  sunAzimuth = 0;
  private pmrem: THREE.PMREMGenerator;
  private environmentTarget: THREE.RenderTarget | null = null;
  private skyScene = new THREE.Scene();
  private iblDirty = true;
  private timeStamp: number | null = null;

  /** Reused sky PMREM; mobile water samples it without rendering the city a second time. */
  get environmentMap() { return this.environmentTarget?.texture ?? null; }

  constructor(private scene: THREE.Scene, renderer: THREE.WebGPURenderer, tier: QualityTier) {
    const q = QUALITY[tier];
    this.pmrem = new THREE.PMREMGenerator(renderer);
    this.sky.scale.setScalar(4500);
    this.sky.turbidity.value = 2.5;
    this.sky.rayleigh.value = 1.5;
    this.sky.cloudCoverage.value = 0;
    this.sky.cloudSpeed.value = 0;
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
    scene.fog = new THREE.Fog(0xb8cde0, 500, 3500);
    scene.environmentIntensity = 0.35;
  }

  /** Keep the sky behind distant geometry and move haze beyond the requested photo scenery. */
  setScreenshotDistance(camera: THREE.PerspectiveCamera, distance: number) {
    this.screenshotDistance = Number.isFinite(distance) ? Math.max(0, Math.min(8000, distance)) : 0;
    const radius = this.screenshotDistance;
    camera.far = Math.max(6000, radius * 4);
    camera.updateProjectionMatrix();
    this.sky.scale.setScalar(Math.max(4500, radius * 4));
    if (this.scene.fog instanceof THREE.Fog) {
      this.scene.fog.near = radius || 500;
      this.scene.fog.far = radius ? radius * 1.5 : 3500;
    }
    this.resize();
  }

  setTime(date: Date) {
    if (date.getTime() === this.timeStamp) return;
    this.timeStamp = date.getTime();
    const d = sunDirection(date);
    this.sunElevation = d.elevation; this.sunAzimuth = d.azimuth;
    this.sunDir.set(d.x, d.y, d.z).normalize();
    this.sky.sunPosition.value.copy(this.sunDir);
    const day = THREE.MathUtils.clamp((d.elevation + 4) / 14, 0, 1);
    this.sun.intensity = 3.2 * day;
    this.sun.visible = d.elevation > -2;
    this.scene.environmentIntensity = 0.04 + 0.31 * day;
    const warm = THREE.MathUtils.clamp(d.elevation / 25, 0, 1);
    this.sun.color.setRGB(1, 0.75 + 0.22 * warm, 0.55 + 0.4 * warm);
    this.iblDirty = true;
  }

  /** Regenerate only after a time edit, not every frame. Reuse the target so time edits keep material bindings and shader programs warm. */
  updateIBL() {
    if (!this.iblDirty) return;
    this.iblDirty = false;
    this.environmentTarget = this.pmrem.fromScene(this.skyScene, 0, 0.1, 6000,
      { size: 128, renderTarget: this.environmentTarget });
    this.scene.environment = this.environmentTarget.texture;
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
    this.scene.remove(this.sky, this.sun, this.sun.target);
    this.scene.environment = null;
  }
}
