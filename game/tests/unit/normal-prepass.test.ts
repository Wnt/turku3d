import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { AnalyticSky } from '../../src/render/analyticSky';
import { sceneryMaterial } from '../../src/render/sceneryMaterial';
import { NormalPrepass } from '../../src/render/normalPrepass';

test('the actual analytic sky retains its far-plane vertex shader and disabled depth writes', () => {
  const pre = new NormalPrepass(), sky = new AnalyticSky();
  try {
    expect(sky.material.vertexNode).toBeTruthy();
    expect(sky.material.depthWrite).toBe(false);
    expect(pre.select(sky.material)).toBeNull();
  } finally { pre.dispose(); sky.geometry.dispose(); sky.material.dispose(); }
});

test('ordinary opaque surfaces still share the cheap normal material while non-depth-writing surfaces retain theirs', () => {
  const pre = new NormalPrepass();
  const a = new THREE.MeshStandardNodeMaterial(), b = new THREE.MeshBasicNodeMaterial();
  try {
    expect(pre.select(a)).toBe(pre.select(b));
    b.depthWrite = false;
    expect(pre.select(b)).toBeNull();
  } finally { pre.dispose(); a.dispose(); b.dispose(); }
});


test('regional terrain keeps its coverage mask in the depth/normal/velocity pass', () => {
  const pre = new NormalPrepass(), coverage = new THREE.DataTexture(new Uint8Array(4),2,2);
  const regional = sceneryMaterial('regional', coverage);
  try {
    expect(regional.maskNode).toBeTruthy();
    // Returning the generic override would fill masked-out terrain into the
    // depth used for AO and temporal reprojection while beauty shows a hole.
    expect(pre.select(regional)).toBeNull();
  } finally { pre.dispose(); regional.dispose(); coverage.dispose(); }
});
