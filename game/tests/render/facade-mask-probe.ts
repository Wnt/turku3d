// Run in the existing dev page via CDP; this probe never launches a browser.
import * as THREE from 'three/webgpu';
import { CSMShadowNode } from 'three/addons/csm/CSMShadowNode.js';
import { float, step, uv, vec2, vec3 } from 'three/tsl';
import { facadeAttribute, interleaveProgram } from '../../src/render/facadeProgramAttributes';
import { programLayout } from '../../src/render/facadeProgram';
import { facadeKitMaterial } from '../../src/render/facadeKit';

export async function probeFacadeMasks(renderer: THREE.WebGPURenderer, environment?: THREE.Texture | null) {
  const previous = renderer.getRenderTarget();
  const target = new THREE.RenderTarget(256, 256);
  const resources: { dispose(): void }[] = [];
  const camera = new THREE.OrthographicCamera(0, 3, 4, 0, .1, 10);
  camera.position.z = 5;
  const results: Record<string, number> = {};
  try {
    for (const [name, module, detail, height, patch, samples] of [
      ['window', 0, 0, 4, 0, [[.35, .35, 1], [.1, .35, 0], [.5, .35, 0]]],
      ['reviewed-pair', 0, 0, 4, 0, [[.35, .54, 1], [.5, .35, 0]]],
      ['reviewed-asymmetric', 0, 0, 4, 0, [[.5, .35, 1], [.6, .35, 0]]],
      ['opaque-door', 1, 0, 4, 0, [[.35, .35, 0], [.5, .35, 0]]],
      ['portal-leaf', 1, 0, 4, 2, [[.35, .35, 0], [.5, .35, 0]]],
      ['shop', 2, 0, 4, 0, [[.2, .4, 1], [.8, .4, 1], [.04, .4, 0]]],
      ['glass-backing', 47, 0, 4, 0, [[.35, .35, 0]]],
      ['plain-ground', 0, 1, 4, 0, [[.35, .35, 0]]],
      ['above-fragment', 0, 0, 1, 0, [[.35, .35, 0]]],
      ['periodic-seams', 0, 0, 8.05, 0,
        [[.494, .175, 0], [.498, .175, 0], [.175, .494, 0], [.175, .498, 0]]],
    ] as const) {
      // Offset internal bay/floor seams inside a two-pixel derivative quad.
      const viewWidth = name === 'periodic-seams' ? 6.04 : 3;
      const viewHeight = name === 'periodic-seams' ? 8.05 : 4;
      camera.right = viewWidth; camera.top = viewHeight; camera.updateProjectionMatrix();
      const geometry = new THREE.PlaneGeometry(viewWidth, viewHeight);
      geometry.translate(viewWidth / 2, viewHeight / 2, 0);
      const tex = geometry.getAttribute('uv');
      for (let i = 0; i < tex.count; i++) tex.setXY(i, tex.getX(i) * viewWidth, tex.getY(i) * viewHeight);
      const pattern = name === 'reviewed-pair' ? [2, 1, 0, 0]
        : name === 'reviewed-asymmetric' ? [1, 1, .7, 0] : [0, 0, 0, -1];
      const fields = { _kit_layout: [module, 3, 4], _kit_extent: [0, 0, viewWidth, height], _kit_glazing: pattern,
        _kit_opening: [.5, .5, .2, 0], _kit_detail: [0, 0, detail, patch], _kit_floors: [4, 4, 0] };
      for (const [field, values] of Object.entries(fields)) geometry.setAttribute(field,
        new THREE.Float32BufferAttribute(Array.from({ length: tex.count }, () => values).flat(), values.length));
      interleaveProgram(geometry);
      const layout = facadeAttribute('_kit_layout', 'vec3');
      const program = programLayout(vec2(uv().x.div(3), uv().y.div(4)), uv(), layout,
        step(4, uv().y), float(module), facadeAttribute('_kit_glazing', 'vec4'));
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
    // Exercise the production material/attribute layout, including frame colour
    // and a clipped, reversed 22.47 m ribbon with its original pane phase.
    const geometry = new THREE.PlaneGeometry(8, 2);
    geometry.translate(8, 1, 0);
    const tex = geometry.getAttribute('uv');
    for (let i = 0; i < tex.count; i++) tex.setXY(i, 4 + tex.getX(i) * 8, tex.getY(i) * 2);
    const fields = { _kit_layout: [0, 22.47, 2], _kit_extent: [0, 0, 22.47, 2],
      _kit_opening: [.98, .95, .02, 0], _kit_detail: [0, 0, 0, 1], _kit_floors: [.001, 2, 0],
      _kit_bands: [0, 0, 0, 0], _kit_wall: [1, 1, 1], _kit_base: [1, 1, 1], _kit_trim: [1, 1, 1],
      _kit_glazing: [-4.494, 1, .3, 0], _kit_frame: [.6, .6, .6, -5] };
    for (const [field, values] of Object.entries(fields)) geometry.setAttribute(field,
      new THREE.Float32BufferAttribute(Array.from({ length: tex.count }, () => values).flat(), values.length));
    interleaveProgram(geometry);
    const map = (bytes: number[]) => {
      const texture = new THREE.DataTexture(new Uint8Array(bytes), 1, 1);
      // Production KTX2 atlases are filtered. DataTexture's nearest defaults
      // omit a sampler in Three.js even though this material uses textureSampleGrad.
      texture.minFilter = THREE.LinearFilter;
      texture.magFilter = THREE.LinearFilter;
      texture.needsUpdate = true; resources.push(texture); return texture;
    };
    const source = new THREE.MeshStandardMaterial({ map: map([255, 255, 255, 0]),
      normalMap: map([128, 128, 255, 255]), roughnessMap: map([255, 255, 0, 255]) });
    source.userData.facade_kit = { version: 5 };
    const material = facadeKitMaterial(source, renderer)!;
    material.emissiveNode = vec3(0); // deterministic daylight, independent of current game clock
    material.toneMapped = false;
    resources.push(geometry, source, material);
    const scene = new THREE.Scene();
    scene.environment = environment ?? null;
    scene.environmentIntensity = 0; // retain production IBL shader inputs, deterministic pixel colours
    const mesh = new THREE.Mesh(geometry, material);
    mesh.castShadow = mesh.receiveShadow = true;
    const sun = new THREE.DirectionalLight(0xffffff, .1);
    sun.position.set(8, 4, 5); sun.target.position.set(8, 1, 0);
    sun.castShadow = true; sun.shadow.mapSize.set(32, 32);
    const csm = new CSMShadowNode(sun, { cascades: 3, maxFar: 10, lightMargin: 2 });
    sun.shadow.shadowNode = csm;
    resources.push(csm);
    scene.add(mesh, new THREE.AmbientLight(0xffffff, 2), sun, sun.target);
    camera.left = 4; camera.right = 12; camera.top = 2; camera.bottom = 0; camera.updateProjectionMatrix();
    renderer.setRenderTarget(target);
    await renderer.compileAsync(scene, camera);
    renderer.render(scene, camera);
    const pixels = await renderer.readRenderTargetPixelsAsync(target, 0, 0, 256, 256);
    for (const [x, frame] of [[4.05, false], [4.494, true], [5.1, false], [5.8422, true]] as const) {
      const value = Number(pixels[(128 * 256 + Math.floor((x - 4) / 8 * 256)) * 4]);
      results[`production-ribbon:${x}`] = value;
      if (frame ? value < 100 : value > 90) throw new Error(`production-ribbon:${x}: unexpected ${value}`);
    }
    return results;
  } finally {
    renderer.setRenderTarget(previous);
    for (const resource of resources) resource.dispose();
    target.dispose();
  }
}
