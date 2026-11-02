import { expect, test, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { mergeRoadGraphs, type RoadGraph, type RoadGraphJson } from '../../src/gameplay/roadGraph';

type Job = { id: number; keys: number[]; additions: [number, RoadGraphJson][] };
type Reply = { id: number; graph?: RoadGraph; error?: string };

test('worker round trips preserve reusable buffers across empty, cached, removed and returning road parts', async () => {
  const { a, b } = JSON.parse(readFileSync(new URL('../fixtures/roads-v2-seam.json', import.meta.url), 'utf8')) as {
    a: RoadGraphJson; b: RoadGraphJson;
  };
  const requests: Job[] = [];
  let transport: Transport;
  // Execute the real worker handler with real clone/transfer semantics, without launching a browser.
  const context = {
    onmessage: null as null | ((event: { data: Job }) => void),
    postMessage(reply: Reply, options?: StructuredSerializeOptions) {
      const data = structuredClone(reply, options);
      queueMicrotask(() => transport.onmessage?.({ data }));
    },
  };
  class Transport {
    onmessage: ((event: { data: Reply }) => void) | null = null;
    onerror = null;
    constructor() { transport = this; }
    postMessage(job: Job) {
      const data = structuredClone(job);
      requests.push(data);
      context.onmessage!({ data });
    }
    terminate() {}
  }
  vi.stubGlobal('self', context);
  vi.stubGlobal('Worker', Transport);
  try {
    await import('../../src/gameplay/roadGraph.worker');
    const { mergeRoadGraphsAsync } = await import('../../src/gameplay/roadGraphAsync');
    // Empty results reuse the module's singleton typed arrays. Transferring them breaks the next call.
    await expect(mergeRoadGraphsAsync([])).resolves.toEqual(mergeRoadGraphs([]));
    await expect(mergeRoadGraphsAsync([])).resolves.toEqual(mergeRoadGraphs([]));
    const first = await mergeRoadGraphsAsync([a]);
    expect(first).toEqual(mergeRoadGraphs([a]));
    first.nodes[0] = 999999;
    first.edges[0].points[0] = 999999;
    await expect(mergeRoadGraphsAsync([a])).resolves.toEqual(mergeRoadGraphs([a]));
    expect(requests.at(-1)!.additions).toHaveLength(0);
    await expect(mergeRoadGraphsAsync([a, b])).resolves.toEqual(mergeRoadGraphs([a, b]));
    expect(requests.at(-1)!.additions).toHaveLength(1);
    await expect(mergeRoadGraphsAsync([b])).resolves.toEqual(mergeRoadGraphs([b]));
    expect(requests.at(-1)!.additions).toHaveLength(0);
    await expect(mergeRoadGraphsAsync([a, b])).resolves.toEqual(mergeRoadGraphs([a, b]));
    expect(requests.at(-1)!.additions).toHaveLength(1);
    await expect(mergeRoadGraphsAsync([])).resolves.toEqual(mergeRoadGraphs([]));
    await expect(mergeRoadGraphsAsync([])).resolves.toEqual(mergeRoadGraphs([]));
  } finally { vi.unstubAllGlobals(); }
});
