// View clearance (render/viewClearance.ts): the per-frame uniforms that fade vegetation between the camera and the
// player, and the road ahead of them.
import { describe, expect, it } from 'vitest';
import * as THREE from 'three/webgpu';
import { VIEW_CLEARANCE, viewClearance } from '../../src/render/viewClearance';

type Live = { live: THREE.Vector4; eased: number; camera: THREE.Camera | null };
const camera = () => {
  const c = new THREE.PerspectiveCamera(60, 16 / 9, 0.1, 1000);
  c.position.set(0, 3, 6); c.lookAt(0, 1, 0); c.updateMatrixWorld();
  return c;
};

describe('view clearance', () => {
  it('projects the body and the road ahead into tangent space with world radii over depth', () => {
    const c = camera(), body = new THREE.Vector3(0, 1, 0);
    for (let i = 0; i < 200; i++) viewClearance.update(c, { body, velocity: new THREE.Vector3(0, 0, -4), mode: 'foot' }, 1 / 60);
    const a = viewClearance.start.value, b = viewClearance.end.value;
    const depth = body.clone().applyMatrix4(c.matrixWorldInverse).z * -1;
    expect(a.z).toBeCloseTo(depth, 4);
    expect(a.x).toBeCloseTo(0, 4); expect(a.y).toBeCloseTo(0, 4); // the camera looks at the body
    expect(a.w).toBeCloseTo(VIEW_CLEARANCE.radius.foot / depth, 3);
    // Walking away at 4 m/s carries the ahead end 2.5 + 0.6 * 4 m further and down to the feet.
    const [base, seconds, cap] = VIEW_CLEARANCE.ahead.foot;
    expect(b.z - a.z).toBeGreaterThan(Math.min(cap, base + seconds * 4) * 0.8);
    expect(b.w).toBeLessThan(a.w); // farther, so smaller on screen even with the wider world radius
    expect((viewClearance as unknown as Live).eased).toBeCloseTo(1, 3);
  });

  it('eases out without a player and leaves other cameras unfaded', () => {
    const c = camera();
    viewClearance.update(c, null, 0.1);
    const state = viewClearance as unknown as Live;
    expect(state.eased).toBeLessThan(1);
    expect(state.eased).toBeGreaterThan(0.5); // no pop: one frame removes only part of it
    for (let i = 0; i < 300; i++) viewClearance.update(c, null, 1 / 60);
    expect(state.eased).toBeLessThan(1e-3);
    expect(state.live.y).toBe(1); // the near-camera fade stays for the play camera
    expect(state.camera).toBe(c);
    viewClearance.enabled = false;
    viewClearance.update(c, null, 1 / 60);
    expect(state.camera).toBeNull(); // every render then reads zero strength
    viewClearance.enabled = true;
  });

  it('shrinks to the body when the road ahead lies behind the camera', () => {
    const c = camera(), body = new THREE.Vector3(0, 1, 0);
    for (let i = 0; i < 400; i++) viewClearance.update(c, { body, velocity: new THREE.Vector3(0, 0, 12), mode: 'car' }, 1 / 60);
    expect(viewClearance.end.value.toArray()).toEqual(viewClearance.start.value.toArray());
  });
});
