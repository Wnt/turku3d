/** Decode the GLB container away from rendering; meshopt and KTX keep their existing decoder workers. */
export function decodeGlb(data: ArrayBuffer): { json: object; binary: ArrayBuffer | null } {
  const view = new DataView(data);
  if (view.byteLength < 12 || view.getUint32(0, true) !== 0x46546c67 || view.getUint32(4, true) !== 2
      || view.getUint32(8, true) !== data.byteLength) throw new Error('Invalid GLB v2 header');
  let json: object | undefined, binary: ArrayBuffer | null = null;
  for (let offset = 12; offset < data.byteLength;) {
    if (offset + 8 > data.byteLength) throw new Error('Truncated GLB chunk');
    const size = view.getUint32(offset, true), kind = view.getUint32(offset + 4, true);
    offset += 8;
    if (offset + size > data.byteLength || size % 4) throw new Error('Invalid GLB chunk size');
    if (kind === 0x4e4f534a) json = JSON.parse(new TextDecoder().decode(new Uint8Array(data, offset, size)));
    if (kind === 0x004e4942) binary = data.slice(offset, offset + size);
    offset += size;
  }
  if (!json) throw new Error('GLB JSON missing');
  return { json, binary };
}
