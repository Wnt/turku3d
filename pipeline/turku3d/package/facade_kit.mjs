// Shared fallback atlas. Unique generated/derived/fast walls are selected before this binder.
import { createHash } from 'node:crypto';

export function bindFacadeKit(doc, request, material, prim, e, texture) {
  if (!['buildings', 'buildings_outer'].includes(e.layer) ||
      !(e.material_key === 'facade_kit' || e.material_key?.startsWith('facade_'))) return false;
  const uv = prim.getAttribute('TEXCOORD_0');
  const pos = prim.getAttribute('POSITION');
  if (!uv || !pos || e.surface === 'roof' || (e.layer === 'buildings_outer' && !e.surface)) return false;
  let lo = Infinity, hi = -Infinity, u0 = Infinity, u1 = -Infinity, v0 = Infinity;
  for (let i = 0; i < pos.getCount(); i++) {
    const point = pos.getElement(i, []), texcoord = uv.getElement(i, []);
    lo = Math.min(lo, point[1]); hi = Math.max(hi, point[1]);
    u0 = Math.min(u0, texcoord[0]); u1 = Math.max(u1, texcoord[0]); v0 = Math.min(v0, texcoord[1]);
  }
  const height = Math.max(hi - lo, 1), width = Math.max(u1 - u0, 1);
  const id = String(e.building_id ?? e.object_id);
  const hash = createHash('sha256').update(id).digest().readUInt32BE(0);
  const cls = { facade_brick:1, facade_wood:3, facade_concrete:4 }[e.material_key] ?? 0;
  const d = request.facadeKit.descriptions?.[id];
  const sourceWidth = request.facadeKit.referenceWidths?.[id] ?? width;
  const sourceBay = sourceWidth / Math.max(1, d?.window_columns || Math.round(sourceWidth / 3));
  const described = d ? {
    module: d.window_columns === 0 && d.ground_floor === 'plain'
      ? 42 + ['plaster', 'brick', 'stone', 'wood', 'concrete', 'glass', 'metal'].indexOf(d.material)
      : ['plaster', 'brick', 'stone', 'wood', 'concrete', 'glass', 'metal'].indexOf(d.material) * 6
      + (d.window_width > .55 ? 3 : 0) + ({ shops:2, doors:1 }[d.ground_floor] ?? 0),
    wall: d.wall_colour, trim: d.trim_colour,
    bay: sourceBay >= .9 && sourceBay <= 8 ? sourceBay : 3,
    storey: height / Math.max(1, height / d.storeys >= 2.4 && height / d.storeys <= 5.5 ? d.storeys : Math.round(height / 3.2)),
  } : null;
  const p = described ?? e.facade_kit ?? {
    module: cls * 6 + (width >= 6 ? 1 : 0),
    wall: ['#d4d1c9', '#b8b5ae', '#cfc8b8', '#d8bf7a', '#c99a86'][hash % 5], trim:'#e4ddd0',
    bay: width / Math.max(1, Math.round(width / 3)), storey: height / Math.max(1, Math.round(height / 3.2)),
  };
  // Blank modules still represent three metres of cladding, never the whole wall.
  if (p.module >= 42) p.bay = 3;
  // Keep TEXCOORD_0 in metres. Each primitive owns its kit attributes before batching.
  const buffer = pos.getBuffer();
  const attribute = (semantic, type, values) => {
    const array = new Float32Array(pos.getCount() * values.length);
    for (let i = 0; i < pos.getCount(); i++) array.set(values, i * values.length);
    prim.setAttribute(semantic, doc.createAccessor().setType(type).setArray(array).setBuffer(buffer));
  };
  const linear = hex => hex.match(/[a-f0-9]{2}/gi).map(v => {
    const c = parseInt(v, 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  attribute('_KIT_LAYOUT', 'VEC3', [p.module, p.bay, p.storey]);
  attribute('_KIT_WALL', 'VEC3', linear(p.wall));
  attribute('_KIT_TRIM', 'VEC3', linear(p.trim));
  attribute('_KIT_ORIGIN', 'VEC2', [u0, v0]);
  const version = request.facadeKit.version ?? 2;
  if (version >= 3) {
    const floors = Math.max(1, Math.round(height / p.storey));
    const ground = p.ground ?? (floors > 1 && p.module < 42 && p.module % 3 === 2 ? Math.min(Math.max(p.storey * 1.25, 2.8), 5, height * .6) : p.storey);
    const upper = p.upper ?? (floors > 1 ? (height - ground) / (floors - 1) : ground);
    attribute('_KIT_FLOORS', 'VEC3', [ground, upper, Math.floor(width / p.bay / 2)]);
  }
  material.setName(`facade-kit:${e.layer}`).setExtras({ material_key:'facade_kit', facade_kit: { version } })
    .setBaseColorFactor([1, 1, 1, 1]).setMetallicFactor(1).setRoughnessFactor(1);
  const maps = request.facadeKit.maps;
  material.setBaseColorTexture(texture(maps.albedo, true, 'albedo'));
  material.setNormalTexture(texture(maps.normal, true, 'normal'));
  const orm = texture(maps.orm, true, 'orm');
  material.setMetallicRoughnessTexture(orm).setOcclusionTexture(orm);
  return true;
}
