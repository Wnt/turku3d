// Sky + sun + ambient. Sun direction from ?time= (core/sun.ts). The shadow camera follows a focus point.
import * as THREE from 'three/webgpu';
import { SkyMesh } from 'three/addons/objects/SkyMesh.js';
import { sunDirection } from '../core/sun';

export class Environment {
  readonly sky = new SkyMesh();
  readonly sun = new THREE.DirectionalLight(0xfff2e0, 3.0);
  readonly hemi = new THREE.HemisphereLight(0xcfe3ff, 0x5a5348, 1.0);
  readonly sunDir = new THREE.Vector3(0, 1, 0);
  sunElevation = 90;
  sunAzimuth = 0;

  constructor(scene: THREE.Scene, deterministic: boolean) {
    this.sky.scale.setScalar(4500);
    this.sky.turbidity.value = 3;
    this.sky.rayleigh.value = 1.2;
    if (deterministic) this.sky.cloudSpeed.value = 0;
    scene.add(this.sky);
    scene.add(this.hemi);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(2048, 2048);
    const s = this.sun.shadow.camera as THREE.OrthographicCamera;
    Object.assign(s, { left: -60, right: 60, top: 60, bottom: -60, near: 1, far: 400 });
    this.sun.shadow.bias = -0.0005;
    this.sun.shadow.normalBias = 0.03;
    scene.add(this.sun, this.sun.target);
    scene.fog = new THREE.Fog(0xb8cde0, 300, 2500);
  }

  setTime(date: Date) {
    const d = sunDirection(date);
    this.sunElevation = d.elevation; this.sunAzimuth = d.azimuth;
    this.sunDir.set(d.x, d.y, d.z).normalize();
    this.sky.sunPosition.value.copy(this.sunDir);
    const day = THREE.MathUtils.clamp((d.elevation + 4) / 14, 0, 1); // 0 at -4 deg .. 1 at +10 deg
    this.sun.intensity = 3.2 * day;
    this.sun.visible = d.elevation > -2;
    this.hemi.intensity = 0.15 + 0.85 * day;
    const warm = THREE.MathUtils.clamp(d.elevation / 25, 0, 1);
    this.sun.color.setRGB(1, 0.75 + 0.22 * warm, 0.55 + 0.4 * warm);
  }

  /** Keep the shadow frustum centred on the focus point. */
  follow(focus: THREE.Vector3) {
    const dir = this.sunDir.y > 0.05 ? this.sunDir : new THREE.Vector3(0, 1, 0);
    this.sun.target.position.copy(focus);
    this.sun.position.copy(focus).addScaledVector(dir, 200);
    this.sun.target.updateMatrixWorld();
  }
}
