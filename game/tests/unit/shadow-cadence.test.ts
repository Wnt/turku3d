import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { CSMFrustum } from 'three/addons/csm/CSMFrustum.js';
import { Environment, cascadeCovers } from '../../src/render/environment';

/** A street camera and three cascades over a sun straight overhead: each cascade map spans 600 m around the camera. */
function rig() {
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  const env = new Environment(new THREE.Scene(), renderer, 'medium');
  const camera = new THREE.PerspectiveCamera(60, 2, 0.1, 6000);
  camera.position.set(0, 2, 0); camera.updateMatrixWorld();
  const main = new CSMFrustum({ webGL: false, projectionMatrix: camera.projectionMatrix, maxFar: 200 });
  const frustums: CSMFrustum[] = [];
  main.split([16 / 200, 50 / 200, 1], frustums);
  const lights = frustums.map(() => {
    const light = new THREE.DirectionalLight();
    const shadow = light.shadow.camera;
    Object.assign(shadow, { left: -300, right: 300, top: 300, bottom: -300, near: 0.5, far: 600 });
    shadow.position.set(0, 300, 0); shadow.lookAt(0, 0, 0); shadow.updateProjectionMatrix(); shadow.updateMatrixWorld();
    return light;
  });
  Object.assign(env.csm, { camera, frustums, lights, updateFrustums: () => {} });
  env.sun.intensity = 3;
  const renders = () => lights.map(light => light.shadow.autoUpdate);
  return { env, camera, frustums, lights, renders };
}

test('a cascade covers its view slice until the camera leaves its last map', () => {
  const { env, camera, frustums, lights } = rig();
  const far = lights[2].shadow.camera as THREE.OrthographicCamera;
  expect(cascadeCovers(far, camera, frustums[2].vertices)).toBe(true);
  camera.position.x = 80; camera.updateMatrixWorld(); // the 462 m wide far edge now reaches x = 311 m
  expect(cascadeCovers(far, camera, frustums[2].vertices)).toBe(false);
  camera.position.set(0, 2, 0); camera.rotation.y = Math.PI / 4; camera.updateMatrixWorld();
  expect(cascadeCovers(far, camera, frustums[2].vertices)).toBe(false); // a turn swings the far corners out
  expect(cascadeCovers(lights[1].shadow.camera as THREE.OrthographicCamera, camera, frustums[1].vertices)).toBe(true);
  env.dispose();
});

test('in play the far cascades alternate frames while covered; the nearest renders every frame', () => {
  const { env, camera, renders } = rig();
  env.updateShadows(true); env.updateShadows(true); // construction renders every cascade for two frames
  const counts = [0, 0, 0];
  for (let frame = 0; frame < 8; frame++) {
    env.updateShadows(true);
    renders().forEach((update, i) => { if (update) counts[i]++; });
    expect(renders().filter(Boolean)).toHaveLength(2);
  }
  expect(counts).toEqual([8, 4, 4]); // 24 cascade renders -> 16
  camera.position.x = 400; camera.updateMatrixWorld(); // outside every last map: render all of them
  env.updateShadows(true); expect(renders()).toEqual([true, true, true]);
  env.updateShadows(true); expect(renders()).toEqual([true, true, true]);
  env.dispose();
});

test('warmup, QA and night keep their cascade behaviour; a resize renders every cascade', () => {
  const { env, renders } = rig();
  for (let frame = 0; frame < 4; frame++) { env.updateShadows(false); expect(renders()).toEqual([true, true, true]); }
  env.updateShadows(true); env.updateShadows(true);
  env.updateShadows(true); expect(renders().filter(Boolean)).toHaveLength(2);
  env.resize();
  env.updateShadows(true); expect(renders()).toEqual([true, true, true]);
  env.updateShadows(true); expect(renders()).toEqual([true, true, true]);
  env.updateShadows(true); expect(renders().filter(Boolean)).toHaveLength(2);
  env.sun.intensity = 0;
  env.updateShadows(true); expect(env.shadowsFrozen).toBe(true); expect(renders()).toEqual([false, false, false]);
  env.dispose();
});
