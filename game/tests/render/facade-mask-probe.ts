// Run in the existing dev page via CDP; this probe never launches a browser.
import * as THREE from 'three/webgpu';
import { float, step, uv, vec2, vec3 } from 'three/tsl';
import { facadeAttribute, interleaveProgram } from '../../src/render/facadeProgramAttributes';
import { programLayout } from '../../src/render/facadeProgram';

export async function probeFacadeMasks(renderer: THREE.WebGPURenderer) {
  const previous = renderer.getRenderTarget();
  const target = new THREE.RenderTarget(256, 256);
  const resources: (THREE.BufferGeometry | THREE.Material)[] = [];
  const camera = new THREE.OrthographicCamera(0, 3, 4, 0, .1, 10);
  camera.position.z = 5;
  const results: Record<string, number> = {};
  try {
    for (const [name, module, detail, height, patch, samples] of [
      ['window', 0, 0, 4, 0, [[.35, .35, 1], [.1, .35, 0], [.5, .35, 0]]],
      ['opaque-door', 1, 0, 4, 0, [[.35, .35, 0], [.5, .35, 0]]],
      ['portal-leaf', 1, 0, 4, 2, [[.35, .35, 0], [.5, .35, 0]]],
      ['shop', 2, 0, 4, 0, [[.2, .4, 1], [.8, .4, 1], [.04, .4, 0]]],
      ['glass-backing', 47, 0, 4, 0, [[.35, .35, 0]]],
      ['plain-ground', 0, 1, 4, 0, [[.35, .35, 0]]],
      ['above-fragment', 0, 0, 1, 0, [[.35, .35, 0]]],
      ['periodic-seams', 0, 0, 8.05, 0,
        [[.494, .175, 0], [.498, .175, 0], [.175, .494, 0], [.175, .498, 0]]],
    ] as const) {
      // Place internal bay/floor seams inside a two-pixel derivative quad.
      const viewWidth = name === 'periodic-seams' ? 6.04 : 3;
      const viewHeight = name === 'periodic-seams' ? 8.05 : 4;
      camera.right = viewWidth; camera.top = viewHeight; camera.updateProjectionMatrix();
      const geometry = new THREE.PlaneGeometry(viewWidth, viewHeight);
      geometry.translate(viewWidth / 2, viewHeight / 2, 0);
      const tex = geometry.getAttribute('uv');
      for (let i = 0; i < tex.count; i++) tex.setXY(i, tex.getX(i) * viewWidth, tex.getY(i) * viewHeight);
      const fields = { _kit_layout: [module, 3, 4], _kit_extent: [0, 0, viewWidth, height],
        _kit_opening: [.5, .5, .2, 0], _kit_detail: [0, 0, detail, patch], _kit_floors: [4, 4, 0] };
      for (const [field, values] of Object.entries(fields)) geometry.setAttribute(field,
        new THREE.Float32BufferAttribute(Array.from({ length: tex.count }, () => values).flat(), values.length));
      interleaveProgram(geometry);
      const layout = facadeAttribute('_kit_layout', 'vec3');
      const program = programLayout(vec2(uv().x.div(3), uv().y.div(4)), uv(), layout,
        step(4, uv().y), float(module));
      const material = new THREE.MeshBasicNodeMaterial();
      resources.push(geometry, material);
      material.colorNode = vec3(program.glass);
      material.toneMapped = false;
      const scene = new THREE.Scene();
      scene.add(new THREE.Mesh(geometry, material));
      renderer.setRenderTarget(target);
      await renderer.compileAsync(scene, camera);
      renderer.render(scene, camera);
      const pixels = await renderer.readRenderTargetPixelsAsync(target, 0, 0, 256, 256);
      for (const [x, y, expected] of samples) {
        const value = Number(pixels[(Math.floor(y * 256) * 256 + Math.floor(x * 256)) * 4]);
        const key = `${name}:${x},${y}`;
        results[key] = value;
        if (expected ? value < 200 : value > 20) throw new Error(`${key}: mask ${value}, expected ${expected}`);
      }
    }
    return results;
  } finally {
    renderer.setRenderTarget(previous);
    for (const resource of resources) resource.dispose();
    target.dispose();
  }
}
