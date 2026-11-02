// GPU side of the walkers: one InstancedMesh per body variant, all sharing one material and one vertex animation
// texture. The vertex stage reads the baked pose (no skinning), so shader pipelines do not depend on walker count.
import * as THREE from 'three/webgpu';
import { WalkerContact } from './contact';
import type Node from 'three/src/nodes/core/Node.js';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import {
  attribute, cos, float, floor, fract, int, ivec2, max, mix, mod, normalize, normalLocal, positionLocal, positionPrevious,
  pow, select, sin, smoothstep, sqrt, texture, textureLoad, varying, vec3,
} from 'three/tsl';
import { GOLDEN_FRACTION, fibonacciDirection, type WalkerAsset, type WalkerVariant } from './asset';

/** Per-vertex: (VAT column, body region). Per instance: clip rows and phases, and packed sRGB tints. */
const WALKER_ATTRIBUTES = { vertex: 'walkerVertex', anim: 'walkerAnim', look: 'walkerLook', trim: 'walkerTrim', shape: 'walkerShape' } as const;

/** Packs an 8-bit sRGB colour exactly into one float (24 bits fit f32's mantissa). */
export const packColour = (hex: number) => hex & 0xffffff;

/** Shared PBR crowd material with the source face, hair and clothing atlas. */
export class WalkerMaterial extends THREE.MeshStandardNodeMaterial {
  constructor(private readonly vat: THREE.DataTexture, private readonly directions: number, albedo?: THREE.Texture) {
    super({ roughness: 0.82, metalness: 0, side: albedo ? THREE.DoubleSide : THREE.FrontSide });
    this.name = 'walker-vat';
    // The VAT pose is set before instancing (setupPosition); NormalPrepass must keep this program for depth/normals.
    this.userData.vertexProgram = true;
    const look = attribute(WALKER_ATTRIBUTES.look, 'vec4'), trim = attribute(WALKER_ATTRIBUTES.trim, 'vec4');
    const region = attribute(WALKER_ATTRIBUTES.vertex, 'vec2').y;
    const unpack = (v: Node<'float'>) => {
      const high = floor(v.div(65536)), mid = floor(v.div(256));
      return pow(vec3(high, mid.sub(high.mul(256)), v.sub(mid.mul(256))).div(255), vec3(2.2));
    };
    const tint = select(region.lessThan(0.5), unpack(look.x),
      select(region.lessThan(1.5), unpack(look.y),
        select(region.lessThan(2.5), unpack(look.z),
          select(region.lessThan(3.5), unpack(trim.x),
            select(region.lessThan(4.5), unpack(look.w),
              select(region.lessThan(5.5), unpack(trim.y), select(region.lessThan(6.5), unpack(trim.z), vec3(1))))))));
    this.colorNode = varying(tint, 'walkerTint');
    if (albedo) {
      const texel = texture(albedo);
      const gain = select(region.lessThan(0.5), float(1.6129), select(region.lessThan(6.5), float(2), float(1)));
      this.colorNode = this.colorNode.mul(texel.rgb).mul(varying(gain));
      this.opacityNode = texel.a;
      this.alphaTest = 0.35;
    }
  }

