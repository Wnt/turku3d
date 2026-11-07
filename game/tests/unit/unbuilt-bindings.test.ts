import { expect, test } from 'vitest';
// @ts-expect-error r186 internal bindings manager has no DefinitelyTyped declaration.
import Bindings from 'three/src/renderers/common/Bindings.js';
import { releaseUnbuiltBindings } from '../../src/render/programCache';

// A disposed render object as r186's RenderObjects.onDispose hands it to Bindings.deleteForRender.
function renderObject(built: boolean) {
  let builds = 0, released = 0;
  const groups = built ? [{ name: 'object', bindings: [] }] : null;
  const object = {
    _bindings: groups as unknown,
    get builds() { return builds; },
    get released() { return released; },
    // r186 RenderObject.getBindings: a missing node program is built here, synchronously (getForRender).
    getBindings() { released++; if (!object._bindings) { builds++; object._bindings = [{ name: 'object', bindings: [] }]; } return object._bindings; },
  };
  return object;
}

test('disposing a draw whose node program was never built builds nothing; built draws still release their groups', () => {
  const make = () => new Bindings({}, {}, {}, {}, {}, {});

  // Unpatched r186: the dispose of an unbuilt draw runs its node build first.
  const plain = make(), before = renderObject(false);
  plain.get(before).x = 1;
  plain.deleteForRender(before);
  expect(before.builds).toBe(1);

  const bindings = make();
  releaseUnbuiltBindings(bindings);
  const unbuilt = renderObject(false), built = renderObject(true);
  bindings.get(unbuilt).x = 1; bindings.get(built).x = 1;
  bindings.deleteForRender(unbuilt);
  expect([unbuilt.builds, unbuilt.released, bindings.has(unbuilt)]).toEqual([0, 0, false]);
  // A built draw goes through r186's own release of its groups.
  bindings.deleteForRender(built);
  expect([built.builds, built.released, bindings.has(built)]).toEqual([0, 1, false]);
});
