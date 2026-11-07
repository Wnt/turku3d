import { afterEach, expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
// @ts-expect-error r186 internal cache has no DefinitelyTyped declaration.
import NodeManager from 'three/src/renderers/common/nodes/NodeManager.js';
// @ts-expect-error r186 internal render object has no DefinitelyTyped declaration.
import RenderObject from 'three/src/renderers/common/RenderObject.js';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { instancePrograms, previousColumns, shareInstanceProgram, shareInstancePrograms, type InstanceDraw } from '../../src/render/instancePrograms';

let cleanup: (() => void) | null = null;
afterEach(() => { cleanup?.(); cleanup = null; });

function fixture() {
  const renderer = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  renderer.hasFeature = () => false;
  Object.assign(renderer.backend, { capabilities: { getUniformBufferLimit: () => 65536 } });
  const nodes = new NodeManager(renderer, renderer.backend), scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera();
  const updated: InstanceDraw[] = [];
  const geometries = { updateForRender(draw: InstanceDraw) { updated.push(draw); } };
  const sharing = shareInstancePrograms({ _nodes: nodes, _geometries: geometries } as unknown as THREE.WebGPURenderer);
  renderer.debug.onNodeBuilderCreated = (builder, draw) => {
    instancePrograms(builder, (draw as InstanceDraw).object); sharing.builder(builder, draw as InstanceDraw);
  };
  cleanup = () => sharing.dispose();
  const lights = renderer.lighting.getNode(scene);
  const material = new THREE.MeshStandardNodeMaterial({ roughness: .9 }), geometry = new THREE.BoxGeometry();
  const mesh = (count: number, colors = true, shared = true) => {
    const value = new THREE.InstancedMesh(geometry, material, count);
    for (let i = 0; i < count; i++) value.setMatrixAt(i, new THREE.Matrix4().makeTranslation(i * 10 + count, 0, 0));
    if (colors) for (let i = 0; i < count; i++) value.setColorAt(i, new THREE.Color(i / count, .5, 1));
    if (shared) shareInstanceProgram(value);
    return value;
  };
  const draw = (object: THREE.Object3D, context: object = { id: 1, mrt: null }) => {
    const value = new RenderObject(nodes, geometries, renderer, object, material, scene, camera, lights, context, null) as InstanceDraw;
    (value as unknown as { onDispose(): void }).onDispose = () => nodes.delete(value);
    return value;
  };
  return { nodes, geometries, updated, mesh, draw, material };
}

const arrays = (draw: InstanceDraw) => draw.getAttributes().filter(attribute => !Object.values(draw.geometry.attributes).includes(attribute))
  .map(attribute => (attribute as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute
    ? (attribute as THREE.InterleavedBufferAttribute).data.array : (attribute as THREE.BufferAttribute).array);

test('opted-in instanced tile draws share one built state and bind their own matrix/colour buffers', () => {
  const { nodes, mesh, draw } = fixture(), create = vi.spyOn(nodes, '_createNodeBuilder');
  const meshes = [mesh(3), mesh(7), mesh(12)], draws = meshes.map(value => draw(value));
  const states = draws.map(value => nodes.getForRender(value));
  expect(new Set(states).size).toBe(1); expect(create).toHaveBeenCalledOnce();
  for (let i = 0; i < meshes.length; i++) {
    const own = arrays(draws[i]);
    // Four matrix columns on one instanced interleaved buffer plus the colour attribute, all over this mesh's arrays.
    expect(own.filter(array => array === meshes[i].instanceMatrix.array)).toHaveLength(4);
    expect(own.filter(array => array === meshes[i].instanceColor!.array)).toHaveLength(1);
    for (const other of meshes) if (other !== meshes[i])
      expect(own.some(array => array === other.instanceMatrix.array || array === other.instanceColor!.array)).toBe(false);
    const buffers = draws[i].vertexBuffers!;
    expect(buffers.some(buffer => (buffer as unknown as { isInstancedInterleavedBuffer?: boolean }).isInstancedInterleavedBuffer
      && buffer.array === meshes[i].instanceMatrix.array)).toBe(true);
    expect(draws[i].getAttributes()).toBe(draws[i].getAttributes());
  }
  for (const value of draws) (value as unknown as { dispose(): void }).dispose();
});

test('r186 per-object keys remain for non-opted meshes, velocity passes and different instance layouts', () => {
  const { nodes, mesh, draw } = fixture(), create = vi.spyOn(nodes, '_createNodeBuilder');
  const plain = [mesh(2, true, false), mesh(4, true, false)].map(value => nodes.getForRender(draw(value)));
  expect(plain[0]).not.toBe(plain[1]);
  const velocity = { id: 2, mrt: new THREE.MRTNode({ output: THREE.TSL.output, velocity: THREE.TSL.velocity }) };
  const moving = [mesh(2), mesh(4)].map(value => nodes.getForRender(draw(value, velocity)));
  expect(moving[0]).not.toBe(moving[1]);
  const colourless = nodes.getForRender(draw(mesh(5, false))), coloured = nodes.getForRender(draw(mesh(5)));
  expect(colourless).not.toBe(coloured);
  expect(create).toHaveBeenCalledTimes(6);
});

test('still instances (vegetation) share one state in velocity passes too, without previous-frame instance data', () => {
  const { nodes, mesh, draw } = fixture(), create = vi.spyOn(nodes, '_createNodeBuilder');
  const velocity = { id: 2, mrt: new THREE.MRTNode({ output: THREE.TSL.output, velocity: THREE.TSL.velocity }) };
  const trees = [mesh(2), mesh(9), mesh(64)];
  for (const tree of trees) shareInstanceProgram(tree, true);
  const states = trees.map(value => nodes.getForRender(draw(value, velocity)));
  expect(new Set(states).size).toBe(1); expect(create).toHaveBeenCalledOnce(); // a grown pool reuses the program
  const builder = create.mock.results[0].value as { needsPreviousData(): boolean };
  expect(builder.needsPreviousData()).toBe(false);
});

test('shared draws follow their own instance updates and survive the build owner leaving', () => {
  const { nodes, mesh, draw, geometries, updated } = fixture();
  const owner = mesh(3), other = mesh(6), first = draw(owner), second = draw(other);
  const state = nodes.getForRender(first); expect(nodes.getForRender(second)).toBe(state);
  const column = second.getAttributes().find(attribute => (attribute as THREE.InterleavedBufferAttribute).data?.array === other.instanceMatrix.array) as THREE.InterleavedBufferAttribute;
  geometries.updateForRender(second); expect(column.data.version).toBe(other.instanceMatrix.version);
  other.setMatrixAt(2, new THREE.Matrix4().makeTranslation(1, 2, 3));
  other.instanceMatrix.clearUpdateRanges(); other.instanceMatrix.addUpdateRange(32, 16); other.instanceMatrix.needsUpdate = true;
  geometries.updateForRender(second);
  expect(column.data.version).toBe(other.instanceMatrix.version);
  expect(column.data.updateRanges).toEqual([{ start: 32, count: 16 }]);
  expect(updated.at(-1)).toBe(second);
  (first as unknown as { dispose(): void }).dispose();
  expect((state as unknown as { usedTimes: number }).usedTimes).toBe(1);
  expect(nodes.getForRender(draw(mesh(9)))).toBe(state);
  (second as unknown as { dispose(): void }).dispose();
});

test('moving instances (fleet) share one velocity program; each mesh binds and keeps its own previous matrices and role attributes', () => {
  const { nodes, draw } = fixture(), create = vi.spyOn(nodes, '_createNodeBuilder');
  const paints = new WeakMap<THREE.Object3D, THREE.InstancedBufferAttribute>();
  const material = new THREE.MeshStandardNodeMaterial();
  // Like actors/parkedPaint.ts paintNode: the graph binds builder.object's own per-instance attribute.
  material.colorNode = THREE.TSL.Fn((builder: { object: THREE.Object3D }) => THREE.TSL.instancedBufferAttribute(paints.get(builder.object)!, 'vec3'))() as unknown as THREE.Node<'vec3'>;
  const geometry = new THREE.BoxGeometry();
  const car = (count: number) => {
    const mesh = new THREE.InstancedMesh(geometry, material, count);
    for (let i = 0; i < count; i++) mesh.setMatrixAt(i, new THREE.Matrix4().makeTranslation(i * 4, 0, count));
    const paint = new THREE.InstancedBufferAttribute(new Float32Array(count * 3).fill(count / 10), 3);
    paints.set(mesh, paint);
    shareInstanceProgram(mesh, false, { moving: true, attributes: { paint } });
    return { mesh, paint };
  };
  const velocity = { id: 2, mrt: new THREE.MRTNode({ output: THREE.TSL.output, velocity: THREE.TSL.velocity }) };
  const cars = [car(4), car(16), car(64)];
  const draws = cars.map(({ mesh }) => { const value = draw(mesh, velocity); (value as unknown as { material: THREE.Material }).material = material; return value; });
  // r186 asks the renderer's MRT whether a build needs previous-frame data (beauty's velocity output).
  const renderer = (nodes as unknown as { renderer: THREE.WebGPURenderer }).renderer;
  renderer.setMRT(velocity.mrt);
  const states = draws.map(value => nodes.getForRender(value));
  renderer.setMRT(null);
  expect(new Set(states).size).toBe(1); expect(create).toHaveBeenCalledOnce();
  for (let i = 0; i < cars.length; i++) {
    const { mesh, paint } = cars[i], own = arrays(draws[i]);
    expect(own.filter(array => array === mesh.instanceMatrix.array)).toHaveLength(4);
    expect(own.filter(array => array === previousColumns(mesh).array)).toHaveLength(4);
    expect(draws[i].getAttributes()).toContain(paint);
    for (const other of cars) if (other !== cars[i])
      expect(own.some(array => array === other.mesh.instanceMatrix.array || array === other.paint.array || array === previousColumns(other.mesh).array)).toBe(false);
  }
  // r186's after-draw hook, run for a non-owner mesh: it keeps that mesh's own previous matrices (and does not throw).
  const hook = (states[0] as unknown as { updateAfterNodes: { updateAfter(frame: object): void }[] }).updateAfterNodes;
  const { mesh } = cars[1];
  const before = previousColumns(mesh).array.slice();
  mesh.setMatrixAt(3, new THREE.Matrix4().makeTranslation(100, 2, 3)); mesh.instanceMatrix.needsUpdate = true;
  expect(Array.from(previousColumns(mesh).array)).toEqual(Array.from(before));
  for (const node of hook) node.updateAfter({ object: mesh });
  expect(Array.from(previousColumns(mesh).array)).toEqual(Array.from(mesh.instanceMatrix.array));
  expect(previousColumns(mesh).buffer.version).toBe(mesh.instanceMatrix.version);
  for (const node of hook) node.updateAfter({ object: cars[0].mesh });
  // A shadow (no velocity) pass of the same meshes shares a separate program without previous data.
  const shadow = cars.map(({ mesh: value }) => { const d = draw(value); (d as unknown as { material: THREE.Material }).material = material; return nodes.getForRender(d); });
  expect(new Set(shadow).size).toBe(1); expect(shadow[0]).not.toBe(states[0]);
});
