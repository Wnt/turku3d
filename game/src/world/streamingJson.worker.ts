import { jsonRecords, peekJson, type JsonRecord } from './glbRecords';

const jobs = new Map<number, Generator<JsonRecord>>();
self.onmessage = ({ data }: MessageEvent<{ id: number; buffer?: ArrayBuffer; peek?: boolean }>) => {
  try {
    if (data.peek) { self.postMessage({ id: data.id, peek: peekJson(JSON.parse(new TextDecoder().decode(data.buffer!))), done: true }); return; }
    if (data.buffer) jobs.set(data.id, jsonRecords(JSON.parse(new TextDecoder().decode(data.buffer))));
    const records = jobs.get(data.id)!;
    const rows: JsonRecord[] = [];
    let size = 0;
    // About 0.5 ms of main-thread record application per reply: few replies, each well inside the frame budget.
    while (rows.length < 2048 && size < 262144) {
      const next = records.next();
      if (next.done) { jobs.delete(data.id); self.postMessage({ id: data.id, rows, done: true }); return; }
      rows.push(next.value);
      size += 64 + (typeof next.value[3] === 'string' ? next.value[3].length * 2 : 8);
    }
    self.postMessage({ id: data.id, rows });
  } catch (error) { jobs.delete(data.id); self.postMessage({ id: data.id, error: String(error) }); }
};
