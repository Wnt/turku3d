import { describe, expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { CandidateCycle, pickCandidates, rayToVertical } from '../../src/dev/editor/pick';
import { EditableCatalog, principalYaw, scanProps } from '../../src/dev/editor/catalog';
import { Previews, previewMatrix, rangeGeometry, type PreviewState } from '../../src/dev/editor/preview';
import { moveLamp } from '../../src/render/lights';
import type { Lamp } from '../../src/render/lights/lampField';

const item = (key: string, x: number, z: number, radius = 0.5, height = 2) => ({ key, base: { x, y: 0, z }, height, radius });
const o = { x: 0, y: 1.6, z: 0 }, north = { x: 0, y: 0, z: -1 };

describe('picking', () => {
  test('closest approach to a vertical segment', () => {
    expect(rayToVertical(o, north, { x: 0, y: 0, z: -10 }, 3)).toEqual({ t: 10, distance: 0 });
    const side = rayToVertical(o, north, { x: 2, y: 0, z: -10 }, 3);
    expect(side.t).toBeCloseTo(10); expect(side.distance).toBeCloseTo(2);
    // Looking down at a short bench from above: the closest point is its top.
    const d = new THREE.Vector3(0, -1, -1).normalize();
    const down = rayToVertical({ x: 0, y: 10, z: 0 }, d, { x: 0, y: 0, z: -9.5 }, 0.5);
    expect(down.distance).toBeLessThan(0.01);
  });

  test('direct hits nearest first, near misses after them, behind-the-wall and far ones dropped', () => {
    const items = [item('far', 0, -40), item('near', 0, -8), item('miss', 0.7, -10, 0.4), item('wide', 5, -10), item('behind', 0, -30)];
    const all = pickCandidates(o, north, items);
    expect(all.map(c => c.item.key)).toEqual(['near', 'behind', 'far', 'miss']);
    expect(pickCandidates(o, north, items, 20).map(c => c.item.key)).toEqual(['near', 'miss']);
    expect(pickCandidates(o, north, [item('beyond', 0, -130)])).toEqual([]);
  });

  test('the cycled choice stays on its placement while the list reorders, and resets when it leaves', () => {
    const cycle = new CandidateCycle<ReturnType<typeof item>>();
    const a = item('a', 0, -5), b = item('b', 0, -7), c = item('c', 0, -9);
    cycle.update(pickCandidates(o, north, [a, b, c]));
    expect(cycle.current?.item.key).toBe('a');
    cycle.cycle(1); expect(cycle.current?.item.key).toBe('b');
    cycle.cycle(-2); expect(cycle.current?.item.key).toBe('c');
    cycle.update(pickCandidates(o, north, [c, item('d', 0, -3)]));
    expect(cycle.current?.item.key).toBe('c'); expect(cycle.index).toBe(1);
    cycle.update(pickCandidates(o, north, [a, b]));
    expect(cycle.current?.item.key).toBe('a');
  });
});

/** A merged props batch as the packager writes it: two benches and a sign, rows with index ranges. */
function batch() {
  const box = (w: number, h: number, d: number, x: number, z: number, yaw: number) =>
    new THREE.BoxGeometry(w, h, d).translate(0, h / 2, 0).rotateY(yaw).translate(x, 3, z);
  const parts = [box(1.8, 0.8, 0.5, 10, -20, 0), box(1.8, 0.8, 0.5, 14, -20, Math.PI / 4), box(0.1, 2.7, 0.1, 18, -22, 0)];
  const positions: number[] = [], indices: number[] = [], rows: Record<string, unknown>[] = [];
  const types = ['bench', 'bench', 'sign'];
  parts.forEach((g, i) => {
    const offset = positions.length / 3, start = indices.length;
    positions.push(...g.attributes.position.array);
    for (const v of g.index!.array) indices.push(v + offset);
    rows.push({ start, count: g.index!.count, object_id: `GIS_Penkit:${i}`, prop_type: types[i], tile: 't0_0_-1', layer: 'props',
      ...(types[i] === 'sign' ? { pole_base: [18, 3, -22], pole_id: 't0_0_-1:GIS_Penkit:2' } : {}) });
  });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(new Array(positions.length / 3 * 2).fill(0.5), 2));
  geometry.setIndex(indices);
  const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial());
  mesh.userData = { layer: 'props', tile: 't0_0_-1', objects: rows, object_id: 't0_0_-1/batch/1/2' };
  const model = new THREE.Group(); model.add(mesh); model.updateMatrixWorld(true);
  return { model, mesh };
}

