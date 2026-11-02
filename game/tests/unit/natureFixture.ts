import * as THREE from 'three/webgpu';
import { natureLibraryFromScene, type NatureLibrary } from '../../src/world/nature/library';

const TREES = ['tree_birch', 'tree_linden', 'tree_maple', 'tree_oak', 'tree_rowan', 'tree_willow', 'tree_pine', 'tree_spruce'];
const SHRUBS = ['shrub_lilac', 'shrub_rose', 'shrub_juniper'];

/** A small stand-in for nature.glb with the same node/extras/material contract (box geometry, plain textures). */
export function natureFixture(): NatureLibrary {
  const root = new THREE.Group();
  const material = (name: string, textured = true) => {
    const m = new THREE.MeshStandardMaterial(textured ? { map: new THREE.Texture(), normalMap: new THREE.Texture() } : {});
    m.name = name; return m;
  };
  const materials = Object.fromEntries(['nature_bark', 'nature_leaves', 'nature_impostor', 'nature_wood', 'nature_granite'].map(n => [n, material(n)]));
  materials.nature_metal = material('nature_metal', false);
  const node = (asset: string, lod: number, kind: string, parts: string[], height: number) => {
    const group = new THREE.Group();
    group.userData = { asset, lod, kind };
    for (const part of parts) {
      const geometry = new THREE.BoxGeometry(height * 0.5, height, height * 0.5, lod ? 1 : 2, lod ? 1 : 2, lod ? 1 : 2).translate(0, height / 2, 0);
      group.add(new THREE.Mesh(geometry, materials[part]));
    }
    root.add(group);
  };
  for (const name of TREES) {
    node(name, 0, 'tree', ['nature_bark', 'nature_leaves'], 15); node(name, 1, 'tree', ['nature_bark', 'nature_leaves'], 15); node(name, 2, 'tree', ['nature_impostor'], 15);
  }
  for (const name of SHRUBS) { node(name, 0, 'shrub', ['nature_bark', 'nature_leaves'], 2); node(name, 1, 'shrub', ['nature_leaves'], 2); }
  node('hedge', 0, 'hedge', ['nature_leaves'], 1.2); node('hedge', 1, 'hedge', ['nature_leaves'], 1.2);
  node('grass_clump', 0, 'grass', ['nature_leaves'], .42);
  node('picket_fence', 0, 'fence', ['nature_wood'], 1); node('metal_railing', 0, 'fence', ['nature_metal'], 1); node('granite_wall', 0, 'fence', ['nature_granite'], 1);
  return natureLibraryFromScene(root, null, true);
}
