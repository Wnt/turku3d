// Hemi-octahedral tree impostors (scripts/nature/octahedral.mjs bakes them): each tree's impostor LOD is one quad
// that faces the camera in the tree's own space. The fragment blends the three baked views nearest the view
// direction; each view is reprojected onto the fragment's view ray with its baked depth, so neighbouring views agree
// on where a leaf is and the crown turns with the camera instead of popping between views.
//
// Everything runs in asset space (the model at scale 1): the instance matrix (rotation, scale, the lidar crown's
// shear and stretch) is affine, so view rays stay straight there and the baked orthographic views apply unchanged.
import * as THREE from 'three/webgpu';
import {
  abs, attribute, cameraPosition, cameraViewMatrix, clamp, cross, dot, float, floor, Fn, fwidth, instancedBufferAttribute, inverse, length, log2, mat3,
  max, min, mod, modelNormalMatrix, modelWorldMatrixInverse, normalize, OnObjectUpdate, positionPrevious, select, step, texture,
  transpose, uv, varyingProperty, vec2, vec3, vec4,
} from 'three/tsl';

/** Mirrors of scripts/nature/octahedral.mjs (FRAMES, MARGIN, BLOCKS); tests/unit/tree-impostor.test.ts keeps them equal. */
export const IMPOSTOR_FRAMES = 8;
export const IMPOSTOR_MARGIN = 1.08;
export const IMPOSTOR_BLOCKS = 4;

type Vec2 = THREE.Node<'vec2'>; type Vec3 = THREE.Node<'vec3'>; type Float = THREE.Node<'float'>;

/** The upper hemisphere's direction of hemi-octahedral coordinates in [0, 1]^2 (octahedral.mjs hemiOctDecode). */
const hemiOctDecode = (g: Vec2) => {
  const t = g.mul(2).sub(1);
  const x = t.x.add(t.y).mul(0.5), z = t.x.sub(t.y).mul(0.5);
  return normalize(vec3(x, float(1).sub(abs(x)).sub(abs(z)), z));
};
/** Hemi-octahedral coordinates of a direction; below the horizon it takes the horizon's (octahedral.mjs hemiOctEncode). */
const hemiOctEncode = (d: Vec3) => {
  const p = d.xz.div(abs(d.x).add(max(d.y, 0)).add(abs(d.z)));
  return vec2(p.x.add(p.y), p.x.sub(p.y)).mul(0.5).add(0.5);
};
/** A view's horizontal right axis (octahedral.mjs viewFrame); straight down it takes +X. */
const rightOf = (d: Vec3) => {
  const flat = length(d.xz);
  return select(flat.greaterThan(1e-4), vec3(d.z, 0, d.x.negate()).div(max(flat, 1e-4)), vec3(1, 0, 0));
};

/** The draw's instance matrix as attributes over its own array: a shared program (render/instancePrograms.ts)
 *  rebinds them to each draw's matrices like three's own instancing attributes. */
const ownMatrices = new WeakMap<THREE.InstancedBufferAttribute, THREE.InstancedInterleavedBuffer>();
function instanceTransform(object: THREE.Object3D | null) {
  const mesh = object as THREE.InstancedMesh | null;
  const source = mesh?.isInstancedMesh ? mesh.instanceMatrix as THREE.InstancedBufferAttribute & { isStorageInstancedBufferAttribute?: boolean } : null;
  if (!source || source.isStorageInstancedBufferAttribute) return { m: mat3(vec3(1, 0, 0), vec3(0, 1, 0), vec3(0, 0, 1)), t: vec3(0) };
  let buffer = ownMatrices.get(source);
  if (!buffer || buffer.array !== source.array) {
    buffer = new THREE.InstancedInterleavedBuffer(source.array, 16, 1);
    buffer.setUsage(source.usage); buffer.version = -1;
    ownMatrices.set(source, buffer);
  }
  const own = buffer;
  // Unshared draws bind these: upload when the matrices change (shared draws follow their own copy).
  OnObjectUpdate(() => { if (own.version !== source.version) { own.clearUpdateRanges(); own.version = source.version; } });
  const columns = [0, 4, 8, 12].map(offset => instancedBufferAttribute(own, 'vec4', 16, offset) as THREE.Node<'vec4'>);
  return { m: mat3(columns[0].xyz, columns[1].xyz, columns[2].xyz), t: columns[3].xyz };
}

