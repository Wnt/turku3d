// The building generator's material families (pipeline/turku3d/world/kinds/building): procedural PBR at true
// metric scale from the parameters a compiled building names, see ../buildingMaterials.ts.
import { registerFamily } from '../materials';
import { BUILDING_FAMILIES, buildingMaterial, type BuildingMaterialParams } from '../buildingMaterials';

// For buildings, quay walls, bridges and stair flights (metre UVs along the face and up) only: street objects use
// some of the same names (metal, stone) with their own parameters, and ground's granite is paving.
for (const kind of ['building', 'wall', 'bridge', 'steps']) {
  for (const family of BUILDING_FAMILIES) registerFamily(family, params => buildingMaterial(family, params as BuildingMaterialParams), kind);
}
