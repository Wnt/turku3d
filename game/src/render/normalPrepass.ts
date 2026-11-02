import * as THREE from 'three/webgpu';

/** AO and puddle rays need geometric normals; normal alpha separately marks wet mineral receivers.
 * AO needs geometric normals, not every facade's albedo/normal/ORM graph. Keep cutouts,
 * displacement and custom vertex programs on their authored path to retain silhouettes.
 * `userData.vertexProgram` marks materials that move vertices before instancing (walkers' VAT). */
export class NormalPrepass {
  readonly marker = new THREE.MeshBasicNodeMaterial();
  private sides = new Map<string, THREE.MeshBasicNodeMaterial>();
  constructor() { this.marker.userData.normalPrepass = true; }
  select(source: THREE.Material): THREE.Material | null {
    const material = source as THREE.MeshStandardNodeMaterial;
    const receiver = source.userData.weatherWetness ? 1 : 0;
    material.userData.weatherReceiver = receiver;
    if (!material.depthWrite || material.vertexNode || material.alphaTest > 0 || material.alphaMap || material.displacementMap || material.positionNode
      || material.depthNode || material.fragmentNode || material.alphaToCoverage || material.userData.vertexProgram) return null;
    const key = `${source.side}:${receiver}`;
    let normal = this.sides.get(key);
    if (!normal) {
      normal = new THREE.MeshBasicNodeMaterial({ side: source.side });
      normal.name = 'normal-velocity-prepass';
      normal.userData.weatherReceiver = receiver;
      this.sides.set(key, normal);
    }
    return normal;
  }
  dispose() { this.marker.dispose(); for (const material of this.sides.values()) material.dispose(); }
}
