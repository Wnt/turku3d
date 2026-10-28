import fs from 'node:fs';
import path from 'node:path';

// A session has one immutable body store. Every extent gets hard links, even on resume.
export class ResponseCatalogue {
  constructor(root) {
    this.root = root;
    fs.mkdirSync(path.join(root, 'responses'), {recursive: true, mode: 0o700});
    this.index = path.join(root, 'catalogue.json');
    this.journal = path.join(root, 'catalogue.ndjson');
    const rows = fs.existsSync(this.index) ? JSON.parse(fs.readFileSync(this.index)) : [];
    if (fs.existsSync(this.journal)) {
      for (const line of fs.readFileSync(this.journal, 'utf8').split('\n').filter(Boolean))
        rows.push(JSON.parse(line));
    }
    this.rows = new Map(rows.map(row => [`${row.url}|${row.sha256}`, row]));
    this.dirty = rows.length > 0;
  }
  add(info, sha, data) {
    const file = `responses/${sha}.bin`;
    const dest = path.join(this.root, file);
    if (!fs.existsSync(dest)) fs.writeFileSync(dest, data, {mode: 0o600, flag: 'wx'});
    const key = `${info.url}|${sha}`;
    if (this.rows.has(key)) return;
    const row = {...info, file, sha256: sha, bytes: data.length};
    // Appending avoids rewriting the entire growing session index per response.
    fs.appendFileSync(this.journal, JSON.stringify(row) + '\n', {mode: 0o600});
    this.rows.set(key, row);
    this.dirty = true;
  }
  merge(record, out) {
    if (this.dirty) {
      const tmp = `${this.index}.tmp`;
      fs.writeFileSync(tmp, JSON.stringify([...this.rows.values()]), {mode: 0o600});
      fs.renameSync(tmp, this.index);
      fs.writeFileSync(this.journal, '', {mode: 0o600});
      this.dirty = false;
    }
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

export function inventoryState(plan) {
  if (!plan.available) return 'planned';
  if (!plan.metadata_complete) return 'visited';
  return plan.complete ? 'eligible' : 'metadata resolved';
}
