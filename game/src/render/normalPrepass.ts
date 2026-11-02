import * as THREE from 'three/webgpu';

/** AO needs geometric normals, not every facade's albedo/normal/ORM graph. Keep cutouts,
 * displacement and custom vertex programs on their authored path to retain silhouettes.
 * `userData.vertexProgram` marks materials that move vertices before instancing (walkers' VAT). */
export class NormalPrepass {
  readonly marker = new THREE.MeshBasicNodeMaterial();
  private sides = new Map<THREE.Side, THREE.MeshBasicNodeMaterial>();
  constructor() { this.marker.userData.normalPrepass = true; }
  select(source: THREE.Material): THREE.Material | null {
    const material = source as THREE.MeshStandardNodeMaterial;
    if (!material.depthWrite || material.vertexNode || material.alphaTest > 0 || material.alphaMap || material.displacementMap || material.positionNode
      || material.depthNode || material.fragmentNode || material.alphaToCoverage || material.userData.vertexProgram) return null;
    let normal = this.sides.get(source.side);
    if (!normal) {
      normal = new THREE.MeshBasicNodeMaterial({ side: source.side });
      normal.name = 'normal-velocity-prepass';
      this.sides.set(source.side, normal);
    }
    return normal;
  }
  dispose() { this.marker.dispose(); for (const material of this.sides.values()) material.dispose(); }
}