describe('catalog and previews', () => {
  test('prop rows resolve to their source placements with base, size and estimated axis', () => {
    const { model } = batch();
    const props = scanProps(model);
    expect(props.map(p => p.key)).toEqual(['prop:t0_0_-1:GIS_Penkit:0', 'prop:t0_0_-1:GIS_Penkit:1', 'prop:t0_0_-1:GIS_Penkit:2']);
    expect(props[0].base.x).toBeCloseTo(10); expect(props[0].base.y).toBeCloseTo(3); expect(props[0].base.z).toBeCloseTo(-20);
    expect(props[0].height).toBeCloseTo(0.8); expect(props[0].yawDeg).toBeCloseTo(0); expect(props[0].yawSource).toBe('estimate-axis');
    expect(props[1].yawDeg).toBeCloseTo(45); // turned by rotation.y = 45°: the long axis, mod 180
    expect(props[2].base).toEqual({ x: 18, y: 3, z: -22 }); // the recorded pole base
    expect(principalYaw([0, 1, 0, 1], [0, 0, 1, 1])).toBeNull();
  });

  test('a prop preview degenerates only its own range, draws a moved copy, and restores the batch exactly', () => {
    const { model, mesh } = batch();
    const catalog = new EditableCatalog(() => null, () => null);
    const props = catalog.addModel(model, true);
    expect(new EditableCatalog(() => null, () => null).addModel(model)).toEqual([]); // lazy unless asked
    const original = Array.from(mesh.geometry.index!.array);
    const previews = new Previews({ vegetation: () => null, lights: null });
    const e = props[0];
    const state: PreviewState = { key: e.key, category: 'prop', objectId: e.objectId, tile: e.tile, original: e.base,
      after: { x: e.base.x + 2, y: e.base.y + 0.1, z: e.base.z }, turnDeg: 90, deleted: false, originalYawDeg: e.yawDeg };
    previews.set(e.key, state, e);
    const ref = e.ref.kind === 'prop' ? e.ref : null!;
    const index = mesh.geometry.index!.array;
    expect(new Set(Array.from(index.slice(ref.start, ref.start + ref.count))).size).toBe(1);
    expect(Array.from(index.slice(ref.count))).toEqual(original.slice(ref.count));
    const copy = model.children.find(c => c.userData.editorPreview === e.key) as THREE.Mesh;
    expect(copy).toBeTruthy();
    copy.geometry.computeBoundingBox();
    const box = copy.geometry.boundingBox!.clone().applyMatrix4(copy.matrixWorld);
    const centre = box.getCenter(new THREE.Vector3());
    expect(centre.x).toBeCloseTo(12); expect(box.min.y).toBeCloseTo(3.1);
    expect(box.max.x - box.min.x).toBeCloseTo(0.5); // turned 90°: the 1.8 m bench now runs along z
    expect(copy.geometry.getAttribute('uv').count).toBe(copy.geometry.getAttribute('position').count);
    // Delete hides it (no copy), then clearing restores the original index.
    previews.set(e.key, { ...state, deleted: true }, e);
    expect(model.children.some(c => c.userData.editorPreview)).toBe(false);
    previews.set(e.key, null, e);
    expect(Array.from(mesh.geometry.index!.array)).toEqual(original);
    // A model that leaves restores its batch too.
    previews.set(e.key, state, e); previews.detach(model);
    expect(Array.from(mesh.geometry.index!.array)).toEqual(original);
  });

  test('preview matrix turns about the original base, then moves; range geometry keeps every attribute', () => {
    const m = previewMatrix({ original: { x: 1, y: 0, z: 1 }, after: { x: 5, y: 1, z: 1 }, turnDeg: 90 });
    const p = new THREE.Vector3(2, 0, 1).applyMatrix4(m);
    expect(p.x).toBeCloseTo(5); expect(p.y).toBeCloseTo(1); expect(p.z).toBeCloseTo(0);
    const { mesh } = batch();
    const g = rangeGeometry(mesh.geometry, [0, 1, 2, 2, 1, 3]);
    expect(g.getAttribute('position').count).toBe(4); expect(g.index!.count).toBe(6);
  });

  test('a moved lamp carries its light head rigidly about the post base', () => {
    const lamp: Lamp = { key: '1,2', x: 0, y: 4, z: 0, yaw: 0, scale: 1, post: true, kind: 'street',
      light: { x: 1.6, y: 11.6, z: 0, radius: 1.2, r: 1, g: 1, b: 1, day: 0 } };
    const moved = moveLamp(lamp, { x: 10, y: 5, z: -3, yaw: Math.PI / 2 });
    expect(moved.light.x).toBeCloseTo(10); expect(moved.light.z).toBeCloseTo(-3 - 1.6); expect(moved.light.y).toBeCloseTo(12.6);
    expect(moved.key).toBe('1,2');
    // three.js rotation.y = 90° maps local +X to world -Z: the arm turns with the post.
    expect(new THREE.Vector3(1.6, 0, 0).applyAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI / 2).z).toBeCloseTo(-1.6);
  });
});

