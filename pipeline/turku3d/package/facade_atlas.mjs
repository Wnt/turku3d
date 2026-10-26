import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

const PAGE = 2048;
const GUTTER = 4;
const slots = ['albedo', 'normal', 'orm'];
const clamp = (v) => Math.max(0, Math.min(1, v));
const align4 = (v) => Math.ceil(v / 4) * 4;

export function atlasUv(u, v, scale, offset, entry, pageHeight) {
  // glTF/KTX coordinates have v=0 at the image's top row. Keep that origin
  // when moving a source image into an atlas row.
  return [
    (entry.x + GUTTER + clamp(u * scale[0] + offset[0]) * entry.width) / PAGE,
    (entry.y + GUTTER + clamp(v * scale[1] + offset[1]) * entry.height) / pageHeight,
  ];
}

function imageSize(texture) {
  const image = texture.getImage();
  return [image.readUInt32LE(20), image.readUInt32LE(24)];
}

async function extract(texture, target, cache, run, sharp) {
  const bytes = texture.getImage();
  const key = createHash('sha256').update('facade-atlas-extract-v2').update(bytes)
    .update(JSON.stringify(target)).digest('hex');
  const output = path.join(cache, `${key}.png`);
  if (!fs.existsSync(output)) {
    const input = path.join(cache, `${key}.${process.pid}.ktx2`);
    const temp = path.join(cache, `${key}.${process.pid}.png`);
    const resized = path.join(cache, `${key}.${process.pid}.resized.png`);
    fs.writeFileSync(input, bytes);
    try {
      run('ktx', ['extract', '--transcode', 'rgb8', input, temp]);
      await sharp(temp).resize(target[0], target[1], { fit:'fill' }).png().toFile(resized);
      fs.renameSync(resized, output);
    } finally {
      for (const file of [input, temp, resized]) if (fs.existsSync(file)) fs.unlinkSync(file);
    }
  }
  return output;
}

function pack(entries) {
  const pages = [];
  for (const entry of entries.sort((a, b) => b.height - a.height || b.width - a.width || a.id.localeCompare(b.id))) {
    const width = entry.width + 2 * GUTTER;
    const height = entry.height + 2 * GUTTER;
    if (width > PAGE || height > PAGE) throw new Error(`Facade exceeds ${PAGE}px atlas page: ${entry.id}`);
    let placed = false;
    for (const page of pages) {
      let x = page.x; let y = page.y; let row = page.row;
      if (x + width > PAGE) { x = 0; y += row; row = 0; }
      if (y + height > PAGE) continue;
      entry.page = pages.indexOf(page); entry.x = x; entry.y = y;
      page.x = x + width; page.y = y; page.row = Math.max(row, height);
      page.entries.push(entry); placed = true; break;
    }
    if (!placed) {
      entry.page = pages.length; entry.x = 0; entry.y = 0;
      pages.push({ x:width, y:0, row:height, entries:[entry] });
    }
  }
  for (const page of pages) page.height = align4(page.y + page.row);
  return pages;
}

