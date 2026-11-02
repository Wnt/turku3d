// Camera-centred, world-locked streaks. Two instanced draws, no per-drop CPU update.
import * as THREE from 'three/webgpu';
import { attribute, cameraFar, cameraNear, cameraPosition, float, fract, length, max, mix, pass, perspectiveDepthToViewZ, positionLocal, positionView, screenUV, smoothstep, texture, uniform, uv, vec2, vec3 } from 'three/tsl';
import { isPrecompiling } from '../compileModel';
import { puddleMask, weatherUniforms } from './surfaces';
import { RainExposure } from './exposure';
import type { WeatherSample } from './schedule';

export class Rain {
  readonly scene: THREE.Scene;
  readonly sceneDepth = texture(new THREE.DepthTexture(1, 1));
  readonly mesh: THREE.Mesh;
  readonly splashes: THREE.Mesh;
  readonly exposure: RainExposure;
  private readonly origin = uniform(new THREE.Vector2());
  private readonly time = uniform(0);
  private readonly rain = uniform(0);
  private readonly wind = uniform(0);
  private readonly drift = uniform(new THREE.Vector2());
  private readonly brightness = uniform(.65);
  private readonly right = uniform(new THREE.Vector3(1, 0, 0));
  constructor(world: THREE.Scene, count = 6000, exposure = new RainExposure(), scene = new THREE.Scene(), depth = new THREE.DepthTexture(1, 1)) {
    this.exposure = exposure; this.scene = scene; this.scene.fog = world.fog;
    if (import.meta.hot && window.__dev) Object.defineProperty(window.__dev, 'rainEffects', { value: this, configurable: true });
    this.sceneDepth.value = depth;
    const base = new THREE.PlaneGeometry(1, 1), geometry = new THREE.InstancedBufferGeometry();
    geometry.index = base.index;
    geometry.attributes = { ...base.attributes };
    const seeds = new Float32Array(count * 4);
    let state = 17411;
    for (let i = 0; i < seeds.length; i++) { state = (Math.imul(state, 1664525) + 1013904223) >>> 0; seeds[i] = state / 4294967296; }
    geometry.setAttribute('dropSeed', new THREE.InstancedBufferAttribute(seeds, 4));
    geometry.instanceCount = count;
    const seed = attribute('dropSeed', 'vec4');
    const phase = fract(seed.y.sub(this.time.mul(seed.w.mul(4).floor().mul(.0625).add(.5))));
    const drift = this.drift;
    const xz = fract(seed.xz.add(drift).sub(cameraPosition.xz.div(64))).mul(64).add(cameraPosition.xz).sub(32);
    const centre = vec3(xz.x, phase.mul(32).add(cameraPosition.y).sub(8), xz.y);
    const width = seed.w.mul(.01).add(.012);
    const streak = seed.w.mul(.3).add(.18);
    const material = new THREE.MeshBasicNodeMaterial({ transparent: true, depthTest: false, depthWrite: false, side: THREE.DoubleSide });
    material.positionNode = centre.add(this.right.mul(positionLocal.x).mul(width))
      .add(vec3(this.wind.mul(-.1), 1, this.wind.mul(-.037)).mul(positionLocal.y).mul(streak));
    material.colorNode = vec3(.66, .74, .8).mul(this.brightness);
    const edge = float(1).sub(smoothstep(.05, .5, uv().x.sub(.5).abs()));
    const end = float(1).sub(uv().y.sub(.5).abs().mul(2));
    const distance = length(centre.sub(cameraPosition));
    const fade = smoothstep(1.5, 5, distance).mul(float(1).sub(smoothstep(24, 36, distance)));
    // Each drop fades through the density threshold; no disappearing instance-count steps during transitions.
    const density = smoothstep(seed.w.mul(.92), max(seed.w.mul(.92).add(.08), .08), this.rain);
    const surface = texture(this.exposure.texture, centre.xz.sub(this.origin).div(64));
    const sceneZ = perspectiveDepthToViewZ(this.sceneDepth.sample(screenUV).r, cameraNear, cameraFar).negate();
    const ownDepth = positionView.z.negate();
    const unoccluded = smoothstep(ownDepth.sub(.03), ownDepth.add(.03), sceneZ);
    const exposed = smoothstep(surface.r.add(.1), surface.r.add(.6), centre.y);
    material.opacityNode = edge.mul(end).mul(fade).mul(density).mul(mix(.18, .48, this.rain)).mul(exposed).mul(unoccluded);
    material.fog = true;
    this.mesh = new THREE.Mesh(geometry, material);
    this.mesh.name = 'weather GPU rain'; this.mesh.frustumCulled = false;
    this.mesh.renderOrder = 6;
    scene.add(this.mesh);
    const splashGeometry = geometry.clone(); splashGeometry.instanceCount = 900;
    const splash = new THREE.MeshBasicNodeMaterial({ transparent: true, depthTest: false, depthWrite: false, side: THREE.DoubleSide });
    const age = fract(seed.y.add(this.time.mul(.625)));
    const splashXZ = fract(seed.xz.sub(cameraPosition.xz.div(64))).mul(64).add(cameraPosition.xz).sub(32);
    const ground = texture(this.exposure.texture, splashXZ.sub(this.origin).div(64));
    splash.positionNode = vec3(splashXZ.x.add(positionLocal.x.mul(age.mul(.24).add(.02))), ground.r.add(.035),
      splashXZ.y.add(positionLocal.y.mul(age.mul(.24).add(.02))));
    splash.colorNode = vec3(.55, .65, .72).mul(this.brightness);
    const radius = length(uv().sub(vec2(.5))).mul(2);
    const ring = smoothstep(.7, .85, radius).mul(float(1).sub(smoothstep(.87, 1, radius)));
    const pool = puddleMask(vec3(splashXZ.x, ground.r, splashXZ.y), weatherUniforms.wetness, float(1), 1);
    splash.opacityNode = ring.mul(float(1).sub(age)).mul(ground.g).mul(density).mul(.15).mul(unoccluded).mul(pool.mul(.85).add(.15));
    splash.fog = true;
    this.splashes = new THREE.Mesh(splashGeometry, splash);
    this.splashes.name = 'weather surface splashes'; this.splashes.frustumCulled = false; this.splashes.renderOrder = 5;
    scene.add(this.splashes);
  }
  update(camera: THREE.Camera, sample: WeatherSample, seconds: number, night: number) {
    if (sample.rain > .0001 || sample.wetness > .0001) this.exposure.update(camera);
    this.origin.value.copy(this.exposure.origin);
    // Quantized fall rates complete whole cycles in16s; wrap in double precision before GPU upload.
    // Drift phases are wrapped independently, so shared-clock joins and weather edits never reset a volume.
    const wrap = (value: number) => value - Math.floor(value);
    this.time.value = ((seconds % 16) + 16) % 16;
    this.drift.value.set(wrap(seconds * .009765625), wrap(seconds * .0036163330078125));
    this.rain.value = sample.rain;
    this.wind.value = sample.wind;
    this.brightness.value = .2 + (1 - night) * .5;
    this.right.value.setFromMatrixColumn(camera.matrixWorld, 0);
    this.scene.userData.rain = sample.rain;
  }
  /** Resolve rain after temporal AA so moving subpixel streaks survive the history filter. */
  overlay(renderer: THREE.WebGPURenderer, camera: THREE.Camera, sceneDepth: THREE.Texture) {
    this.sceneDepth.value = sceneDepth;
    const layer = pass(this.scene, camera, { depthBuffer: false });
    const render = layer.updateBefore.bind(layer);
    let dry = false;
    layer.updateBefore = frame => {
      if (isPrecompiling(renderer)) return;
      if (dry && this.scene.userData.rain <= .0001) return;
      render(frame); dry = this.scene.userData.rain <= .0001;
    };
    return { color: layer.getTextureNode().rgb, resource: layer };
  }
  dispose(keepExposure = false) {
    for (const mesh of [this.mesh, this.splashes]) { mesh.removeFromParent(); mesh.geometry.dispose(); (mesh.material as THREE.Material).dispose(); }
    if (!keepExposure) this.exposure.dispose();
  }
}
