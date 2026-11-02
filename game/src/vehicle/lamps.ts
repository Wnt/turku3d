import * as T from 'three/webgpu';
import type { VehicleKind } from '../physics/constants';
import type { LightState } from './signals';

const rigs = new WeakMap<T.Object3D, { front: T.MeshStandardMaterial[]; tail: T.MeshStandardMaterial[] }>();
/** GLB convention: lamp-front-left/right and lamp-tail-left/right, on a node or material.
 * Single front/tail nodes are accepted for scooters; KORPI's led-optics/tail-lens are also recognised. */
export function vehicleLamps(root: T.Object3D, kind: VehicleKind, state: LightState) {
  // The fleet's vertex surface tags already identify its lamps, with one shader shared by all cars.
  if ((root as T.Mesh).geometry?.hasAttribute('fleetSurface')) {
    root.userData.vehicleLampState ??= new T.Vector4();
    root.userData.vehicleLampState.set(state === 2 ? 3 : state === 1 ? 1 : 0, 0, 0, 0);
    return;
  }
  let rig = rigs.get(root);
  if (!rig) {
    rig = { front: [], tail: [] };
    root.traverse(object => {
      const mesh = object as T.Mesh;
      if (!mesh.isMesh) return;
      const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      const replacements = materials.map(material => {
        const name = `${object.name} ${material.name}`.toLowerCase();
        const side = /lamp[-_ ]front|headlight|led-optics/.test(name) ? 'front'
          : /lamp[-_ ]tail|taillight|tail-lens/.test(name) ? 'tail' : null;
        if (!side || !(material as T.MeshStandardMaterial).emissive) return material;
        const copy = (material as T.MeshStandardMaterial).clone();
        copy.emissive.set(side === 'front' ? 0xffeed1 : 0xff1204);
        rig![side].push(copy); return copy;
      });
      mesh.material = Array.isArray(mesh.material) ? replacements : replacements[0];
    });
    // Texture-atlas models have no separable lamp material. In the physics frame +X is forward, +Z right.
    for (const side of ['front', 'tail'] as const) if (!rig[side].length) {
      const scooter = kind === 'scooter', truck = kind === 'truck';
      for (const sign of scooter ? [0] : [-1, 1]) {
        const material = new T.MeshStandardMaterial({ color: side === 'front' ? 0xc8c4ae : 0x650903,
          emissive: side === 'front' ? 0xffeed1 : 0xff1204, roughness: .2, metalness: .1 });
        const lamp = new T.Mesh(new T.BoxGeometry(.035, scooter ? .12 : .13, scooter ? .14 : .30), material);
        lamp.name = `lamp-${side}-${sign < 0 ? 'left' : 'right'}`;
        lamp.position.set((side === 'front' ? 1 : -1) * (scooter ? .79 : truck ? 2.39 : 2.11),
          scooter ? .48 : truck ? .15 : -.05, sign * (truck ? .67 : .68));
        lamp.castShadow = false; root.add(lamp); rig[side].push(material);
      }
    }
    rigs.set(root, rig);
  }
  rig.front.forEach(material => material.emissiveIntensity = state === 2 ? 12 : state === 1 ? 3 : 0);
  rig.tail.forEach(material => material.emissiveIntensity = state ? .65 : 0);
}
