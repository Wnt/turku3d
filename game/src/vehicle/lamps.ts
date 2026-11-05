import * as T from 'three/webgpu';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import type { VehicleKind } from '../physics/constants';
import type { LightState } from './signals';

const rigs = new WeakMap<T.Object3D, { front: T.MeshStandardMaterial[]; tail: T.MeshStandardMaterial[] }>();
/** GLB convention: lamp-front-left/right and lamp-tail-left/right, on a node or material.
 * Single front/tail nodes are accepted for scooters; the trophy truck's led-optics/tail-lens materials are also recognised. */
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
        const side = /lamp[-_ ]front|headlight|headlamp|led-optics/.test(name) ? 'front'
          : /lamp[-_ ]tail|taillight|taillamp|tail-lens/.test(name) ? 'tail' : null;
        if (!side || !(material as T.MeshStandardMaterial).emissive) return material;
        const copy = (material as T.MeshStandardMaterial).clone();
        copy.emissive.set(side === 'front' ? 0xffeed1 : 0xff1204);
        rig![side].push(copy); return copy;
      });
      mesh.material = Array.isArray(mesh.material) ? replacements : replacements[0];
    });
    // Texture-atlas models have no separable lamp material. In the physics frame +X is forward, +Z right.
    const trim: T.BufferGeometry[] = [];
    for (const side of ['front', 'tail'] as const) if (!rig[side].length) {
      const scooter = kind === 'scooter';
      for (const sign of scooter ? [0] : [-1, 1]) {
        const material = new T.MeshStandardMaterial({ color: side === 'front' ? 0xc8c4ae : 0x650903,
          emissive: side === 'front' ? 0xffeed1 : 0xff1204, roughness: .2, metalness: .1 });
        const lamp = new T.Mesh(new T.BoxGeometry(.035, scooter ? .12 : .13, scooter ? .14 : .30), material);
        lamp.name = `lamp-${side}-${sign < 0 ? 'left' : 'right'}`;
        lamp.position.set((side === 'front' ? 1 : -1) * (scooter ? .79 : 2.11), scooter ? .48 : -.05, sign * .68);
        lamp.castShadow = false; root.add(lamp); rig[side].push(material);
        const at = lamp.position, face = side === 'front' ? 1 : -1;
        trim.push(new T.BoxGeometry(.045, scooter ? .15 : .17, scooter ? .18 : .35)
          .translate(at.x - face * .023, at.y, at.z));
        // Raised optical dividers remain dark, breaking the emitter into a ribbed lens rather than a white square.
        trim.push(new T.BoxGeometry(.004, .008, scooter ? .14 : .30).translate(at.x + face * .021, at.y, at.z));
        trim.push(new T.BoxGeometry(.004, scooter ? .12 : .13, .008).translate(at.x + face * .021, at.y, at.z));
      }
    }
    if (trim.length) {
      const geometry = mergeGeometries(trim, false)!; trim.forEach(part => part.dispose());
      const bezel = new T.Mesh(geometry, new T.MeshStandardMaterial({ color: 0x181c20, roughness: .35, metalness: .3 }));
      bezel.name = 'vehicle-lens-bezels'; root.add(bezel);
    }
    rigs.set(root, rig);
  }
  rig.front.forEach(material => material.emissiveIntensity = state === 2 ? 6 : state === 1 ? 1.7 : 0);
  rig.tail.forEach(material => material.emissiveIntensity = state ? .3 : 0);
}
