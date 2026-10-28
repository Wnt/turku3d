import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { bindFacadeKit } from './facade_kit.mjs';

// W1-03 colour/material classes mapped deliberately to the five available W1-09 assets.
// Brick, timber and tile are approximations until corresponding library materials exist.
const LIBRARY_FALLBACKS = {
  facade_brick: 'render_smooth', facade_wood: 'render_smooth',
  facade_concrete: 'render_smooth', facade_plaster: 'render_smooth',
  roof_tile_red: 'roof_standing_seam_metal', roof_metal_green: 'roof_standing_seam_metal',
  roof_metal_dark: 'roof_standing_seam_metal', roof_metal_gray: 'roof_standing_seam_metal',
  render_smooth: 'render_smooth', roof_standing_seam_metal: 'roof_standing_seam_metal',
  plinth_granite: 'plinth_granite', ground_asphalt: 'ground_asphalt', ground_granite: 'ground_granite',
};

function safe(value) {
  const s = String(value);
  if (!/^[a-zA-Z0-9_.-]+$/.test(s) || s === '.' || s === '..') throw new Error(`Unsafe texture key: ${s}`);
  return s;
}

function pair(value, name) {
  if (!Array.isArray(value) || value.length !== 2 || !value.every(Number.isFinite)) {
    throw new Error(`Invalid texture ${name}`);
  }
  return value;
}

