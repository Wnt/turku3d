import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { TrafficSignals, signalHeads } from '../../src/traffic/signals';
import { signalEditable } from '../../src/dev/editor/catalog';
import { provenanceOf } from '../../src/dev/editor/provenance';
import { EVENT, type RoadGraphJson } from '../../src/gameplay/roadGraph';

// One two-way street west→east along z = 0 with a signal at s = 40 m for both directions, controller "j1".
const roads: RoadGraphJson = { format: 'turku3d-roadgraph', version: 1, revision: 2, frame: 'engine-xz', bbox: [0, -50, 100, 50],
  nodes: [0, 0, 100, 0], edges: [[0, 1, 2 | (2 << 3), 100]], edgeIds: ['e1'],
  edgeAttrColumns: ['way', 'lanes_fwd', 'lanes_bwd', 'width'], edgeAttrs: [[777, 1, 1, 7]],
  edgeHeights: [[4, 4]], edgeEvents: [[40, EVENT.SIGNAL | EVENT.FORWARD | EVENT.BACKWARD, 0]], controllers: [['j1', 40, 0]] };

test('signal heads carry their junction, approach edge and direction; the editor lists them with provenance', () => {
  const heads = signalHeads('t0_0_-1', roads);
  expect(heads.length).toBe(2);
  expect(heads[0]).toMatchObject({ controller: 'j1', source: { edge: 'e1', way: 777, s: 40, direction: 'forward', lanes: 1 } });
  const e = signalEditable('t0_0_-1', heads[0]);
  expect(e).toMatchObject({ category: 'signal', type: 'traffic_signal', base: { x: 40, y: 4 } });
  expect(e.key).toBe(`signal:t0_0_-1:${heads[0].key}`);
  const p = provenanceOf(e, []);
  expect(p).toMatchObject({ dataset: 'osm', record: { junction: 'j1', approach: { edge: 'e1', direction: 'forward', way: 777 } } });
});

test('a previewed head moves and turns only its drawing; hidden heads leave the draw; phases keep the approach', () => {
  const scene = new THREE.Scene(), signals = new TrafficSignals(scene);
  signals.setTile('t0_0_-1', roads);
  const [pole] = scene.getObjectByName('traffic-signals')!.children as THREE.InstancedMesh[];
  const heads: string[] = []; signals.forEachHead((_t, h) => heads.push(h.key));
  const at = (i: number) => { const m = new THREE.Matrix4(); pole.getMatrixAt(i, m); return new THREE.Vector3().setFromMatrixPosition(m); };
  const sorted = [...heads].sort();
  const before = at(0);
  signals.setEdit(sorted[0], { x: 45, y: 4, z: 6, yaw: 0 });
  expect(at(0).x).toBeCloseTo(45); expect(at(0).z).toBeCloseTo(6);
  signals.setEdit(sorted[0], null);
  expect(pole.count).toBe(1);
  signals.setEdit(sorted[0], undefined);
  expect(pole.count).toBe(2); expect(at(0).distanceTo(before)).toBeLessThan(1e-6);
  // forEachHead lists the roads' heads, never a preview.
  const listed: number[] = []; signals.forEachHead((_t, h) => listed.push(h.x));
  expect(listed.every(x => Math.abs(x - 40) < 1e-6)).toBe(true);
  signals.update(12.3); signals.dispose();
});
