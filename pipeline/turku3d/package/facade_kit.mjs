// Shared fallback atlas. Unique generated/derived/fast walls are selected before this binder.
import { createHash } from 'node:crypto';

export function bindFacadeKit(doc, request, material, prim, e, texture) {
  if (!['buildings', 'buildings_outer'].includes(e.layer) ||
      !(e.material_key === 'facade_kit' || e.material_key?.startsWith('facade_'))) return false;
  const uv = prim.getAttribute('TEXCOORD_0');
  const pos = prim.getAttribute('POSITION');
  if (!uv || !pos) return false;
  let lo = Infinity, hi = -Infinity, u0 = Infinity, u1 = -Infinity, v0 = Infinity;
  for (let i = 0; i < pos.getCount(); i++) {
    lo = Math.min(lo, pos.getY(i)); hi = Math.max(hi, pos.getY(i));
    u0 = Math.min(u0, uv.getX(i)); u1 = Math.max(u1, uv.getX(i)); v0 = Math.min(v0, uv.getY(i));
  }
  const height = Math.max(hi - lo, 1), width = Math.max(u1 - u0, 1);
  const id = String(e.building_id ?? e.object_id);
  const hash = createHash('sha256').update(id).digest().readUInt32BE(0);
  const cls = { facade_brick:1, facade_wood:3, facade_concrete:4 }[e.material_key] ?? 0;
  const p = e.facade_kit ?? request.facadeKit.params?.[id] ?? {
    module: cls * 4 + (width >= 6 ? 1 : 0),
    wall: ['#d4d1c9', '#b8b5ae', '#cfc8b8', '#d8bf7a', '#c99a86'][hash % 5], trim:'#e4ddd0',
    bay: width / Math.max(1, Math.round(width / 3)), storey: height / Math.max(1, Math.round(height / 3.2)),
  };
  // Clone: adjacent walls can share source accessors before batching.
  const mapped = uv.clone();
  for (let i = 0; i < uv.getCount(); i++) mapped.setElement(i, [(uv.getX(i) - u0) / p.bay, (uv.getY(i) - v0) / p.storey]);
  prim.setAttribute('TEXCOORD_0', mapped);
  material.setName(`facade-kit:${JSON.stringify(p)}`).setExtras({ material_key:'facade_kit', facade_kit:p })
    .setBaseColorFactor([1, 1, 1, 1]).setMetallicFactor(1).setRoughnessFactor(1);
  const maps = request.facadeKit.maps;
  material.setBaseColorTexture(texture(maps.albedo, true, 'albedo'));
  material.setNormalTexture(texture(maps.normal, true, 'normal'));
  const orm = texture(maps.orm, true, 'orm');
  material.setMetallicRoughnessTexture(orm).setOcclusionTexture(orm);
  return true;
}
