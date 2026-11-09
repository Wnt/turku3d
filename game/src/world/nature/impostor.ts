// Hemi-octahedral tree impostors (scripts/nature/octahedral.mjs bakes them): each tree's impostor LOD is one quad
// that faces the camera in the tree's own space. The fragment blends the three baked views nearest the view
// direction; each view is reprojected onto the pixel's view ray with its baked depth, so neighbouring views agree on
// where a leaf is and the crown turns with the camera instead of popping between views.
//
// Everything runs in asset space (the model at scale 1): the instance matrix (rotation, scale, the lidar crown's
// shear and stretch) is affine, so view rays stay straight there and the baked orthographic views apply unchanged.
import * as THREE from 'three/webgpu';
import {
  abs, attribute, cameraPosition, cameraViewMatrix, clamp, cross, dFdx, dFdy, dot, float, floor, Fn, fwidth, instancedBufferAttribute, inverse, length, log2, mat3,
  max, min, mod, modelNormalMatrix, modelWorldMatrixInverse, normalize, OnObjectUpdate, positionLocal, positionPrevious, select, step, texture,
  transpose, uv, varyingProperty, vec2, vec3, vec4,
} from 'three/tsl';

/** Mirrors of scripts/nature/octahedral.mjs (FRAMES, MARGIN, BLOCKS); tests/unit/tree-impostor.test.ts keeps them equal. */
export const IMPOSTOR_FRAMES = 8;
export const IMPOSTOR_MARGIN = 1.08;
export const IMPOSTOR_BLOCKS = 4;

type Vec2 = THREE.Node<'vec2'>; type Vec3 = THREE.Node<'vec3'>;

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

/** The linear part of the draw's instance matrix, as attributes over its own array: a shared program
 *  (render/instancePrograms.ts) rebinds them to each draw's matrices like three's own instancing attributes. Its
 *  translation follows from the instanced position (instanceTransform), which saves a vertex attribute: a vertex stage
 *  takes 16, and an unshared velocity pass already binds 13 (quad, instance tint, matrices and previous matrices). */
