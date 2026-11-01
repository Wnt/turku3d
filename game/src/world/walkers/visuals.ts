// GPU side of the walkers: one InstancedMesh per body variant, all sharing one material and one vertex animation
// texture. The vertex stage reads the baked pose (no skinning), so shader pipelines do not depend on walker count.
import * as THREE from 'three/webgpu';
import type Node from 'three/src/nodes/core/Node.js';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import {
  attribute, cos, float, floor, fract, int, ivec2, max, mix, mod, normalize, normalLocal, positionLocal, positionPrevious,
  pow, select, sin, sqrt, textureLoad, varying, vec3,
} from 'three/tsl';
import { GOLDEN_FRACTION, fibonacciDirection, type WalkerAsset, type WalkerVariant } from './asset';

/** Per-vertex: (VAT column, body region). Per instance: clip rows and phases, and packed sRGB tints. */
const WALKER_ATTRIBUTES = { vertex: 'walkerVertex', anim: 'walkerAnim', look: 'walkerLook', trim: 'walkerTrim' } as const;

/** Packs an 8-bit sRGB colour exactly into one float (24 bits fit f32's mantissa). */
export const packColour = (hex: number) => hex & 0xffffff;

/** Physically based but untextured: the walkers are a few hundred pixels at most. */
export class WalkerMaterial extends THREE.MeshStandardNodeMaterial {
  constructor(private readonly vat: THREE.DataTexture, private readonly directions: number) {
    super({ roughness: 0.82, metalness: 0 });
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
              select(region.lessThan(5.5), unpack(trim.y), unpack(trim.z)))))));
    this.colorNode = varying(tint, 'walkerTint');
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
    positionLocal.assign(mix(now.a.xyz, now.b.xyz, now.w).mul(fade));
    normalLocal.assign(normalize(mix(direction(now.a.w), direction(now.b.w), now.w)));
    if ((builder as NodeBuilder & { needsPreviousData(): boolean }).needsPreviousData()) {
      const before = pose(fract(anim.w));
      positionPrevious.assign(mix(before.a.xyz, before.b.xyz, before.w).mul(fade));
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
function walkerGeometry(asset: WalkerAsset, variant: WalkerVariant, capacity: number): THREE.BufferGeometry {
  const geometry = new THREE.BufferGeometry();
  const n = variant.vertices, position = new Float32Array(3 * n), normal = new Float32Array(3 * n), vertex = new Float32Array(2 * n);
  const row = variant.rowBase + variant.idle.row, data = asset.vat.data, width = asset.vat.width;
  for (let i = 0; i < n; i++) {
    const o = 4 * (row * width + i);
    position.set([0, 1, 2].map(k => THREE.DataUtils.fromHalfFloat(data[o + k])), 3 * i);
    normal.set(fibonacciDirection(THREE.DataUtils.fromHalfFloat(data[o + 3]), asset.vat.directions), 3 * i);
    vertex.set([i, variant.regions[i]], 2 * i);
  }
  geometry.setAttribute('position', new THREE.BufferAttribute(position, 3));
  geometry.setAttribute('normal', new THREE.BufferAttribute(normal, 3));
  geometry.setAttribute(WALKER_ATTRIBUTES.vertex, new THREE.BufferAttribute(vertex, 2));
  geometry.setIndex(new THREE.BufferAttribute(variant.indices, 1));
  for (const name of [WALKER_ATTRIBUTES.anim, WALKER_ATTRIBUTES.look, WALKER_ATTRIBUTES.trim]) {
    geometry.setAttribute(name, new THREE.InstancedBufferAttribute(new Float32Array(4 * capacity), 4).setUsage(THREE.DynamicDrawUsage));
  }
  geometry.computeBoundingSphere();
  // Swinging limbs and the walk's stride reach past the idle pose.
  geometry.boundingSphere!.radius += 0.4;
  return geometry;
}

export interface WalkerInstance {
  variant: number;
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
  private readonly texture: THREE.DataTexture;
  private matrix = new THREE.Matrix4();
  private rotation = new THREE.Quaternion();
  private axis = new THREE.Vector3(0, 1, 0);
  private translation = new THREE.Vector3();
  private scale = new THREE.Vector3();

  constructor(readonly asset: WalkerAsset, readonly capacity: number) {
    this.group.name = 'walkers';
    this.texture = vatTexture(asset);
    this.material = new WalkerMaterial(this.texture, asset.vat.directions);
    this.meshes = asset.variants.map(variant => {
      const mesh = new THREE.InstancedMesh(walkerGeometry(asset, variant, capacity), this.material, capacity);
      mesh.name = `walkers-${variant.id}`;
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      mesh.castShadow = false; // low and medium never cast; high has no VAT shadow program yet
      mesh.receiveShadow = true;
      mesh.count = 0; mesh.visible = false;
      this.group.add(mesh);
      return mesh;
    });
  }

  /** Writes this frame's instances; at most `capacity` per variant. */
  update(instances: readonly WalkerInstance[]) {
    const counts = this.meshes.map(() => 0);
    for (const w of instances) {
      const mesh = this.meshes[w.variant], k = counts[w.variant];
      if (!mesh || k >= this.capacity) continue;
      counts[w.variant]++;
      this.rotation.setFromAxisAngle(this.axis, w.yaw);
      mesh.setMatrixAt(k, this.matrix.compose(this.translation.set(w.x, w.y, w.z), this.rotation, this.scale.setScalar(w.scale)));
      const g = mesh.geometry.attributes;
      (g[WALKER_ATTRIBUTES.anim] as THREE.InstancedBufferAttribute).setXYZW(k, w.rowStart, w.frames, w.phase, w.previousPhase);
      (g[WALKER_ATTRIBUTES.look] as THREE.InstancedBufferAttribute).setXYZW(k, w.skin, w.top, w.bottom, w.hair);
      (g[WALKER_ATTRIBUTES.trim] as THREE.InstancedBufferAttribute).setXYZW(k, w.shoes, w.inner, w.legwear, w.fade);
    }
    this.meshes.forEach((mesh, v) => {
      mesh.count = counts[v];
      mesh.visible = counts[v] > 0;
      if (!counts[v]) return;
      mesh.instanceMatrix.needsUpdate = true;
      for (const name of [WALKER_ATTRIBUTES.anim, WALKER_ATTRIBUTES.look, WALKER_ATTRIBUTES.trim]) mesh.geometry.attributes[name].needsUpdate = true;
      mesh.computeBoundingSphere();
    });
  }

  dispose() {
    for (const mesh of this.meshes) { mesh.geometry.dispose(); mesh.dispose(); }
    this.material.dispose(); this.texture.dispose();
    this.group.removeFromParent();
  }
}
