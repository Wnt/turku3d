// Per-wall program fields remain vertex attributes when the packager batches a tile.
export function bindProgram(doc, prim, extras, fallback, linear) {
  const pos = prim.getAttribute('POSITION'), buffer = pos.getBuffer();
  const rows = extras.facade_kit_walls ?? [{ start:0, count:pos.getCount(), ...fallback }];
  const owners = new Int32Array(pos.getCount()).fill(-1);
  for (const [row, p] of rows.entries()) {
    if (!Number.isInteger(p.start) || !Number.isInteger(p.count) || p.start < 0 || p.start + p.count > owners.length)
      throw new Error(`Invalid facade program range: ${extras.object_id}`);
    for (let i = p.start; i < p.start + p.count; i++) {
      if (owners[i] !== -1) throw new Error(`Overlapping facade program ranges: ${extras.object_id}`);
      owners[i] = row;
    }
  }
  if (owners.includes(-1)) throw new Error(`Unassigned facade program vertices: ${extras.object_id}`);
  const indices = prim.getIndices()?.getArray() ?? Uint32Array.from(owners, (_, i) => i);
  for (let i = 0; i < indices.length; i += 3)
    if (owners[indices[i]] !== owners[indices[i+1]] || owners[indices[i]] !== owners[indices[i+2]])
      throw new Error(`Mixed facade program triangle: ${extras.object_id}`);
  const definitions = {
    _KIT_LAYOUT: ['VEC3', p => [p.module, p.module >= 42 ? 3 : p.bay, p.storey]],
    _KIT_WALL: ['VEC3', p => linear(p.wall)],
    _KIT_TRIM: ['VEC3', p => linear(p.trim)],
    _KIT_BASE: ['VEC3', p => linear(p.base ?? p.wall)],
    _KIT_EXTENT: ['VEC4', p => [...(p.origin ?? [0, extras.base_height ?? 0]), ...p.size]],
    _KIT_FLOORS: ['VEC3', p => [p.ground, p.upper, Math.floor(p.size[0] / p.bay / 2)]],
    _KIT_OPENING: ['VEC4', p => p.opening],
    _KIT_BANDS: ['VEC4', p => p.bands],
    _KIT_DETAIL: ['VEC4', p => p.detail],
  };
  for (const [semantic, [type, values]] of Object.entries(definitions)) {
    const size = Number(type.slice(-1));
    const array = new Float32Array(pos.getCount() * size);
    for (const p of rows) {
      const data = values(p);
      for (let i = p.start; i < p.start + p.count; i++) array.set(data, i * size);
    }
    prim.getAttribute(semantic)?.dispose();
    prim.setAttribute(semantic, doc.createAccessor().setType(type).setArray(array).setBuffer(buffer));
  }
}
