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

test('ctl, w and st: a car pose keeps its controls, spin and room-clock stamp; malformed ones are rejected', () => {
  const m = pose({ mode: 'car', vehicle: 'golf7/red', ctl: [-35, 0, 412, 6], w: [0.0004, -0.1234567, 1.5], st: 1794250028385.4 });
  assert.equal(validPose(m), true);
  assert.deepEqual(cleanPose(m).ctl, [-35, 0, 412, 6]);
  assert.deepEqual(cleanPose(m).w, [0, -0.123, 1.5]);
  assert.equal(cleanPose(m).st, 1794250028385);
  assert.equal(['ctl', 'w', 'st'].some(k => k in cleanPose(pose())), false);
  for (const bad of [{ ctl: [101, 0, 0, 4] }, { ctl: [0, -1, 0, 4] }, { ctl: [0, 0, 1001, 4] }, { ctl: [0, 0, 0, 8] }, { ctl: [0.5, 0, 0, 4] },
    { ctl: [0, 0, 0] }, { w: [0, 0, 101] }, { w: [0, 0] }, { w: [0, 0, NaN] }, { st: -1 }, { st: '5' }])
    assert.equal(validPose(pose(bad)), false, JSON.stringify(bad));
});
