import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cleanPose, validDrv, validPose } from '../pose.js';

const pose = (extra = {}) => ({ t: 1, E: 1, N: 2, H: 3, yaw: 0, vE: 0, vN: 0, vH: 0, mode: 'truck', vehicle: 'truck', carrying: false, ...extra });

test('drv: a driven truck pose keeps its integer drv; a pose without drv stays valid', () => {
  assert.equal(validPose(pose()), true);
  assert.equal('drv' in cleanPose(pose()), false);
  const m = pose({ drv: [87, 612, 2, 0] });
  assert.equal(validPose(m), true);
  assert.deepEqual(cleanPose(m).drv, [87, 612, 2, 0]);
  assert.deepEqual(cleanPose(pose({ drv: [0, 0, -1, 3] })).drv, [0, 0, -1, 3]);
});

test('drv: floats, out-of-range values and wrong lengths are rejected', () => {
  for (const drv of [[87.5, 612, 2, 0], [101, 612, 2, 0], [50, 801, 2, 0], [50, 600, 4, 0], [50, 600, -2, 0], [50, 600, 1, 4],
    [50, 600, 1], [50, 600, 1, 0, 0], '50,600,1,0', null, { 0: 1 }]) {
    assert.equal(validDrv(drv), false, JSON.stringify(drv));
    assert.equal(validPose(pose({ drv })), false, JSON.stringify(drv));
  }
});
