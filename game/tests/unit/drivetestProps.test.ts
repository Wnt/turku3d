import { describe, expect, it } from 'vitest';
import { glbJson, polesOf } from '../../src/qa/drivetest/props';

function glb(json: object): Uint8Array {
  let text = JSON.stringify(json);
  while (text.length % 4) text += ' ';
  const body = new TextEncoder().encode(text), out = new Uint8Array(20 + body.length), view = new DataView(out.buffer);
  view.setUint32(0, 0x46546c67, true); view.setUint32(4, 2, true); view.setUint32(8, out.length, true);
  view.setUint32(12, body.length, true); view.setUint32(16, 0x4e4f534a, true);
  out.set(body, 20);
  return out;
}

describe('drive sweep props', () => {
  it('reads the sign and bus stop bodies the client spawns, at their recorded bases', () => {
    const gltf = {
      scene: 0, scenes: [{ nodes: [0] }],
      nodes: [
        { children: [1, 2, 3] },
        { mesh: 0, translation: [5, 0, 5], extras: { layer: 'props', objects: [
          { prop_type: 'sign', pole_id: 't0_0_0:sign-a', pole_base: [12, 3, 4], pole_height: 3.1 },
          { prop_type: 'bench', object_id: 'b' },
        ] } },
        { translation: [40, 2, -8], extras: { prop_type: 'bus_stop', tile: 't0_0_0', object_id: 'stop' } },
        { translation: [1, 1, 1], extras: { prop_type: 'lamp', tile: 't0_0_0', object_id: 'lamp' } },
      ],
    };
    const poles = polesOf(glbJson(glb(gltf)));
    expect(poles).toEqual([
      { id: 't0_0_0:sign-a', base: { x: 12, y: 3, z: 4 }, yaw: 0, height: 3.1 },
      { id: 't0_0_0:stop', base: { x: 40, y: 2, z: -8 }, yaw: 0, height: 2.7 },
    ]);
  });
});