  override setupPosition(builder: NodeBuilder) {
    const vertex = attribute(WALKER_ATTRIBUTES.vertex, 'vec2'), anim = attribute(WALKER_ATTRIBUTES.anim, 'vec4');
    const fade = attribute(WALKER_ATTRIBUTES.trim, 'vec4').w;
    const column = int(vertex.x);
    const pose = (phase: Node<'float'>) => {
      const f = phase.mul(anim.y), f0 = floor(f), w = f.sub(f0), f1 = mod(f0.add(1), anim.y);
      const a = textureLoad(this.vat, ivec2(column, int(anim.x.add(f0)))), b = textureLoad(this.vat, ivec2(column, int(anim.x.add(f1))));
      return { a, b, w };
    };
    const direction = (index: Node<'float'>) => {
      const z = float(1).sub(index.mul(2).add(1).div(this.directions));
      const r = sqrt(max(float(0), float(1).sub(z.mul(z))));
      const phi = fract(index.mul(GOLDEN_FRACTION)).mul(Math.PI * 2);
      return vec3(cos(phi).mul(r), sin(phi).mul(r), z);
    };
    const now = pose(fract(anim.z));
    const shape = attribute(WALKER_ATTRIBUTES.shape, 'vec4');
    // Ankle/foot dimensions and forward stride remain authored. The torso broadens independently of the head.
    const height = attribute('position', 'vec3').y;
    const torso = smoothstep(0.72, 1.05, height).mul(float(1).sub(smoothstep(1.42, 1.57, height)));
    const head = smoothstep(1.48, 1.62, height);
    const width = float(1).add(torso.mul(shape.x.sub(1))).add(head.mul(shape.z.sub(1)));
    const depth = float(1).add(torso.mul(shape.y.sub(1))).add(head.mul(shape.w.sub(1)));
    const deform = (p: Node<'vec3'>) => p.mul(vec3(width, 1, depth)).mul(fade);
    positionLocal.assign(deform(mix(now.a.xyz, now.b.xyz, now.w)));
    normalLocal.assign(normalize(mix(direction(now.a.w), direction(now.b.w), now.w).div(vec3(width, 1, depth))));
    if ((builder as NodeBuilder & { needsPreviousData(): boolean }).needsPreviousData()) {
      const before = pose(fract(anim.w));
      positionPrevious.assign(deform(mix(before.a.xyz, before.b.xyz, before.w)));
    }
    // Instancing (yaw, height scale, position) follows in NodeMaterial.setupPosition.
    return super.setupPosition(builder);
  }
}

function vatTexture(asset: WalkerAsset): THREE.DataTexture {
  const texture = new THREE.DataTexture(asset.vat.data, asset.vat.width, asset.vat.height, THREE.RGBAFormat, THREE.HalfFloatType);
  texture.name = 'walker-vat';
  texture.minFilter = texture.magFilter = THREE.NearestFilter;
  texture.generateMipmaps = false;
  texture.colorSpace = THREE.NoColorSpace;
  texture.needsUpdate = true;
  return texture;
}

/** Bind pose (first idle frame) for bounds and the attribute layout; the shader replaces it every frame. */
function walkerGeometry(asset: WalkerAsset, variant: WalkerVariant, capacity: number, far: boolean): THREE.BufferGeometry {
  const geometry = new THREE.BufferGeometry();
  const n = variant.vertices, position = new Float32Array(3 * n), normal = new Float32Array(3 * n), vertex = new Float32Array(2 * n);
  const row = variant.rowBase + variant.idle.row, data = asset.vat.data, width = asset.vat.width;
  for (let i = 0; i < n; i++) {
    const o = 4 * (row * width + i);
    position.set([0, 1, 2].map(k => THREE.DataUtils.fromHalfFloat(data[o + k])), 3 * i);
    normal.set(fibonacciDirection(THREE.DataUtils.fromHalfFloat(data[o + 3]), asset.vat.directions), 3 * i);
    vertex.set([i, variant.regions[i]], 2 * i);
  }
  // WebGPU guarantees only eight vertex-buffer bindings, including current/previous instance matrices.
  // Interleave the static fields and the per-instance fields so the textured velocity pass uses four.
  const staticData = new THREE.InterleavedBuffer(new Float32Array(n * 10), 10);
  for (let i = 0; i < n; i++) {
    staticData.array.set(position.subarray(i * 3, i * 3 + 3), i * 10);
    staticData.array.set(normal.subarray(i * 3, i * 3 + 3), i * 10 + 3);
    staticData.array.set(variant.uv.subarray(i * 2, i * 2 + 2), i * 10 + 6);
    staticData.array.set(vertex.subarray(i * 2, i * 2 + 2), i * 10 + 8);
  }
  geometry.setAttribute('position', new THREE.InterleavedBufferAttribute(staticData, 3, 0));
  geometry.setAttribute('normal', new THREE.InterleavedBufferAttribute(staticData, 3, 3));
  geometry.setAttribute('uv', new THREE.InterleavedBufferAttribute(staticData, 2, 6));
  geometry.setAttribute(WALKER_ATTRIBUTES.vertex, new THREE.InterleavedBufferAttribute(staticData, 2, 8));
  geometry.setIndex(new THREE.BufferAttribute(far ? variant.farIndices : variant.indices, 1));
  const instanceData = new THREE.InstancedInterleavedBuffer(new Float32Array(16 * capacity), 16).setUsage(THREE.DynamicDrawUsage);
  [WALKER_ATTRIBUTES.anim, WALKER_ATTRIBUTES.look, WALKER_ATTRIBUTES.trim, WALKER_ATTRIBUTES.shape].forEach((name, i) => {
    geometry.setAttribute(name, new THREE.InterleavedBufferAttribute(instanceData, 4, i * 4));
  });
  geometry.computeBoundingSphere();
  // Swinging limbs and the walk's stride reach past the idle pose.
  geometry.boundingSphere!.radius += 0.4;
  return geometry;
}

