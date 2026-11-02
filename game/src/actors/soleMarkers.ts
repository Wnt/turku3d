import * as THREE from 'three/webgpu';

export interface SoleMarker { mesh: THREE.SkinnedMesh; vertex: number }

/** Four heel/toe corners per foot, selected from the actual bind-pose sole.
 * Sampling these skin vertices retains toe-bone articulation and avoids assuming
 * the ankle is at a constant height while a shoe rolls from heel to toe. */
export function soleMarkers(model: THREE.Object3D, ankles: readonly THREE.Object3D[]): SoleMarker[][] {
  model.updateWorldMatrix(true, true);
  const feet = ankles.map(ankle => ankle.getWorldPosition(new THREE.Vector3()));
  const meshes: THREE.SkinnedMesh[] = [];
  model.traverse(o => { if ((o as THREE.SkinnedMesh).isSkinnedMesh) meshes.push(o as THREE.SkinnedMesh); });
  const shoes = meshes.filter(mesh => (Array.isArray(mesh.material) ? mesh.material : [mesh.material]).some(m => /shoes|boots/i.test(m.name)));
  const candidates = ankles.map(() => [] as { mesh: THREE.SkinnedMesh; vertex: number; p: THREE.Vector3 }[]);
  const p = new THREE.Vector3();
  for (const mesh of shoes.length ? shoes : meshes) {
    for (let vertex = 0; vertex < mesh.geometry.attributes.position.count; vertex++) {
      mesh.getVertexPosition(vertex, p).applyMatrix4(mesh.matrixWorld);
      let nearest = 0;
      for (let i = 1; i < feet.length; i++) if (p.distanceToSquared(feet[i]) < p.distanceToSquared(feet[nearest])) nearest = i;
      if (p.y < feet[nearest].y + 0.02) candidates[nearest].push({ mesh, vertex, p: p.clone() });
    }
  }
  return candidates.map(points => {
    if (!points.length) return [];
    const low = Math.min(...points.map(p => p.p.y));
    const sole = points.filter(p => p.p.y < low + 0.018);
    const minX = Math.min(...sole.map(p => p.p.x)), maxX = Math.max(...sole.map(p => p.p.x));
    const minZ = Math.min(...sole.map(p => p.p.z)), maxZ = Math.max(...sole.map(p => p.p.z));
    const selected = new Set<typeof sole[number]>();
    for (const x of [minX, maxX]) for (const z of [minZ, maxZ]) {
      let best = sole[0], distance = Infinity;
      for (const point of sole) {
        const d = (point.p.x - x) ** 2 + (point.p.z - z) ** 2;
        if (d < distance) { best = point; distance = d; }
      }
      selected.add(best);
    }
    return [...selected].map(({ mesh, vertex }) => ({ mesh, vertex }));
  });
}
