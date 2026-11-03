import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
// @ts-expect-error r186 renderer internals have no DefinitelyTyped declarations.
import NodeUniformsGroup from 'three/src/renderers/common/nodes/NodeUniformsGroup.js';
// @ts-expect-error r186 renderer internals have no DefinitelyTyped declarations.
import { NumberNodeUniform, Matrix4NodeUniform } from 'three/src/renderers/common/nodes/NodeUniform.js';
import { uniformCensus } from '../../src/render/census';

/* eslint-disable @typescript-eslint/no-explicit-any */
test('the uniform census counts each changed uniform by pass, object, group and owning node, for one frame', async () => {
  // A ModelNode-like owner of a uniform node, and a named per-object uniform, as a draw's update nodes.
  const world = { isUniformNode: true, name: '', updateType: 'object', value: new THREE.Matrix4() };
  class ModelNode { scope = 'worldMatrix'; uniformNode = world; updateType = 'object'; }
  const lamp = { isUniformNode: true, name: 'lampLight', updateType: 'object', value: 0 };
  const group = new NodeUniformsGroup('object', { name: 'object' });
  group.addUniform(new Matrix4NodeUniform({ name: 'nodeUniform0', node: world, get value() { return world.value; } }));
  group.addUniform(new NumberNodeUniform({ name: 'nodeUniform1', node: lamp, get value() { return lamp.value; } }));
  const bindGroup = { bindings: [group] };
  const mesh = new THREE.Mesh(); mesh.name = 'surface-array';
  const renderObject = { object: mesh, camera: new THREE.PerspectiveCamera(), material: new THREE.MeshBasicMaterial(),
    getNodeBuilderState: () => ({ updateNodes: [new ModelNode(), lamp] }) };
  const backend = { updateBinding: () => {} };
  const bindings = {
    _update(this: any, bg: typeof bindGroup) { for (const b of bg.bindings) if (b.update()) backend.updateBinding(); },
    updateForRender(this: any, ro: typeof renderObject) { void ro; this._update(bindGroup, null); },
    updateSharedForRender() {},
  };
  const renderer = { _bindings: bindings, backend, getRenderTarget: () => null } as unknown as THREE.WebGPURenderer;
  let end: (() => void) | null = null;
  const frameEnds = { install: (each: () => void) => { end = each; }, uninstall: () => { end = null; } };
  const draw = () => bindings.updateForRender(renderObject);
  draw(); // uploads the first values
  const census = uniformCensus(renderer, frameEnds);
  draw(); end!(); // a partial frame: discarded
  lamp.value = 1; draw(); // the lamp light changes every frame; the static world matrix does not
  world.value.makeTranslation(1, 0, 0); draw(); // ...unless the object moves
  end!();
  const result = await census;
  expect(result.writes).toBe(2);
  expect(result.rows).toEqual([['screen|persp\tsurface-array\tobject\tlampLight(object)', 1], ['screen|persp\tsurface-array\tobject\tModelNode.worldMatrix.uniformNode(object)', 1]]);
  // Everything is restored.
  expect(Object.keys(bindings)).toEqual(['_update', 'updateForRender', 'updateSharedForRender']);
  expect(Object.prototype.hasOwnProperty.call(Object.getPrototypeOf(Object.getPrototypeOf(group)), 'updateByType')).toBe(true);
  expect(end).toBeNull();
});
