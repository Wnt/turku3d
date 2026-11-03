// COPC range reads and LAZ decompression off the main thread: a tile's header and whole hierarchy, then one node's
// points at a time (fetch the chunk's byte range, laz-perf ChunkDecoder, decode, shuffle so a prefix is a uniform
// subsample for distance thinning). Positions arrive engine-frame, relative to the node's centre.
import createLazPerf from 'laz-perf/lib/worker/laz-perf.js';
import wasmUrl from 'laz-perf/lib/worker/laz-perf.wasm?url';
import { HEAD_READ, decodeRecords, decompressChunk, parseHeader, parseHierarchyPage, type CopcHeader, type CopcNode,
  type LazPerfModule } from './copc';
import { shufflePoints } from './shuffle';

export type WorkerRequest =
  | { id: number; kind: 'tile'; url: string }
  | { id: number; kind: 'node'; url: string; node: CopcNode; header: CopcHeader; anchor: [number, number, number] };

let lazPerf: Promise<LazPerfModule> | null = null;
const lazModule = () => lazPerf ??= fetch(wasmUrl).then(r => r.arrayBuffer())
  .then(wasmBinary => (createLazPerf as unknown as (args: object) => Promise<LazPerfModule>)({ wasmBinary }));

/** `size` bytes at `offset`; `upTo` accepts fewer when the file ends first. */
async function range(url: string, offset: number, size: number, upTo = false): Promise<ArrayBuffer> {
  const reply = await fetch(url, { headers: { Range: `bytes=${offset}-${offset + size - 1}` } });
  if (!reply.ok) throw new Error(`${url} ${offset}+${size}: HTTP ${reply.status}`);
  const body = await reply.arrayBuffer();
  // A server that ignores Range answers 200 with the whole file: slice it rather than misread it.
  if (reply.status === 200 && body.byteLength > size) return body.slice(offset, offset + size);
  if (body.byteLength !== size && !(upTo && body.byteLength < size)) throw new Error(`${url} ${offset}+${size}: got ${body.byteLength} bytes`);
  return body;
}

async function tile(url: string) {
  let head = await range(url, 0, HEAD_READ, true);
  let header = parseHeader(head);
  if (header.pointOffset > head.byteLength) { head = await range(url, 0, header.pointOffset); header = parseHeader(head); }
  const nodes: CopcNode[] = [];
  const pending = [header.rootPage];
  while (pending.length) {
    const page = pending.pop()!;
    const parsed = parseHierarchyPage(await range(url, page.offset, page.size));
    nodes.push(...parsed.nodes);
    pending.push(...parsed.pages);
  }
  return { header, nodes };
}

async function node(request: Extract<WorkerRequest, { kind: 'node' }>) {
  const { url, node, header, anchor } = request;
  const [laz, chunk] = await Promise.all([lazModule(), range(url, node.offset, node.byteSize)]);
  const records = decompressChunk(laz, new Uint8Array(chunk), node.pointCount, header.pointFormat, header.recordLength);
  const points = decodeRecords(records, node.pointCount, header, anchor);
  shufflePoints(points, node.key);
  return points;
}

self.onmessage = async (event: MessageEvent<WorkerRequest>) => {
  const request = event.data;
  try {
    if (request.kind === 'tile') {
      self.postMessage({ id: request.id, ok: true, result: await tile(request.url) });
    } else {
      const points = await node(request);
      self.postMessage({ id: request.id, ok: true, result: points },
        { transfer: [points.positions.buffer, points.colors.buffer, points.survey.buffer, points.extra.buffer] });
    }
  } catch (error) {
    self.postMessage({ id: request.id, ok: false, error: String((error as Error)?.message ?? error) });
  }
};
