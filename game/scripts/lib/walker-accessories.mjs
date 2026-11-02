// Original, unbranded everyday props. Generated geometry only; no third-party models or textures.
import { BoxGeometry, CubicBezierCurve3, SphereGeometry, TubeGeometry, Vector3 } from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';

export function addWalkerAccessories(doc, spec) {
  const scene = doc.getRoot().listScenes()[0], skin = doc.getRoot().listSkins()[0], joints = skin.listJoints();
  const buffer = doc.getRoot().listBuffers()[0];
  const material = new Map();
  const add = (geometry, region, boneName) => {
    let mat = material.get(region);
    if (!mat) { mat = doc.createMaterial(region).setBaseColorFactor([1, 1, 1, 1]); material.set(region, mat); }
    const n = geometry.attributes.position.count, j = joints.findIndex(b => b.getName() === boneName);
    if (j < 0) throw new Error(`Accessory bone ${boneName} missing`);
    const ids = new Uint16Array(n * 4), weights = new Float32Array(n * 4);
    for (let i = 0; i < n; i++) { ids[i * 4] = j; weights[i * 4] = 1; }
    const acc = (type, array) => doc.createAccessor().setType(type).setArray(array).setBuffer(buffer);
    const primitive = doc.createPrimitive().setMaterial(mat)
      .setAttribute('POSITION', acc('VEC3', Float32Array.from(geometry.attributes.position.array)))
      .setAttribute('NORMAL', acc('VEC3', Float32Array.from(geometry.attributes.normal.array)))
      .setAttribute('TEXCOORD_0', acc('VEC2', new Float32Array(n * 2).fill(0.5)))
      .setAttribute('JOINTS_0', acc('VEC4', ids)).setAttribute('WEIGHTS_0', acc('VEC4', weights))
      .setIndices(acc('SCALAR', geometry.index ? Uint32Array.from(geometry.index.array) : Uint32Array.from({length:n},(_,i)=>i)));
    scene.addChild(doc.createNode('human-accessory').setMesh(doc.createMesh().addPrimitive(primitive)).setSkin(skin));
    geometry.dispose();
  };
  const female = spec.morphs[0][0] === 0, shift = female ? -0.065 : 0;
  const rounded = (size, point, radius, region = 'Accessory', bone = 'DEF-spine.003') =>
    add(new RoundedBoxGeometry(...size, 2, radius).translate(point[0], point[1] + shift, point[2]), region, bone);
  if (spec.accessory === 'backpack') {
    rounded([0.29, 0.38, 0.14], [0, 1.25, -0.16], 0.045);
    rounded([0.235, 0.16, 0.045], [0, 1.15, -0.25], 0.02);
    // Two shoulder straps follow the torso; narrow dark webbing survives the far LOD.
    for (const sign of [-1, 1]) {
      const curve = new CubicBezierCurve3(new Vector3(sign * 0.105, 1.42 + shift, -0.19), new Vector3(sign * 0.18, 1.56 + shift, 0),
        new Vector3(sign * 0.17, 1.22 + shift, 0.135), new Vector3(sign * 0.12, 1.10 + shift, -0.13));
      add(new TubeGeometry(curve, 12, 0.013, 4, false), 'Accessory', 'DEF-spine.003');
    }
    add(new BoxGeometry(0.20, 0.006, 0.008).translate(0, 1.24 + shift, -0.276), 'AccessoryTrim', 'DEF-spine.003');
  }
  if (spec.accessory === 'crossbody') {
    rounded([0.22, 0.21, 0.08], [0.20, 0.99, 0.065], 0.025, 'Accessory', 'DEF-hips');
    const curve = new CubicBezierCurve3(new Vector3(-0.17, 1.47 + shift, 0.02), new Vector3(-0.14, 1.40 + shift, 0.15),
      new Vector3(0.12, 1.12 + shift, 0.15), new Vector3(0.20, 1.07 + shift, 0.09));
    add(new TubeGeometry(curve, 14, 0.009, 4, false), 'Accessory', 'DEF-spine.002');
    add(new BoxGeometry(0.14, 0.005, 0.008).translate(0.20, 1.06 + shift, 0.109), 'AccessoryTrim', 'DEF-hips');
  }
  if (spec.accessory === 'cap') {
    const crown = new SphereGeometry(1, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2);
    crown.scale(0.10, 0.105, 0.118).translate(0, 1.665 + shift, 0.006);
    add(crown, 'AccessoryTrim', 'DEF-head');
    add(new RoundedBoxGeometry(0.175, 0.009, 0.115, 2, 0.006).rotateX(-0.11).translate(0, 1.67 + shift, 0.10), 'AccessoryTrim', 'DEF-head');
  }
}
