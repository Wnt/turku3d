import { mkdirSync, readdirSync, readFileSync, renameSync, writeFileSync, unlinkSync } from 'node:fs';
import { join } from 'node:path';

const HOUR_ID = /^\d{4}-\d{2}-\d{2}T\d{2}\+0[23]$/;
export const RETENTION_MS = 7 * 24 * 3_600_000;

export class ScoreStore {
  constructor(directory) {
    this.directory = directory;
    mkdirSync(directory, { recursive: true });
  }

  write(hour, players) {
    const path = join(this.directory, `${hour.id}.json`);
    // Atomic replacement: readers never observe half a completed hour.
    writeFileSync(`${path}.tmp`, `${JSON.stringify({ hour, players }, null, 2)}\n`, { mode: 0o600 });
    renameSync(`${path}.tmp`, path);
  }

  read(id) {
    if (!HOUR_ID.test(id)) return null;
    try { return JSON.parse(readFileSync(join(this.directory, `${id}.json`), 'utf8')); }
    catch (error) { if (error.code === 'ENOENT') return null; throw error; }
  }

  index() {
    return readdirSync(this.directory).filter(f => f.endsWith('.json') && HOUR_ID.test(f.slice(0, -5)))
      .map(f => this.read(f.slice(0, -5)).hour)
      .sort((a, b) => b.start.localeCompare(a.start));
  }

  prune(now) {
    for (const hour of this.index()) {
      if (Date.parse(hour.end) <= now - RETENTION_MS) unlinkSync(join(this.directory, `${hour.id}.json`));
    }
  }
}
