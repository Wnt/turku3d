import { decodeGlb } from './decodeGlb';
import { jsonRecords, type JsonRecord } from './glbRecords';
const jobs = new Map<number, { records: Generator<JsonRecord>; binary: ArrayBuffer | null }>();
self.onmessage = ({ data }: MessageEvent<{ id: number; buffer?: ArrayBuffer }>) => {
  try {
    if (data.buffer) {
      const decoded = decodeGlb(data.buffer);
      jobs.set(data.id, { records: jsonRecords(decoded.json), binary: decoded.binary });
    }
    const job = jobs.get(data.id)!;
    const rows: JsonRecord[] = [];
    let size = 0;
    while (rows.length < 256 && size < 32768) {
      const next = job.records.next();
      if (next.done) {
        jobs.delete(data.id);
        self.postMessage({ id: data.id, rows, done: true, binary: job.binary }, { transfer: job.binary ? [job.binary] : [] });
        return;
      }
      rows.push(next.value); size += 64 + (typeof next.value[3] === 'string' ? next.value[3].length * 2 : 8);
    }
    self.postMessage({ id: data.id, rows });
    // Main asks for the next bounded reply after its shared preparation budget allows it.
  } catch (error) { jobs.delete(data.id); self.postMessage({ id: data.id, error: String(error) }); }
};
