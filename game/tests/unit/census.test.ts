import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { BuildLog, drawCensus } from '../../src/render/census';

test('census counts one whole frame of draws by pass and family, then restores the draw path', async () => {
  let target: THREE.RenderTarget | null = null;
  const direct = (..._args: unknown[]) => {};
  const renderer = { getRenderTarget: () => target, _renderObjectDirect: direct } as unknown as THREE.WebGPURenderer;
  const host = renderer as unknown as { _renderObjectDirect: (...args: unknown[]) => void };
  const tile = new THREE.Group(); tile.name = 'tile:t0_1_-1'; const mesh = new THREE.Mesh(new THREE.BoxGeometry()); mesh.name = 'static-tile-batch'; tile.add(mesh);
  const persp = new THREE.PerspectiveCamera(), ortho = new THREE.OrthographicCamera();
  const shadow = new THREE.RenderTarget(); shadow.texture.name = 'ShadowMap';
  let end: (() => void) | undefined;
  const result = drawCensus(renderer, { install: each => { end = each; }, uninstall: () => { end = undefined; } }, new BuildLog());
  host._renderObjectDirect(mesh, mesh.material, null, persp); end!(); // partial frame: discarded
  for (let i = 0; i < 3; i++) host._renderObjectDirect(mesh, mesh.material, null, persp);
  target = shadow; host._renderObjectDirect(mesh, mesh.material, null, ortho);
  end!();
  const census = await result;
  expect(census.draws).toBe(4);
  expect(census.rows).toEqual([['screen|persp\ttile:T > static-tile-batch', 3, 36], ['ShadowMap|ortho\ttile:T > static-tile-batch', 1, 12]]);
  expect(host._renderObjectDirect).toBe(direct); expect(end).toBeUndefined();
  shadow.dispose();
});

test('build log separates in-frame from preparation builds and stays bounded', () => {
  const log = new BuildLog(), renderer = { getRenderTarget: () => null } as unknown as THREE.WebGPURenderer;
  const material = new THREE.MeshStandardMaterial({ name: 'distant-city-buildings:dissolve' });
  for (let i = 0; i < 500; i++) log.record(renderer, { material, object: new THREE.Mesh(), camera: new THREE.PerspectiveCamera() }, i % 5 === 0);
  expect(log.inFrameTotal).toBe(100); expect(log.preparedTotal).toBe(400); expect(log.entries).toHaveLength(400);
  expect(log.entries.at(-1)).toMatchObject({ pass: 'screen|persp', material: 'distant-city-buildings:dissolve', object: 'Mesh' });
  expect(log.top().map(f => [f.key, f.count])).toEqual([['prep\tscreen|persp\tdistant-city-buildings:dissolve', 400], ['frame\tscreen|persp\tdistant-city-buildings:dissolve', 100]]);
});