export interface WalkerInstance {
  variant: number;
  lod?: 0 | 1;
  shape?: readonly [number, number, number, number];
  x: number; y: number; z: number; yaw: number; scale: number;
  /** VAT rows of the clip, and its phase now and one frame ago (velocity for temporal AA). */
  rowStart: number; frames: number; phase: number; previousPhase: number;
  /** Packed sRGB tints. */
  skin: number; top: number; bottom: number; hair: number; shoes: number; inner: number; legwear: number;
  fade: number;
}

export class WalkerMeshes {
  readonly group = new THREE.Group();
  readonly meshes: THREE.InstancedMesh[];
  readonly material: WalkerMaterial;
  readonly contact: WalkerContact;
  private readonly texture: THREE.DataTexture;
  private matrix = new THREE.Matrix4();
  private rotation = new THREE.Quaternion();
  private axis = new THREE.Vector3(0, 1, 0);
  private translation = new THREE.Vector3();
  private scale = new THREE.Vector3();

  constructor(readonly asset: WalkerAsset, readonly capacity: number) {
    this.group.name = 'walkers';
    this.contact = new WalkerContact(capacity);
    this.group.add(this.contact.mesh);
    this.texture = vatTexture(asset);
    this.material = new WalkerMaterial(this.texture, asset.vat.directions, asset.albedo);
    this.meshes = asset.variants.flatMap(variant => [false, true].map(far => {
      const mesh = new THREE.InstancedMesh(walkerGeometry(asset, variant, capacity, far), this.material, capacity);
      mesh.name = `walkers-${variant.id}-${far ? "far" : "near"}`;
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      mesh.castShadow = false; // low and medium never cast; high has no VAT shadow program yet
      mesh.receiveShadow = true;
      mesh.count = 0; mesh.visible = false;
      this.group.add(mesh);
      return mesh;
    }));
  }

  /** Writes this frame's instances; at most `capacity` per variant. */
  update(instances: readonly WalkerInstance[]) {
    this.contact.update(instances);
    const counts = this.meshes.map(() => 0);
    for (const w of instances) {
      const variant = w.variant * 2 + (w.lod ?? 0);
      const mesh = this.meshes[variant], k = counts[variant];
      if (!mesh || k >= this.capacity) continue;
      counts[variant]++;
      this.rotation.setFromAxisAngle(this.axis, w.yaw);
      mesh.setMatrixAt(k, this.matrix.compose(this.translation.set(w.x, w.y, w.z), this.rotation, this.scale.setScalar(w.scale)));
      const g = mesh.geometry.attributes;
      (g[WALKER_ATTRIBUTES.anim] as THREE.InterleavedBufferAttribute).setXYZW(k, w.rowStart, w.frames, w.phase, w.previousPhase);
      (g[WALKER_ATTRIBUTES.look] as THREE.InterleavedBufferAttribute).setXYZW(k, w.skin, w.top, w.bottom, w.hair);
      (g[WALKER_ATTRIBUTES.trim] as THREE.InterleavedBufferAttribute).setXYZW(k, w.shoes, w.inner, w.legwear, w.fade);
      (g[WALKER_ATTRIBUTES.shape] as THREE.InterleavedBufferAttribute).setXYZW(k, ...(w.shape ?? [1, 1, 1, 1]));
    }
    this.meshes.forEach((mesh, v) => {
      mesh.count = counts[v];
      mesh.visible = counts[v] > 0;
      if (!counts[v]) return;
      mesh.instanceMatrix.needsUpdate = true;
      for (const name of [WALKER_ATTRIBUTES.anim, WALKER_ATTRIBUTES.look, WALKER_ATTRIBUTES.trim, WALKER_ATTRIBUTES.shape]) mesh.geometry.attributes[name].needsUpdate = true;
      mesh.computeBoundingSphere();
    });
  }

  dispose() {
    this.contact.dispose();
    for (const mesh of this.meshes) { mesh.geometry.dispose(); mesh.dispose(); }
    this.material.dispose(); this.texture.dispose(); this.asset.albedo?.dispose();
    this.group.removeFromParent();
  }
}
