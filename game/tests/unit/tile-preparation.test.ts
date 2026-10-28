import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import type { Tile } from '3d-tiles-renderer';
import { prepareTiles } from '../../src/world/tilePreparation';

async function fixture(abort: boolean) {
  const tile = { engineData: {} } as Tile & { engineData: { scene?: THREE.Object3D; materials?: THREE.Material[] } };
  const signal = new AbortController();
  const original = new THREE.MeshStandardMaterial({ map: new THREE.Texture() }), styled = new THREE.MeshBasicMaterial();
  const model: THREE.Mesh<THREE.BoxGeometry, THREE.Material> = new THREE.Mesh(new THREE.BoxGeometry(), original), dispose = vi.spyOn(original, 'dispose');
  let process!: (model: THREE.Object3D, tile: Tile) => Promise<void>;
  const parser = {
    group: new THREE.Group(),
    registerPlugin(plugin: object) { process = (plugin as { processTileModel: typeof process }).processTileModel; },
    async parseTile(_buffer: ArrayBuffer, tile: Tile, _extension: string, _url: string, _signal: AbortSignal) {
      await process(model, tile);
      if (!signal.signal.aborted) Object.assign((tile as typeof tile & { engineData: object }).engineData, { scene: model, materials: [model.material] });
    },
  };
  const release = vi.fn();
  prepareTiles(parser, async current => {
    expect(tile.engineData.scene).toBeUndefined(); expect(parser.group.children).toEqual([]);
    model.material = styled;
    await Promise.resolve();
    expect(current.parent).toBe(parser.group);
    if (abort) signal.abort();
  }, release);
  await parser.parseTile(new ArrayBuffer(0), tile, 'glb', 'tile.glb', signal.signal);
  expect(model.parent).toBeNull();
  return { tile, model, original, release, dispose };
}

test('prepares before native publication and retains original glTF resources after styling', async () => {
  const result = await fixture(false);
  expect(result.tile.engineData.scene).toBe(result.model);
  expect(result.tile.engineData.materials).toEqual([result.original]);
  expect(result.release).not.toHaveBeenCalled(); expect(result.dispose).not.toHaveBeenCalled();
});

test('aborted preparation releases styled additions and original glTF resources without publication', async () => {
  const result = await fixture(true);
  expect(result.tile.engineData.scene).toBeUndefined();
  expect(result.release).toHaveBeenCalledOnce(); expect(result.dispose).toHaveBeenCalledOnce();
});