const ownMatrices = new WeakMap<THREE.InstancedBufferAttribute, THREE.InstancedInterleavedBuffer>();
function instanceLinear(object: THREE.Object3D | null) {
  const mesh = object as THREE.InstancedMesh | null;
  const source = mesh?.isInstancedMesh ? mesh.instanceMatrix as THREE.InstancedBufferAttribute & { isStorageInstancedBufferAttribute?: boolean } : null;
  if (!source || source.isStorageInstancedBufferAttribute) return mat3(vec3(1, 0, 0), vec3(0, 1, 0), vec3(0, 0, 1));
  let buffer = ownMatrices.get(source);
  if (!buffer || buffer.array !== source.array) {
    buffer = new THREE.InstancedInterleavedBuffer(source.array, 16, 1);
    buffer.setUsage(source.usage); buffer.version = -1;
    ownMatrices.set(source, buffer);
  }
  const own = buffer;
  // Unshared draws bind these: upload when the matrices change (shared draws follow their own copy).
  OnObjectUpdate(() => { if (own.version !== source.version) { own.clearUpdateRanges(); own.version = source.version; } });
  const columns = [0, 4, 8].map(offset => instancedBufferAttribute(own, 'vec4', 16, offset) as THREE.Node<'vec4'>);
  return mat3(columns[0].xyz, columns[1].xyz, columns[2].xyz);
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
  // Four varyings (a fragment stage takes 16 in all, three's own included): the quad point and the camera in asset
  // space with the cylinder, the normal transform's horizontal block, and the slot with its vertical scale. The views
  // and their weights follow from the camera, per fragment.
  const vPoint = varyingProperty('vec4', 'impostorPoint'), vCamera = varyingProperty('vec4', 'impostorCamera');
  const vNormalXZ = varyingProperty('vec4', 'impostorNormalXZ'), vSlot = varyingProperty('vec2', 'impostorSlot');

  const position = Fn(builder => {
    const m = instanceLinear((builder as unknown as { object: THREE.Object3D | null }).object);
    // positionLocal is already instanced here: the instance's origin is what its matrix adds to the quad's corner.
    const t = positionLocal.sub(m.mul(attribute('position', 'vec3'))).toVar();
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
    vPoint.assign(vec4(asset, R)); vCamera.assign(vec4(camera, top));
    // Normals turn by the inverse transpose; its shear terms (the lidar crown's lean) are left out.
    const normalMatrix = transpose(toAsset) as THREE.Node<'mat3'> & { element(column: number): Vec3 };
    const nx = normalMatrix.element(0), ny = normalMatrix.element(1), nz = normalMatrix.element(2);
    vNormalXZ.assign(vec4(nx.x, nx.z, nz.x, nz.z)); vSlot.assign(vec2(info.z, ny.y));
    const local = m.mul(asset).add(t);
    const mrt = (builder as unknown as { renderer: { getMRT(): { has(name: string): boolean } | null } }).renderer.getMRT();
    if (mrt?.has('velocity')) positionPrevious.assign(local);
    return local;
  })();

  // Fragment: the three views around the view direction on the grid, with barycentric weights squared (one view leads
  // over most of each triangle, so fewer pixels mix views that saw different gaps between leaves). Each view is sampled
  // where the pixel's ray meets the leaf it saw; colours and normals blend by weight and coverage, coverage by weight.
  // (Dithering one view per pixel instead left stipple TRAA could not resolve where a view saw sky and another a leaf.)
  const R = vPoint.w, top = vCamera.w, slot = vSlot.x, camera = vCamera.xyz;
  const ray = normalize(vPoint.xyz.sub(camera)).toVar();
  const centre = vec3(0, top.mul(0.5), 0);
  const g = hemiOctEncode(normalize(camera.sub(centre))).mul(F - 1).toVar(), cell = min(floor(g), vec2(F - 2)).toVar(), f = g.sub(cell).toVar();
  const upper = f.x.add(f.y).greaterThan(1);
  const barycentric = select(upper, vec3(f.x.add(f.y).sub(1), float(1).sub(f.y), float(1).sub(f.x)), vec3(float(1).sub(f.x).sub(f.y), f.x, f.y));
  const sharp = barycentric.mul(barycentric), weights = sharp.div(dot(sharp, vec3(1))).toVar();
  const first = select(upper, cell.add(1), cell).toVar();
  const block = vec2(mod(slot, IMPOSTOR_BLOCKS), floor(slot.div(IMPOSTOR_BLOCKS))).mul(F);
  /** A view's axes and its image's extent: width, height and depth (m) of the tree's bounding cylinder seen along it. */
  const basis = (cellAt: Vec2) => {
    const d = hemiOctDecode(cellAt.div(F - 1)).toVar(), right = rightOf(d).toVar(), up = cross(d, right).toVar(), flat = length(d.xz);
    return { d, right, up, size: vec3(R.mul(2 * MARGIN), top.mul(flat).add(R.mul(2).mul(d.y)).mul(MARGIN), top.mul(d.y).add(R.mul(2).mul(flat)).mul(MARGIN)) };
  };
  const onPlane = (view: ReturnType<typeof basis>) => camera.add(ray.mul(dot(centre.sub(camera), view.d).div(min(dot(ray, view.d), -1e-3))));
  const imageAt = (view: ReturnType<typeof basis>, p: Vec3) =>
    vec2(dot(p.sub(centre), view.right).div(view.size.x).add(0.5), float(0.5).sub(dot(p.sub(centre), view.up).div(view.size.y)));
  // Texture gradients from the first view's image coordinates, continuous over the quad (each view's own are nearly the
  // same; one pair serves all nine reads).
  const reference = imageAt(basis(first), onPlane(basis(first))).toVar();
  const dx = dFdx(reference).div(vec2(columns, rows)), dy = dFdy(reference).div(vec2(columns, rows));
  const sample = (frame: Vec2, weight: THREE.Node<'float'>) => {
    const view = basis(frame), along = min(dot(ray, view.d), -1e-3), hit = onPlane(view);
    const atlas = (at: Vec2) => block.add(frame).add(clamp(at, 0, 1)).div(vec2(columns, rows));
    const z = texture(normalDepth, atlas(imageAt(view, hit))).grad(dx, dy).a;
    const at = imageAt(view, hit.add(ray.mul(z.sub(0.5).mul(view.size.z).div(along)))).toVar();
    const inside = step(0, at.x).mul(step(at.x, 1)).mul(step(0, at.y)).mul(step(at.y, 1));
    const colour = texture(albedo, atlas(at)).grad(dx, dy), nd = texture(normalDepth, atlas(at)).grad(dx, dy).xyz.mul(2).sub(1);
    const a = colour.a.mul(inside).mul(weight).toVar();
    return { rgb: colour.rgb.mul(a), a, normal: view.right.mul(nd.x).add(view.up.mul(nd.y)).add(view.d.mul(nd.z)).mul(a) };
  };
  const views = [sample(first, weights.x), sample(cell.add(vec2(1, 0)), weights.y), sample(cell.add(vec2(0, 1)), weights.z)];
  const alpha = views[0].a.add(views[1].a).add(views[2].a).toVar();
  const rgb = views[0].rgb.add(views[1].rgb).add(views[2].rgb).div(max(alpha, 1e-4));
  const assetNormal = views[0].normal.add(views[1].normal).add(views[2].normal);
  const local = vec3(vNormalXZ.x.mul(assetNormal.x).add(vNormalXZ.z.mul(assetNormal.z)), vSlot.y.mul(assetNormal.y),
    vNormalXZ.y.mul(assetNormal.x).add(vNormalXZ.w.mul(assetNormal.z)));
  const normal = normalize(cameraViewMatrix.mul(vec4(modelNormalMatrix.mul(local), 0)).xyz);
  const level = log2(max(max(fwidth(reference.x), fwidth(reference.y)).mul(texels / columns), 1));
  return { position, color: vec4(rgb, alpha), normal, level };
}