export interface ImpostorNodes {
  /** Mesh-local position of the camera-facing quad corner (also the previous frame's: trees never move). */
  position: THREE.Node<'vec3'>;
  /** Blended albedo (rgb) and coverage (a), before toning. */
  color: THREE.Node<'vec4'>;
  /** View-space normal of the blended views. */
  normal: THREE.Node<'vec3'>;
  /** Atlas texel footprint for mip-dependent alpha (log2 of texels per pixel). */
  level: THREE.Node<'float'>;
}

/**
 * Nodes of the impostor material. `albedo`: rgb + coverage per view; `normalDepth`: the view-frame normal in rgb and
 * the depth towards the viewer in a (octahedral.mjs bakeTree). The quads carry `_impostor` (R, top, atlas slot).
 */
export function impostorNodes(albedo: THREE.Texture, normalDepth: THREE.Texture): ImpostorNodes {
  const F = IMPOSTOR_FRAMES, MARGIN = IMPOSTOR_MARGIN;
  const size = (albedo.image as { width?: number; height?: number } | null) ?? {};
  const columns = IMPOSTOR_BLOCKS * F, rows = columns * (size.height ?? 1) / (size.width ?? 1), texels = size.width ?? 4096;
  const vAsset = varyingProperty('vec3', 'impostorAsset'), vCamera = varyingProperty('vec3', 'impostorCamera');
  const vInfo = varyingProperty('vec3', 'impostorInfo'), vWeights = varyingProperty('vec3', 'impostorWeights');
  const vFrames = varyingProperty('vec4', 'impostorFrames'), vFrame2 = varyingProperty('vec2', 'impostorFrame2');
  const vNormal = [0, 1, 2].map(k => varyingProperty('vec3', `impostorNormal${k}`));

  const position = Fn(builder => {
    const { m, t } = instanceTransform((builder as unknown as { object: THREE.Object3D | null }).object);
    const info = attribute('_impostor', 'vec4');
    const R = info.x, top = info.y;
    const toAsset = inverse(m).toVar();
    const camera = toAsset.mul(modelWorldMatrixInverse.mul(vec4(cameraPosition, 1)).xyz.sub(t)).toVar();
    const centre = vec3(0, top.mul(0.5), 0);
    const toCamera = camera.sub(centre).toVar(), distance = length(toCamera).toVar();
    const d = toCamera.div(distance).toVar();
    const right = rightOf(d).toVar(), up = cross(d, right);
    // The quad frames the tree's bounding cylinder as seen along d, widened for perspective: its near side, up to the
    // bounding sphere's radius closer, looks larger than the orthographic views.
    const cover = distance.div(max(distance.sub(length(vec2(R, top.mul(0.5)))), distance.mul(0.25))).mul(MARGIN);
    const flat = length(d.xz);
    const halfWidth = R.mul(cover), halfHeight = top.mul(flat).add(R.mul(2).mul(abs(d.y))).mul(cover).mul(0.5);
    const corner = vec2(uv().x.mul(2).sub(1), float(1).sub(uv().y.mul(2)));
    const asset = centre.add(right.mul(corner.x.mul(halfWidth))).add(up.mul(corner.y.mul(halfHeight)));
    // The three views around d on the grid, with barycentric weights.
    const g = hemiOctEncode(d).mul(F - 1), cell = min(floor(g), vec2(F - 2)), f = g.sub(cell);
    const upper = f.x.add(f.y).greaterThan(1);
    vFrames.assign(vec4(select(upper, cell.add(1), cell), cell.add(vec2(1, 0))));
    vFrame2.assign(cell.add(vec2(0, 1)));
    vWeights.assign(select(upper, vec3(f.x.add(f.y).sub(1), float(1).sub(f.y), float(1).sub(f.x)), vec3(float(1).sub(f.x).sub(f.y), f.x, f.y)));
    vAsset.assign(asset); vCamera.assign(camera); vInfo.assign(info.xyz);
    const normalMatrix = transpose(toAsset) as THREE.Node<'mat3'> & { element(column: number): Vec3 };
    vNormal.forEach((v, k) => v.assign(normalMatrix.element(k)));
    const local = m.mul(asset).add(t);
    const mrt = (builder as unknown as { renderer: { getMRT(): { has(name: string): boolean } | null } }).renderer.getMRT();
    if (mrt?.has('velocity')) positionPrevious.assign(local);
    return local;
  })();

  // Fragment: each view's sample where the view ray meets the leaf the view saw.
  const ray = normalize(vAsset.sub(vCamera)).toVar();
  const R = vInfo.x, top = vInfo.y, slot = vInfo.z;
  const centre = vec3(0, top.mul(0.5), 0);
  const block = vec2(mod(slot, IMPOSTOR_BLOCKS), floor(slot.div(IMPOSTOR_BLOCKS))).mul(F);
  const atlas = (frame: Vec2, at: Vec2) => block.add(frame).add(clamp(at, 0, 1)).div(vec2(columns, rows));
  const view = (frame: Vec2, weight: Float) => {
    const d = hemiOctDecode(frame.div(F - 1)).toVar(), right = rightOf(d).toVar(), up = cross(d, right).toVar();
    const flat = length(d.xz);
    const width = R.mul(2 * MARGIN), height = top.mul(flat).add(R.mul(2).mul(d.y)).mul(MARGIN);
    const depth = top.mul(d.y).add(R.mul(2).mul(flat)).mul(MARGIN);
    const along = min(dot(ray, d), -1e-3);
    const onPlane = vCamera.add(ray.mul(dot(centre.sub(vCamera), d).div(along)));
    const frameUv = (p: Vec3) => vec2(dot(p.sub(centre), right).div(width).add(0.5), float(0.5).sub(dot(p.sub(centre), up).div(height)));
    const z = texture(normalDepth, atlas(frame, frameUv(onPlane))).a;
    const at = frameUv(onPlane.add(ray.mul(z.sub(0.5).mul(depth).div(along)))).toVar();
    const inside = step(0, at.x).mul(step(at.x, 1)).mul(step(0, at.y)).mul(step(at.y, 1));
    const colour = texture(albedo, atlas(frame, at)), nd = texture(normalDepth, atlas(frame, at)).xyz.mul(2).sub(1);
    const a = colour.a.mul(inside).mul(weight);
    return { rgb: colour.rgb.mul(a), a, normal: right.mul(nd.x).add(up.mul(nd.y)).add(d.mul(nd.z)).mul(a), at: atlas(frame, at) };
  };
  const views = [view(vFrames.xy, vWeights.x), view(vFrames.zw, vWeights.y), view(vFrame2, vWeights.z)];
  const alpha = views[0].a.add(views[1].a).add(views[2].a).toVar();
  const rgb = views[0].rgb.add(views[1].rgb).add(views[2].rgb).div(max(alpha, 1e-4));
  const assetNormal = views[0].normal.add(views[1].normal).add(views[2].normal);
  const local = mat3(vNormal[0], vNormal[1], vNormal[2]).mul(assetNormal);
  const normal = normalize(cameraViewMatrix.mul(vec4(modelNormalMatrix.mul(local), 0)).xyz);
  const footprint = views[0].at.mul(texels);
  const level = log2(max(max(fwidth(footprint.x), fwidth(footprint.y)), 1));
  return { position, color: vec4(rgb, alpha), normal, level };
}
