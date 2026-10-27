// Which tile release the localhost game streams under /tiles (layout and keys: scripts/dev/tile_store.py).
// `tile_store.py served` picks it for main's commit H with tile key K = key(H) (base_release): the integrator's
// store entry K; the live release when the live commit has the same key (a code-only landing needs no tile build);
// otherwise the newest build of H's history, while a quick build of the affected tiles runs in a separate checkout
// when H's tile code or the data root's facades or layers differ from that base's (a list change alone needs none:
// a newly listed tile arrives as its builder's per-tile entry). Over the base it lays the newest per-tile entry of
// every listed tile the base has not absorbed or superseded (tile-add and the quick builds put them), as one composed
// store entry.
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

/** Whether a quick build can improve on `base` for main's `head`: its tile code differs, or the facades or layers it
 *  was built from (`delta`, tile_store.resolve) did. */
export function needsQuickBuild(base, head, delta) {
  return base.commit !== head || delta.includes('facade-approvals') || delta.includes('layers');
}

export class TileStore {
  constructor({ repo, worktree, published, stateDir, registry, build, log }) {
    Object.assign(this, { repo, worktree, published, stateDir, registry, build, log });
    this.selection = null;
    /** The release under the selection (never a composed entry): the next quick build starts from it. */
    this.base = null;
    this.checked = { signature: null, at: 0, entries: null };
    this.child = null;
    /** Keys a quick build ran for (its per-tile entries are in the store) or failed on: never built again. */
    this.attempted = new Set();
  }

  python(...args) {
    return run('python3', [path.join(this.worktree, 'scripts/dev/tile_store.py'), ...args,
      '--repo', this.worktree, '--published', this.published], { maxBuffer: 16 << 20 }).then(r => r.stdout.trim());
  }

  listed() {
    try { return JSON.parse(fs.readFileSync(path.join(this.published, 'playable-tiles.json'), 'utf8')).tiles; }
    catch { return ['t0_0_0', 't0_0_1', 't0_1_0', 't0_1_1']; }
  }

  /** Listed tiles the published ground layer has: a quick build cannot assemble the others (no layers yet). */
  buildable(tiles) {
    let ground;
    try { ground = JSON.parse(fs.readFileSync(path.join(this.published, 'build/layers/ground/MANIFEST.json'), 'utf8')).tiles; }
    catch { return tiles; }
    return tiles.filter(tile => `${tile}.vis.glb` in ground);
  }

  /** The selection for main's commit `head`: { dir, key, source, commit, base, overlay, building }. */
  async select(head, isAncestor) {
    const signature = [head, mtime(path.join(this.published, 'playable-tiles.json')),
      mtime(path.join(this.published, 'facade-approvals/assembly/MANIFEST.json')), fs.readlinkSync(path.join(this.published, 'live'))].join('|');
    const entries = mtime(path.join(this.published, 'tile-store/tiles/index.jsonl'));
    const fresh = signature === this.checked.signature && (this.base?.source === 'integrator' || Date.now() - this.checked.at < RECHECK_MS);
    if (fresh && entries === this.checked.entries) return this.selection;
    const at = fresh ? this.checked.at : Date.now();
    this.checked = { signature: null, at: 0, entries: null }; // a failed pass is retried
    const found = JSON.parse(await this.python('served', '--commit', head));
    const { base, served } = found;
    if (this.build && base.key !== found.key && !this.attempted.has(found.key) && needsQuickBuild(base, head, found.delta)) {
      const tiles = this.buildable(this.affected(base.commit, head, isAncestor));
      if (tiles.length) this.quickBuild(head, found.key, base.dir, tiles);
    }
    this.checked = { signature, at, entries };
    const next = served.source
      ? { ...base, dir: served.dir, key: served.key, source: 'composed', base: base.key, overlay: served.tiles }
      : { ...base };
    next.building = this.child?.tiles ?? null;
    if (next.dir !== this.selection?.dir) {
      this.log({ type: 'tiles', commit: head, source: next.source, key: next.key, dir: next.dir,
        ...(served.source ? { base: base.key, overlay: served.tiles } : {}), skipped: served.skipped });
    }
    this.base = base;
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

  /** One quick build at a time, in its own checkout so landings keep moving the served worktree meanwhile. Its
   *  changed tiles become per-tile entries (tile_store.py quick-build), which the next selection composes. */
  quickBuild(head, key, base, tiles) {
    if (this.child?.key === key) return;
    if (this.child) { this.halt(this.child); this.child = null; }
    const checkout = path.join(this.stateDir, 'build-repo');
    const work = path.join(this.stateDir, 'build-work');
    const started = Date.now();
    const prepare = fs.existsSync(path.join(checkout, '.git'))
      ? run('git', ['checkout', '--detach', '--force', '--quiet', head], { cwd: checkout })
      : run('git', ['worktree', 'add', '--detach', checkout, head], { cwd: this.repo });
    const child = { key, tiles, process: null };
    this.child = child;
    this.attempted.add(key);
    this.log({ type: 'quick-build', commit: head, key, tiles });
    void prepare.then(() => {
      if (this.child !== child) return;
      child.process = spawn('python3', [path.join(checkout, 'scripts/dev/tile_store.py'), 'quick-build', '--repo', checkout,
        '--commit', head, '--published', this.published, '--work', work, '--base', base, '--tiles', ...tiles],
      { stdio: ['ignore', 'pipe', 'pipe'], detached: true }); // its own process group: make and the assembler stop with it
      let output = '';
      child.process.stdout.on('data', d => { output += d; });
      child.process.stderr.on('data', d => { output += d; });
      child.process.on('exit', code => {
        if (this.child === child) this.child = null;
        this.checked.signature = null; // re-resolve on the next status pass
        let put = null;
        try { put = code ? null : JSON.parse(output.trim().split('\n').at(-1)).length; } catch { /* unparsable */ }
        this.log({ type: 'quick-build-done', commit: head, key, code, seconds: (Date.now() - started) / 1000, put,
          ...(code ? { output: output.slice(-2000) } : {}) });
      });
    }, (e) => { if (this.child === child) this.child = null; this.log({ type: 'quick-build-done', key, code: -1, output: String(e) }); });
  }

  halt(child) { try { if (child.process?.pid) process.kill(-child.process.pid, 'SIGTERM'); } catch { /* already gone */ } }

  stop() { if (this.child) this.halt(this.child); }
}
