// Opaque draw order that groups state changes (A/B alternative, ?drawSort=material; owner perf3, the losing path goes
// after the Windows and Mac A/B). r186 sorts opaque draws strictly front to back (painterSortStable), so neighbouring
// draws rarely share a material: almost every draw switches pipeline and material bind group. On D3D12 through Dawn
// that per-draw state work is about 2.6x the Mac's (winperf: toArray, updateBinding and _draw about 3 ms a frame).
// Within each `bucketM` depth band, draws are grouped by material, then geometry. The bands keep the order coarsely
// front to back for early depth rejection.

interface Item { groupOrder: number; renderOrder: number; z: number; id: number; material: { id: number }; geometry: { id: number } }

/** Clip-space depth bands: about metres for perspective cameras. An orthographic shadow camera's whole depth range
 *  is under one band, so its casters group by material alone (depth-only draws need no front-to-back order). */
export const DRAW_SORT_BAND = 25;

export function materialBandSort(bucketM = DRAW_SORT_BAND) {
  return (a: Item, b: Item) => a.groupOrder - b.groupOrder || a.renderOrder - b.renderOrder
    || Math.floor(a.z / bucketM) - Math.floor(b.z / bucketM) || a.material.id - b.material.id
    || a.geometry.id - b.geometry.id || a.z - b.z || a.id - b.id;
}
