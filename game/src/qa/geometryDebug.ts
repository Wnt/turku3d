import * as THREE from 'three/webgpu';

type Finding = { kind: string; mesh: string; other_mesh?: string };
const original = new Map<THREE.Mesh, THREE.Material | THREE.Material[]>();
let diagnostic: THREE.MeshBasicNodeMaterial | null = null;

/** Highlight source objects with measured coplanar overlap. No work while disabled. */
export function coplanarDebug(scene: THREE.Scene, report: { findings: Finding[] } | null) {
  for (const [mesh, material] of original) mesh.material = material;
  original.clear(); diagnostic?.dispose(); diagnostic = null;
  if (!report) return { meshes: 0, enabled: false };
  const ids = new Set(report.findings.filter(f => f.kind === 'coplanar_overlap')
    .flatMap(f => [f.mesh, f.other_mesh]).filter(Boolean));
  diagnostic = new THREE.MeshBasicNodeMaterial({ color: 0xff2459, side: THREE.DoubleSide });
  scene.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    const source = mesh.userData.objects as { object_id?: string }[] | undefined;
    if (!ids.has(mesh.userData.object_id) && !source?.some(s => ids.has(s.object_id))) return;
    original.set(mesh, mesh.material); mesh.material = diagnostic!;
  });
  return { meshes: original.size, enabled: true, note: 'Whole material batches are highlighted; inspect source ranges in JSON.' };
}

/** Capture identity and geometry/material revisions, including swaps with unchanged counts. */
export function geometryState(scene: THREE.Scene) {
  const meshes: Record<string, unknown>[] = [];
  scene.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    meshes.push({ uuid: mesh.uuid, id: mesh.userData.object_id ?? mesh.name, visible: mesh.visible,
      geometry: mesh.geometry.uuid, indices: mesh.geometry.index?.count,
      attributeVersions: Object.fromEntries(Object.entries(mesh.geometry.attributes).map(([key, a]) =>
        [key, a instanceof THREE.InterleavedBufferAttribute ? a.data.version : a.version])),
      materials: materials.map(m => ({ uuid: m.uuid, version: m.version, depthWrite: m.depthWrite,
        polygonOffset: m.polygonOffset, factor: m.polygonOffsetFactor, units: m.polygonOffsetUnits })) });
  });
  return meshes;
}
