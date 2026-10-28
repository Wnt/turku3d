import fs from 'node:fs';
import path from 'node:path';

export function parseMapCoordinate(text) {
  const match = /^\s*(-?\d{1,2}\.\d{4,}),\s*(-?\d{1,3}\.\d{4,})\s*$/.exec(text);
  if (!match) return null;
  const point = {lat: Number(match[1]), lon: Number(match[2])};
  return Math.abs(point.lat) <= 90 && Math.abs(point.lon) <= 180 ? point : null;
}

// A session has one immutable body store. Every extent gets hard links, even on resume.
export class ResponseCatalogue {
  constructor(root) {
    this.root = root;
    fs.mkdirSync(path.join(root, 'responses'), {recursive: true, mode: 0o700});
    this.index = path.join(root, 'catalogue.json');
    this.rows = new Map((fs.existsSync(this.index) ? JSON.parse(fs.readFileSync(this.index)) : [])
      .map(row => [`${row.url}|${row.sha256}`, row]));
  }
  add(info, sha, data) {
    const file = `responses/${sha}.bin`;
    const dest = path.join(this.root, file);
    if (!fs.existsSync(dest)) fs.writeFileSync(dest, data, {mode: 0o600, flag: 'wx'});
    this.rows.set(`${info.url}|${sha}`, {...info, file, sha256: sha, bytes: data.length});
    const tmp = `${this.index}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify([...this.rows.values()]), {mode: 0o600});
    fs.renameSync(tmp, this.index);
  }
  merge(record, out) {
    const rows = new Map(record.responses.map(row => [`${row.url}|${row.sha256}`, row]));
    for (const row of this.rows.values()) {
      const dest = path.join(out, row.file);
      if (!fs.existsSync(dest)) fs.linkSync(path.join(this.root, row.file), dest);
      rows.set(`${row.url}|${row.sha256}`, row);
    }
    record.responses = [...rows.values()];
    record.session_catalogue = this.index;
  }
}

export function inventoryState(record, plan) {
  if (!record.detail_visits.some(v => v.timing?.target_error_m < 5)) return 'planned';
  if (!plan.summary.terminal_data_nodes) return 'visited';
  if (plan.frontier) return 'visited';
  return plan.complete ? 'eligible' : 'metadata resolved';
}
