import { createHash } from 'node:crypto';

// Registered sign faces share the same 256 x 128 palette and differ only in
// their right half. Pack them into one texture so all sign instances batch.
export async function atlasPropSigns(doc, sharp) {
  const signs = [];
  const images = new Map();
  for (const node of doc.getRoot().listNodes()) {
    if (node.getExtras().material_key !== 'props_sign') continue;
    for (const prim of node.getMesh()?.listPrimitives() ?? []) {
      const material = prim.getMaterial();
      const texture = material?.getBaseColorTexture();
      const bytes = texture?.getImage();
      if (!bytes || !prim.getAttribute('TEXCOORD_0')) continue;
      const meta = await sharp(bytes).metadata();
      if (meta.width !== 256 || meta.height !== 128) throw new Error('Unexpected sign atlas size');
      const key = createHash('sha256').update(bytes).digest('hex');
      if (!images.has(key)) images.set(key, bytes);
      signs.push({ prim, material, key });
    }
  }
  if (images.size < 2) return;
  const keys = [...images.keys()].sort();
  const packed = await sharp({ create:{ width:256 * keys.length, height:128, channels:3, background:'#000000' } })
    .composite(keys.map((key, index) => ({ input:images.get(key), left:256 * index, top:0 })))
    .png().toBuffer();
  const texture = doc.createTexture('props_sign_atlas').setImage(packed).setMimeType('image/png');
  const material = signs[0].material.clone().setName('props_sign_atlas').setBaseColorTexture(texture);
  for (const { prim, key } of signs) {
    const uv = prim.getAttribute('TEXCOORD_0').clone();
    const values = uv.getArray();
    const offset = keys.indexOf(key);
    for (let i = 0; i < values.length; i += 2) values[i] = (offset + values[i]) / keys.length;
    prim.setAttribute('TEXCOORD_0', uv).setMaterial(material);
  }
}