export function createMaterialBinder(doc, request, { KHRTextureBasisu, KHRTextureTransform }) {
  const textures = new Map();
  const bindings = new Set(request.facadeBindings);
  function texture(filename, shared, slot, facadeInfo) {
    if (!textures.has(filename)) {
      const ext = path.extname(filename);
      const mime = ext === '.ktx2' ? 'image/ktx2' : ext === '.png' ? 'image/png' : 'image/jpeg';
      textures.set(filename, doc.createTexture(path.basename(filename)).setImage(fs.readFileSync(filename)).setMimeType(mime)
        .setExtras({ shared_library: shared, facade_map: facadeInfo ? { ...facadeInfo, slot } : null }));
      if (ext === '.ktx2') doc.createExtension(KHRTextureBasisu).setRequired(true);
    }
    return textures.get(filename);
  }
  function library(key) {
    const directory = path.join(request.library, safe(key));
    const maps = {};
    for (const slot of ['albedo', 'normal', 'orm']) {
      for (const ext of ['ktx2', 'png', 'jpg', 'jpeg']) {
        const f = path.join(directory, `${slot}.${ext}`);
        if (fs.existsSync(f)) { maps[slot] = f; break; }
      }
    }
    const metadata = path.join(directory, 'material.json');
    const scale = fs.existsSync(metadata) ? JSON.parse(fs.readFileSync(metadata, 'utf8')).uv_scale : undefined;
    return { maps, scale: pair(scale ?? [1, 1], 'uv_scale'), offset: [0, 0], unique: false };
  }
  function facade(e) {
    // W1-09 normalizes authoritative facade IDs to <building_id>_<facade_index>.
    if (e.building_id === undefined || e.facade_index === undefined ||
        !bindings.has(`${e.building_id}_${e.facade_index}`)) return null;
    const directory = path.join(request.facades, safe(e.building_id));
    const sidecar = path.join(directory, `${safe(e.facade_index)}.json`);
    if (!fs.existsSync(sidecar)) return null;
    const meta = JSON.parse(fs.readFileSync(sidecar, 'utf8'));
    if (meta.building_id !== e.building_id || meta.facade_index !== e.facade_index ||
        !meta.qa?.gltf_basisu_valid || meta.uv?.units !== 'metres') return null;
    const maps = {};
    for (const slot of ['albedo', 'normal', 'orm']) {
      if (!meta.files?.[slot]) return null;
      const name = safe(meta.files[slot]);
      const f = path.join(directory, name);
      if (!fs.existsSync(f)) return null;
      if (createHash('sha256').update(fs.readFileSync(f)).digest('hex') !== meta.hashes?.[name]) {
        throw new Error(`Facade map changed after export: ${f}`);
      }
      maps[slot] = f;
    }
    return { maps, scale: pair(meta.uv.scale, 'uv.scale'), offset: pair(meta.uv.offset, 'uv.offset'), unique: true,
      facadeInfo: { id: `${e.building_id}_${e.facade_index}`, width: meta.width_m, height: meta.height_m } };
  }
  return (material, prim, e) => {
    const key = LIBRARY_FALLBACKS[e.material_key] ?? e.material_key;
    const own = facade(e);
    if (!own && request.facadeKit && bindFacadeKit(doc, request, material, prim, e, texture)) return;
    const selected = own ?? library(key);
    let { albedo } = selected.maps;
    const { normal, orm } = selected.maps;
    if (e.roof_rgb) {
      bindRoofColour(doc, material, prim, e.roof_rgb);
      albedo = undefined;
    }
    if ((albedo || normal || orm) && !prim.getAttribute('TEXCOORD_0')) throw new Error(`Missing UVs: ${e.object_id}`);
    // The source mesh name and semantic key are per object, not per GPU material.
    // Canonicalize library-backed materials so dedup can batch all walls/roofs
    // using the same maps. Per-object keys remain in the node's QA ranges.
    if (e.layer === 'props' && e.material_key !== 'props_sign' && e.material_key !== 'props_bus_stop') {
      // Furniture shares the producer's neutral palette. Keep type on object
      // extras, while one material allows all palette meshes to batch.
      material.setName('props_palette');
      material.setExtras({ material_key:'props_palette' });
    } else if (!selected.unique && (albedo || normal || orm)) {
      material.setName(`library:${key}`);
      material.setExtras({ material_key: key, library_material_key: key });
    } else {
      material.setExtras({ ...material.getExtras(), material_key: e.material_key, library_material_key: key });
    }
    if (albedo) material.setBaseColorTexture(texture(albedo, !selected.unique, 'albedo', selected.facadeInfo)).setBaseColorFactor([1, 1, 1, 1]);
    if (normal) material.setNormalTexture(texture(normal, !selected.unique, 'normal', selected.facadeInfo));
    if (orm) material.setOcclusionTexture(texture(orm, !selected.unique, 'orm', selected.facadeInfo)).setMetallicRoughnessTexture(texture(orm, !selected.unique, 'orm', selected.facadeInfo))
      .setMetallicFactor(1).setRoughnessFactor(1);
    for (const [slot, filename] of [['BaseColor', albedo], ['Normal', normal], ['Occlusion', orm], ['MetallicRoughness', orm]]) {
      if (!filename) continue; // Preserve embedded source textures and their UV contract.
      const info = material[`get${slot}TextureInfo`]();
      const wrap = selected.unique ? 33071 : 10497;
      info.setWrapS(wrap).setWrapT(wrap);
      info.setExtension('KHR_texture_transform', doc.createExtension(KHRTextureTransform).setRequired(true)
        .createTransform().setScale(selected.scale).setOffset(selected.offset));
    }
  };
}

// Measured roof colours use vertex colours so roofs with different RGB still share a batch.
export function bindRoofColour(doc, material, prim, rgb) {
  if (!Array.isArray(rgb) || rgb.length !== 3 || !rgb.every(v => Number.isFinite(v) && v >= 0 && v <= 255)) {
    throw new Error('Invalid orthophoto roof RGB');
  }
  const linear = rgb.map(v => { const s = v / 255; return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; });
  const position = prim.getAttribute('POSITION');
  const colours = new Float32Array(position.getCount() * 3);
  for (let i = 0; i < position.getCount(); i++) colours.set(linear, i * 3);
  prim.setAttribute('COLOR_0', doc.createAccessor().setBuffer(position.getBuffer()).setType('VEC3').setArray(colours));
  material.setBaseColorTexture(null).setBaseColorFactor([1, 1, 1, 1]);
}
