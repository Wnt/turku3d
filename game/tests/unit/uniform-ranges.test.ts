import { expect, test } from 'vitest';
// @ts-expect-error r186 internal uniforms group has no DefinitelyTyped declaration.
import UniformsGroup from 'three/src/renderers/common/UniformsGroup.js';
import { stampUniformRanges } from '../../src/render/uniformRanges';

test('uniform update ranges queue once per upload without a per-upload Set', () => {
  const group = new UniformsGroup('object');
  const matrix = { index: 0, offset: 0, itemSize: 16 }, color = { index: 1, offset: 16, itemSize: 4 };
  // r186 as shipped: the Set gets a fresh table on every clear.
  group.addUniformUpdateRange(matrix); group.addUniformUpdateRange(matrix);
  expect(group.updateRanges).toHaveLength(1);
  expect(group._addedIndices.size).toBe(1);
  group.clearUpdateRanges();

  stampUniformRanges(UniformsGroup.prototype);
  for (let upload = 0; upload < 3; upload++) {
    group.addUniformUpdateRange(matrix); group.addUniformUpdateRange(color); group.addUniformUpdateRange(matrix);
    expect(group.updateRanges.map((r: { start: number; count: number }) => [r.start, r.count])).toEqual([[0, 16], [16, 4]]);
    group.clearUpdateRanges();
    expect(group.updateRanges).toHaveLength(0);
  }
  // The Set is no longer used at all; the range objects are the cached ones, reused across uploads.
  group.addUniformUpdateRange(matrix);
  expect(group._addedIndices.size).toBe(0);
  expect(group.updateRanges[0]).toBe(group._updateRangeCache.get(0));
});