export async function atlasFacades(doc, request, { sharp, run, KHRTextureBasisu }) {
  const primitives = [];
  const byId = new Map();
  for (const node of doc.getRoot().listNodes()) for (const prim of node.getMesh()?.listPrimitives() ?? []) {
    const material = prim.getMaterial();
    const colour = material?.getBaseColorTexture();
    const info = colour?.getExtras().facade_map;
    if (!info) continue;
    const maps = [colour, material.getNormalTexture(), material.getOcclusionTexture()];
    if (maps.some((map, index) => map?.getExtras().facade_map?.id !== info.id ||
        map.getExtras().facade_map.slot !== slots[index])) throw new Error(`Incomplete facade maps: ${info.id}`);
    const transform = material.getBaseColorTextureInfo().getExtension('KHR_texture_transform');
    if (!transform) throw new Error(`Missing facade UV transform: ${info.id}`);
    const sourceSize = imageSize(colour);
    if (maps.some(map => imageSize(map).some((n, i) => n !== sourceSize[i]))) {
      throw new Error(`Facade map sizes differ: ${info.id}`);
    }
    const density = request.facadeDensity?.[info.id] ?? (info.height <= 5 ? 72 : 48);
    let [width, height] = sourceSize.map((n, i) => Math.max(4, Math.min(n,
      Math.round([info.width, info.height][i] * density / 4) * 4)));
    if (Math.max(width, height) > PAGE - 2 * GUTTER) {
      const ratio = (PAGE - 2 * GUTTER) / Math.max(width, height);
      width = Math.max(4, Math.floor(width * ratio / 4) * 4);
      height = Math.max(4, Math.floor(height * ratio / 4) * 4);
    }
    const key = JSON.stringify([width, height]) + ':' + maps.map(map =>
      createHash('sha256').update(map.getImage()).digest('hex')).join(':');
    let entry = byId.get(key);
    if (!entry) {
      entry = { id:info.id, width, height, maps };
      byId.set(key, entry);
    }
    primitives.push({ prim, transform, entry });
  }
  if (byId.size < 2) return { pages:0, facades:byId.size };

  const pages = pack([...byId.values()]);
  const materials = [];
  for (const [pageIndex, page] of pages.entries()) {
    const images = [];
    for (const [slotIndex, slot] of slots.entries()) {
      const inputs = await Promise.all(page.entries.map(async entry => ({ entry,
        file:await extract(entry.maps[slotIndex], [entry.width, entry.height], request.textureCache, run, sharp) })));
      const overlays = await Promise.all(inputs.map(async ({ entry, file }) => ({
        input:await sharp(file).extend({ top:GUTTER, bottom:GUTTER, left:GUTTER, right:GUTTER, extendWith:'copy' }).png().toBuffer(),
        top:entry.y, left:entry.x,
      })));
      const png = await sharp({ create:{ width:PAGE, height:page.height, channels:3,
        background:slot === 'normal' ? '#8080ff' : slot === 'orm' ? '#ffffff' : '#808080' } })
        .composite(overlays).png().toBuffer();
      // The normal map uses the same normalized atlas UVs at half resolution.
      // Keep the photographed colour and ORM maps at their authored density.
      const image = slot === 'normal'
        ? await sharp(png).resize(PAGE / 2, Math.max(4, align4(page.height / 2)), { fit:'fill' }).png().toBuffer()
        : png;
      const hash = createHash('sha256').update('facade-atlas-v1').update(slot).update(image).digest('hex');
      const dest = path.join(request.textureCache, `${hash}.ktx2`);
      if (!fs.existsSync(dest)) {
        const source = path.join(request.textureCache, `${hash}.${process.pid}.png`);
        const temp = path.join(request.textureCache, `${hash}.${process.pid}.ktx2`);
        fs.writeFileSync(source, image);
        try {
          const codec = slot === 'normal' ? 'uastc' : 'etc1s';
          run('toktx', ['--t2', '--genmipmap', '--threads', '2', '--assign_oetf', slot === 'albedo' ? 'srgb' : 'linear',
            '--assign_primaries', slot === 'albedo' ? 'bt709' : 'none', '--encode', codec,
            ...(codec === 'etc1s' ? ['--qlevel', slot === 'albedo' ? '100' : '80'] : ['--uastc_quality', '2', '--zcmp', '18']),
            temp, source]);
          run('ktx', ['validate', '--gltf-basisu', temp]);
          fs.renameSync(temp, dest);
        } finally {
          for (const file of [source, temp]) if (fs.existsSync(file)) fs.unlinkSync(file);
        }
      }
      images.push(doc.createTexture(`facade_atlas_${pageIndex}_${slot}`).setImage(fs.readFileSync(dest)).setMimeType('image/ktx2'));
    }
    const material = doc.createMaterial(`facade_atlas_${pageIndex}`).setExtras({ material_key:'facade_atlas' })
      .setBaseColorTexture(images[0]).setBaseColorFactor([1,1,1,1])
      .setNormalTexture(images[1]).setOcclusionTexture(images[2]).setMetallicRoughnessTexture(images[2])
      .setMetallicFactor(1).setRoughnessFactor(1);
    for (const property of ['BaseColor', 'Normal', 'Occlusion', 'MetallicRoughness']) {
      material[`get${property}TextureInfo`]().setWrapS(33071).setWrapT(33071);
    }
    materials.push(material);
  }
  doc.createExtension(KHRTextureBasisu).setRequired(true);
  for (const { prim, transform, entry } of primitives) {
    const page = pages[entry.page];
    const uv = prim.getAttribute('TEXCOORD_0').clone();
    const array = uv.getArray();
    const scale = transform.getScale(); const offset = transform.getOffset();
    for (let i = 0; i < array.length; i += 2) {
      [array[i], array[i+1]] = atlasUv(array[i], array[i+1], scale, offset, entry, page.height);
    }
    prim.setAttribute('TEXCOORD_0', uv).setMaterial(materials[entry.page]);
  }
  return { pages:pages.length, facades:byId.size };
}
