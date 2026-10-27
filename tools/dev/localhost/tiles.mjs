// Which tile release the localhost game streams under /tiles (layout and keys: scripts/dev/tile_store.py).
// For main's commit H with tile key K = key(H):
//  1. the integrator's store entry K (built from H's code, unverified until its integration row is green);
//  2. the service's own quick build of K (tile-build on the published layers);
//  3. the live release, when the live commit has the same key (a code-only landing needs no tile build);
//  4. otherwise the last selected release, while a quick build of the affected tiles runs in a separate checkout.
import { execFile, spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { promisify } from 'node:util';
import { landingOf, readRows } from './status.mjs';

const run = promisify(execFile);
/** Re-resolve a selection that is not the integrator's build this often (ms): its entry may have appeared. */
const RECHECK_MS = 5000;

const mtime = (file) => { try { return fs.statSync(file).mtimeMs; } catch { return 0; } };

/**
 * L0 tiles a quick build of `head` needs, given the commit `base` the streamed tiles were built for: the union of
 * the ledger rows' `tiles` (scripts/dev/landing.py) of the landings in base..head; every listed tile when a landing
 * has no such field or base is unknown.
 */
export function affectedTiles(landings, listed) {
  if (!landings.length || landings.some(row => !Array.isArray(row?.tiles))) return [...listed];
  return [...new Set(landings.flatMap(row => row.tiles))].filter(tile => listed.includes(tile)).sort();
}

export class TileStore {
  constructor({ repo, worktree, published, stateDir, registry, build, log }) {
    Object.assign(this, { repo, worktree, published, stateDir, registry, build, log });
    this.selection = null;
    this.checked = { signature: null, at: 0 };
    this.child = null;
    this.failed = new Set();
  }

  python(...args) {
    return run('python3', [path.join(this.worktree, 'scripts/dev/tile_store.py'), ...args,
      '--repo', this.worktree, '--published', this.published], { maxBuffer: 16 << 20 }).then(r => r.stdout.trim());
  }

  liveDir() { return path.join(this.published, 'live/tiles'); }

  listed() {
    try { return JSON.parse(fs.readFileSync(path.join(this.published, 'playable-tiles.json'), 'utf8')).tiles; }
    catch { return ['t0_0_0', 't0_0_1', 't0_1_0', 't0_1_1']; }
  }

  /** The selection for main's commit `head`: { dir, key, source, commit, building }. */
  async select(head, isAncestor) {
    const signature = [head, mtime(path.join(this.published, 'playable-tiles.json')),
      mtime(path.join(this.published, 'facade-approvals/assembly/MANIFEST.json')), fs.readlinkSync(path.join(this.published, 'live'))].join('|');
    const fresh = signature === this.checked.signature;
    if (fresh && (this.selection?.source === 'integrator' || Date.now() - this.checked.at < RECHECK_MS)) return this.selection;
    this.checked = { signature, at: Date.now() };
    const found = JSON.parse(await this.python('resolve', '--commit', head));
    let next;
    if (found.integrator) next = { dir: found.integrator, key: found.key, source: 'integrator', commit: head };
    else if (found.localhost) next = { dir: found.localhost, key: found.quick_key, source: 'localhost', commit: head };
    else {
      const live = JSON.parse(fs.readFileSync(path.join(this.published, 'live/live-build.json'), 'utf8')).commit;
      const liveKey = await this.python('key', '--commit', live).catch(() => null);
      if (liveKey === found.key) next = { dir: fs.realpathSync(this.liveDir()), key: found.key, source: 'live', commit: live };
      else {
        const base = this.selection ?? { dir: fs.realpathSync(this.liveDir()), key: liveKey, source: 'live', commit: live };
        const tiles = this.affected(base.commit, head, isAncestor);
        next = { ...base, building: this.build && !this.failed.has(found.key) ? tiles : null };
        if (next.building) this.quickBuild(head, found.key, base.dir, tiles);
      }
    }
    if (next.dir !== this.selection?.dir) this.log({ type: 'tiles', commit: head, source: next.source, key: next.key, dir: next.dir });
    this.selection = next;
    return next;
  }

  affected(base, head, isAncestor) {
    const landed = readRows(path.join(this.registry, 'landed.jsonl'));
    const rows = [];
    if (base && isAncestor(base, head)) {
      for (const row of landed) if (row.commit && row.commit !== base && isAncestor(row.commit, head) && !isAncestor(row.commit, base)) rows.push(row);
    }
    if (!landingOf(head, landed)) rows.push(null);
    return affectedTiles(rows, this.listed());
  }

  /** One quick build at a time, in its own checkout so landings keep moving the served worktree meanwhile. */
  quickBuild(head, key, base, tiles) {
    if (this.child?.key === key) return;
    if (this.child) { this.child.process.kill('SIGTERM'); this.child = null; }
    const checkout = path.join(this.stateDir, 'build-repo');
    const work = path.join(this.stateDir, 'build-work');
    const started = Date.now();
    const prepare = fs.existsSync(path.join(checkout, '.git'))
      ? run('git', ['checkout', '--detach', '--force', '--quiet', head], { cwd: checkout })
      : run('git', ['worktree', 'add', '--detach', checkout, head], { cwd: this.repo });
    const child = { key, process: null };
    this.child = child;
    this.log({ type: 'quick-build', commit: head, key, tiles });
    void prepare.then(() => {
      if (this.child !== child) return;
      child.process = spawn('python3', [path.join(checkout, 'scripts/dev/tile_store.py'), 'quick-build', '--repo', checkout,
        '--commit', head, '--published', this.published, '--work', work, '--base', base, '--tiles', ...tiles],
      { stdio: ['ignore', 'pipe', 'pipe'] });
      let output = '';
      child.process.stdout.on('data', d => { output += d; });
      child.process.stderr.on('data', d => { output += d; });
      child.process.on('exit', code => {
        if (this.child === child) this.child = null;
        if (code) this.failed.add(key);
        this.checked.signature = null; // re-resolve on the next status pass
        this.log({ type: 'quick-build-done', commit: head, key, code, seconds: (Date.now() - started) / 1000,
          ...(code ? { output: output.slice(-2000) } : {}) });
      });
    }, (e) => { this.child = null; this.failed.add(key); this.log({ type: 'quick-build-done', key, code: -1, output: String(e) }); });
  }

  stop() { this.child?.process?.kill('SIGTERM'); }
}
