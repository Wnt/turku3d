import { expect, test } from 'vitest';
// @ts-expect-error r186 internal node manager has no DefinitelyTyped declaration.
import NodeManager from 'three/src/renderers/common/nodes/NodeManager.js';
import { retainNodeStates } from '../../src/render/programCache';

type State = { usedTimes: number; name: string };
// A render object as r186's NodeManager sees it: getForRender caches its state under initialCacheKey.
function draw(nodes: InstanceType<typeof NodeManager>, key: number, make: () => State) {
  const object = { isRenderObject: true, initialCacheKey: key };
  let state = nodes.nodeBuilderCache.get(key) as State | undefined;
  if (!state) { state = make(); nodes.nodeBuilderCache.set(key, state); }
  state.usedTimes++;
  nodes.get(object).nodeBuilderState = state;
  return object;
}

test('a node build whose last draw left is reused by the next draw with the same key, within the idle bound', () => {
  const nodes = new NodeManager({}, {});
  retainNodeStates(nodes, 3);
  let builds = 0;
  const build = (name: string) => () => { builds++; return { usedTimes: 0, name }; };
  // The only draw of a fence pool leaves (the pool grows and replaces its mesh); its successor must not rebuild.
  const first = draw(nodes, 1, build('fence'));
  nodes.delete(first);
  expect(nodes.nodeBuilderCache.get(1)?.usedTimes).toBe(0);
  const next = draw(nodes, 1, build('fence'));
  expect(builds).toBe(1);
  // Other families come and go: idle states beyond the bound are forgotten oldest first, live ones never.
  for (let key = 10; key < 20; key++) nodes.delete(draw(nodes, key, build(`tile${key}`)));
  expect(nodes.nodeBuilderCache.get(1)).toBeDefined();
  expect([...nodes.nodeBuilderCache.keys()].filter(key => key >= 10)).toEqual([17, 18, 19]);
  // Released again, the fence build is the newest idle one and survives the next three releases only.
  nodes.delete(next);
  expect(nodes.nodeBuilderCache.get(1)?.usedTimes).toBe(0);
  for (let key = 20; key < 23; key++) nodes.delete(draw(nodes, key, build(`tile${key}`)));
  expect(nodes.nodeBuilderCache.has(1)).toBe(false);
  // Non-render objects pass straight through.
  const material = {};
  nodes.get(material).x = 1;
  nodes.delete(material);
  expect(nodes.has(material)).toBe(false);
});
