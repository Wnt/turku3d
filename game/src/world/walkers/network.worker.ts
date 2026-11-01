// Builds the walker network off the main thread (5-20 ms per change for a 3x3 window of draft tiles).
import type { RoadGraphJson } from '../../gameplay/roadGraph';
import { buildWalkNetwork } from './network';

self.onmessage = ({ data }: MessageEvent<{ id: number; parts: RoadGraphJson[] }>) => {
  try {
    const start = performance.now();
    const network = buildWalkNetwork(data.parts);
    self.postMessage({ id: data.id, network, ms: performance.now() - start });
  } catch (error) { self.postMessage({ id: data.id, error: String(error) }); }
};
