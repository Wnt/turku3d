// A texture is encoded once, in one colour space: sRGB for colour slots, linear for data slots (normal, ORM).
// Material/texture deduplication merges textures with identical image bytes, so one image can end up bound to
// both kinds of slot (t0_-3_-2). Each such texture is split: the colour slots keep it, the data slots get a copy,
// and each copy is then encoded in its own colour space.
export const SRGB_SLOTS = new Set(['baseColorTexture', 'emissiveTexture']);

const setter = slot => `set${slot[0].toUpperCase()}${slot.slice(1)}`;

/** Splits every texture bound to both sRGB and linear slots; returns one row per split texture. */
export function splitColourSpaces(doc, { skip = () => false } = {}) {
  const root = doc.getRoot(), graph = root.getGraph(), splits = [];
  for (const texture of root.listTextures()) {
    if (skip(texture)) continue;
    const edges = graph.listParentEdges(texture).filter(edge => edge.getParent() !== root);
    const linear = edges.filter(edge => !SRGB_SLOTS.has(edge.getName()));
    if (!linear.length || linear.length === edges.length) continue;
    const copy = texture.clone();
    const materials = new Set();
    for (const edge of linear) {
      const parent = edge.getParent(), slot = edge.getName();
      if (typeof parent[setter(slot)] !== 'function') throw new Error(`Cannot rebind texture slot ${slot}`);
      // The setters keep the slot's TextureInfo (UV set, wrap, KHR_texture_transform) and channel mask.
      parent[setter(slot)](copy);
      materials.add(parent.getName() || parent.propertyType);
    }
    splits.push({ texture: texture.getName(), slots: [...new Set(edges.map(edge => edge.getName()))].sort(),
      materials: [...materials].sort() });
  }
  return splits;
}
