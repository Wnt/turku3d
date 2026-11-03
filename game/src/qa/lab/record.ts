// Bench lab capture folders: the ordinary capture recorder (telemetry/capture.ts) runs over the measured part, and the
// result is appended to the folder's meta.json as `bench.result` through the same /__capture middleware, so
// tools/perf (capture_summary.py, tile_timeline.py, bench_matrix.py) reads a lab run like any capture.
import { DriveCapture, captureAllowed } from '../../telemetry/capture';
import type { LabRecorder } from './lab';

/** A batch number far above any the recorder reaches: the middleware commits each batch id once. */
const RESULT_BATCH = 1_000_000;

export function startLabRecording(game: Record<string, unknown>, meta: Record<string, unknown>): LabRecorder | null {
  if (!captureAllowed()) return null;
  return new DriveCapture(game, meta);
}

export async function saveLabResult(id: string, result: Record<string, unknown>, fetcher: typeof fetch = fetch) {
  const response = await fetcher(`/__capture/${id}/meta.json`, { cache: 'no-store' });
  if (!response.ok) throw new Error(`capture ${id} meta.json: HTTP ${response.status}`);
  const meta = await response.json() as Record<string, unknown>;
  const bench = { ...(meta.bench as Record<string, unknown> | undefined ?? {}), result };
  const saved = await fetcher(`/__capture/${id}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ batch: RESULT_BATCH, meta: { ...meta, bench } }) });
  if (!saved.ok) throw new Error(`capture ${id} result: HTTP ${saved.status}`);
}