test('restoring a tree or post preview needs no resident editable (session close, undo off screen)', () => {
  const trees: [string, unknown][] = [], posts: [string, unknown][] = [];
  const previews = new Previews({ vegetation: () => ({ setEdit: (id, d) => { trees.push([id, d]); } }), lights: { setEdit: (k, e) => { posts.push([k, e]); } } });
  const base = { tile: 't0_0_0', original: { x: 0, y: 0, z: 0 }, after: { x: 1, y: 0, z: 0 }, turnDeg: 30, deleted: false, originalYawDeg: 10 };
  previews.set('tree:t0_0_0:puut:abc', { ...base, key: 'tree:t0_0_0:puut:abc', category: 'tree', objectId: 'puut:abc' });
  previews.set('lamp:t0_0_0:post:12,34', { ...base, key: 'lamp:t0_0_0:post:12,34', category: 'lamp', objectId: 'post:12,34' });
  previews.clear();
  expect(trees.map(([id, d]) => [id, d === undefined ? 'restore' : 'edit'])).toEqual([['puut:abc', 'edit'], ['puut:abc', 'restore']]);
  expect(posts[0]).toEqual(['12,34', { x: 1, y: 0, z: 0, yaw: 40 * Math.PI / 180 }]);
  expect(posts[1]).toEqual(['12,34', undefined]);
  previews.set('tree:t0_0_0:lidar:12.5:40.25', null);
  expect(trees.at(-1)).toEqual(['lidar:12.5:40.25', undefined]);
});

test('a barrier segment picks anywhere along it, highlights its length and resolves to its tile, kind and index', async () => {
  const { barrierEditable } = await import('../../src/dev/editor/catalog');
  // A 12 m fence along x centred at (20, 0, -10), 1.6 m high, 0.1 m thick.
  const matrix = new THREE.Matrix4().compose(new THREE.Vector3(20, 0, -10), new THREE.Quaternion(), new THREE.Vector3(12, 1.6, 0.1));
  const e = barrierEditable({ id: 'barrier:t0_0_-1:draft_fence:7', kind: 'fence', matrix });
  expect(e).toMatchObject({ key: 'barrier:t0_0_-1:barrier:t0_0_-1:draft_fence:7', category: 'barrier', type: 'fence', tile: 't0_0_-1',
    halfLength: 6, ref: { kind: 'barrier', index: 7 } });
  expect(e.anchors!.length).toBeGreaterThan(8);
  // Looking at its east end from the south: the centre is 5.5 m off the ray, an anchor is on it.
  const hits = pickCandidates({ x: 25.5, y: 1.6, z: 0 }, { x: 0, y: 0, z: -1 }, [e]);
  expect(hits.length).toBe(1); expect(hits[0].miss).toBe(0); expect(hits[0].t).toBeCloseTo(10, 0);
  const post = barrierEditable({ id: 'barrier:t0_0_-1:draft_bollard:0', kind: 'bollard',
    matrix: new THREE.Matrix4().compose(new THREE.Vector3(1, 0, 1), new THREE.Quaternion(), new THREE.Vector3(0.2, 0.9, 0.2)) });
  expect(post.anchors).toBeUndefined(); expect(post.radius).toBeCloseTo(0.3);
});
