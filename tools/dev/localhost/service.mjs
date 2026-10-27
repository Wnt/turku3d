// The localhost game service (tools/dev/localhost-game; README.md here). It keeps a checkout of main in a dedicated
// worktree, fast-forwarded within a second of every landing, and runs the game from it through the Vite dev server,
// so code landings reach the open tab by HMR (game/src/play.ts) or by an autosave + reload. The coarse tile service
// runs from the same worktree as a second supervised child (coarse.mjs). It writes state.json for the dev-server
// plugin (vitePlugin.mjs): main's commit, the integrator's verdict for it, the tile release the server streams under
// /tiles and the coarse service's commit and state.
import { execFile, execFileSync, spawn } from 'node:child_process';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import { port, workRoot } from '../environment.mjs';
import { CoarseService } from './coarse.mjs';
import { landingOf, readRows, runningStage, verdict } from './status.mjs';
import { TileStore } from './tiles.mjs';

const run = promisify(execFile);
const expand = (p) => path.resolve(p.replace(/^~(?=$|\/)/, os.homedir()));

/** The game's npm manifests (a change reinstalls and restarts the dev server); files the dev server itself loads.
 *  Tile builds use the shared pipeline environment (pipeline/env/run.sh), so the worktree needs only game/'s. */
const DEPENDENCIES = ['game/package.json', 'game/package-lock.json'];
const SERVER = ['game/vite.config.ts', 'tools/dev/environment.mjs', 'tools/dev/http.mjs', 'tools/dev/facadeCandidates.mjs',
  'tools/dev/localhost/vitePlugin.mjs'];

export function options(argv, env = process.env) {
  const opt = (name, def) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : def; };
  const wtRoot = expand(env.TURKU3D_WT_ROOT ?? '~/turku3d/wt');
  const data = expand(env.TURKU3D_DATA ?? '~/turku3d/data');
  const here = path.dirname(new URL(import.meta.url).pathname);
  const common = execFileSync('git', ['rev-parse', '--path-format=absolute', '--git-common-dir'], { cwd: here, encoding: 'utf8' }).trim();
  return {
    repo: expand(opt('--repo', path.dirname(common))),
    branch: opt('--branch', 'main'),
    worktree: expand(opt('--worktree', path.join(wtRoot, 'localhost/repo'))),
    registry: expand(opt('--registry', path.join(wtRoot, '.registry'))),
    published: expand(opt('--published', data)),
    store: expand(opt('--store', path.join(data, 'tile-store'))),
    stateDir: expand(opt('--state-dir', path.join(workRoot, 'localhost-game'))),
    telemetryDir: expand(opt('--telemetry-dir', path.join(workRoot, 'telemetry'))),
    host: opt('--host', '127.0.0.1'),
    port: Number(opt('--port', port(1))),
    pollMs: Number(opt('--poll-ms', 1000)),
    build: !argv.includes('--no-build'),
    coarse: !argv.includes('--no-coarse'),
    coarsePort: port(5),
  };
}

export class LocalhostGame {
  constructor(o) {
    this.o = o;
    this.statePath = path.join(o.stateDir, 'state.json');
    this.events = path.join(o.stateDir, 'events.jsonl');
    this.current = null;
    this.vite = null;
    this.stopping = false;
    this.ancestry = new Map();
    this.written = '';
    this.tiles = new TileStore({ ...o, log: (row) => this.log(row) });
    // The coarse service is localhost only; the dev server proxies /coarse/* to 127.0.0.1:<port base + 5>.
    this.coarse = o.coarse ? new CoarseService({
      command: () => ({ cmd: 'bash', args: [path.join(o.worktree, 'tools/dev/coarse-tiles'), '--port', String(o.coarsePort)],
        cwd: o.worktree, env: process.env }),
      head: () => this.current, host: '127.0.0.1', port: o.coarsePort, logFile: path.join(o.stateDir, 'coarse.log'),
      log: (row) => this.log(row), onState: () => { void this.write(); },
    }) : null;
  }

  git(cwd, ...args) { return run('git', args, { cwd, maxBuffer: 64 << 20 }).then(r => r.stdout.trim()); }

  log(row) {
    const line = JSON.stringify({ time: Date.now() / 1000, ...row });
    fs.appendFileSync(this.events, line + '\n');
    console.log(`localhost-game: ${line}`);
  }

  /** True when commit a is b or an ancestor of b (cached: commits are immutable). */
  isAncestor(a, b) {
    const key = `${a}:${b}`;
    if (!this.ancestry.has(key)) {
      let result = a === b;
      if (!result) {
        try { execFileSync('git', ['merge-base', '--is-ancestor', a, b], { cwd: this.o.repo, stdio: 'ignore' }); result = true; }
        catch { result = false; }
      }
      this.ancestry.set(key, result);
    }
    return this.ancestry.get(key);
  }

  async start() {
    await fsp.mkdir(this.o.stateDir, { recursive: true });
    this.lock();
    if (!fs.existsSync(path.join(this.o.worktree, '.git'))) {
      await fsp.mkdir(path.dirname(this.o.worktree), { recursive: true });
      await this.git(this.o.repo, 'worktree', 'add', '--detach', this.o.worktree, this.o.branch);
    }
    if (!fs.existsSync(path.join(this.o.worktree, 'game/node_modules/.package-lock.json'))) await this.install();
    this.current = await this.git(this.o.worktree, 'rev-parse', 'HEAD');
    await this.follow();
    await this.status();
    await this.selecting;
    this.startVite();
    void this.coarse?.start();
    // A landing moves refs/heads/<branch> (a lock file renamed into place) and appends to the ledger, a builder's
    // put-tile appends to the per-tile index: react at once; the poll covers packed refs and anything a watch misses.
    const refs = path.join(await this.git(this.o.repo, 'rev-parse', '--path-format=absolute', '--git-common-dir'), 'refs/heads');
    const entries = path.join(this.o.store, 'tiles');
    await fsp.mkdir(entries, { recursive: true });
    for (const dir of [refs, this.o.registry, entries]) if (fs.existsSync(dir)) fs.watch(dir, { persistent: false }, () => { void this.tick(); });
    this.timer = setInterval(() => { void this.tick(); }, this.o.pollMs);
  }

  lock() {
    const file = path.join(this.o.stateDir, 'service.pid');
    try {
      const pid = Number(fs.readFileSync(file, 'utf8'));
      if (pid && pid !== process.pid) { process.kill(pid, 0); throw new Error(`another localhost-game runs as pid ${pid} (${file})`); }
    } catch (e) { if (e.code !== 'ENOENT' && e.code !== 'ESRCH') throw e; }
    fs.writeFileSync(file, String(process.pid));
  }

  /** One follow + status pass; a call while one runs schedules another right after it (a landing is never missed). */
  async tick() {
    if (this.stopping) return;
    if (this.busy) { this.again = true; return; }
    this.busy = true;
    try {
      do {
        this.again = false;
        await this.follow();
        await this.status();
      } while (this.again && !this.stopping);
    } catch (e) {
      console.error(`localhost-game: ${e.stack ?? e}`);
    } finally { this.busy = false; }
  }

  /** Moves the worktree to main's head; dependency or dev-server changes restart Vite, a change to a file the coarse
   *  service loaded restarts it once its running builds finish (in the background: the follow loop goes on). */
  async follow() {
    const head = await this.git(this.o.repo, 'rev-parse', `refs/heads/${this.o.branch}`);
    if (head === this.current) return;
    const changed = (await this.git(this.o.repo, 'diff', '--name-only', this.current, head)).split('\n').filter(Boolean);
    await this.git(this.o.worktree, 'checkout', '--detach', '--force', '--quiet', head);
    const landing = landingOf(head, readRows(path.join(this.o.registry, 'landed.jsonl')));
    this.log({ type: 'checkout', commit: head, from: this.current, changed: changed.length,
      landed: landing?.time ?? null, task: landing?.name ?? null, class: landing?.class ?? null });
    this.current = head;
    if (changed.some(p => DEPENDENCIES.includes(p))) { await this.install(); this.restartVite(); }
    else if (changed.some(p => SERVER.includes(p))) this.restartVite();
    if (this.coarse && await this.coarse.affects(changed)) void this.coarse.restart();
  }

  async install() {
    const started = Date.now();
    await run('npm', ['ci', '--no-audit', '--no-fund'], { cwd: path.join(this.o.worktree, 'game'), maxBuffer: 64 << 20,
      env: { ...process.env, npm_config_cache: process.env.npm_config_cache ?? path.join(workRoot, 'cache/npm') } });
    this.log({ type: 'npm-ci', seconds: (Date.now() - started) / 1000 });
  }

  /** Recomputes the verdict and the tile release; writes state.json when either changed. */
  async status() {
    const live = (() => { try { return JSON.parse(fs.readFileSync(path.join(this.o.published, 'live/live-build.json'), 'utf8')).commit; } catch { return null; } })();
    const published = fs.realpathSync(this.o.published);
    const rows = readRows(path.join(this.o.registry, 'integrations.jsonl')).filter(r => !r.published || r.published === published);
    let running = null;
    try { running = JSON.parse(fs.readFileSync(path.join(this.o.registry, 'integration.json'), 'utf8')); } catch { /* none running */ }
    if (running?.log) running.stage = runningStage(running.log);
    const verification = verdict({ head: this.current, live, rows, running, isAncestor: (a, b) => this.isAncestor(a, b) });
    this.verification = verification;
    await this.write();
    // The tile release lookup (a Python key over the commit's tree) runs beside the follow loop, so a landing is
    // checked out and swapped in while it runs.
    this.selecting ??= this.tiles.select(this.current, (a, b) => this.isAncestor(a, b))
      .then(() => this.write(), (e) => console.error(`localhost-game: tiles: ${e.stack ?? e}`))
      .finally(() => { this.selecting = null; });
  }

  /** Writes state.json when it changed; writes from the follow loop and the tile lookup run one at a time. */
  write() {
    this.writing = (this.writing ?? Promise.resolve()).then(() => this.writeNow());
    return this.writing;
  }

  async writeNow() {
    const verification = this.verification, tiles = this.tiles.selection;
    const state = { commit: this.current, verification, tiles, coarse: this.coarse?.status ?? { commit: null, state: 'off' },
      store: this.o.store, events: this.events, worktree: this.o.worktree, port: this.o.port };
    const text = JSON.stringify(state, null, 2) + '\n';
    if (text === this.written) return;
    if (JSON.stringify(JSON.parse(this.written || '{}').verification) !== JSON.stringify(verification)) {
      this.log({ type: 'verification', commit: this.current, ...verification });
    }
    const temp = `${this.statePath}.${process.pid}.tmp`;
    await fsp.writeFile(temp, text);
    await fsp.rename(temp, this.statePath);
    this.written = text;
  }

  startVite() {
    const game = path.join(this.o.worktree, 'game');
    const out = fs.openSync(path.join(this.o.stateDir, 'vite.log'), 'a');
    this.vite = spawn(process.execPath, [path.join(game, 'node_modules/vite/bin/vite.js'), '--host', this.o.host, '--port', String(this.o.port), '--strictPort'], {
      cwd: game, stdio: ['ignore', out, out],
      env: { ...process.env, TURKU3D_LOCALHOST_STATE: this.statePath, TURKU3D_TELEMETRY_DIR: this.o.telemetryDir },
    });
    const child = this.vite;
    child.on('exit', (code, signal) => {
      fs.closeSync(out);
      if (this.vite !== child || this.stopping) return;
      this.log({ type: 'vite-exit', code, signal });
      setTimeout(() => { if (this.vite === child && !this.stopping) this.startVite(); }, 2000);
    });
    this.log({ type: 'vite-start', pid: child.pid, port: this.o.port });
  }

  /** Restarts a running dev server. Without one there is nothing to restart: before start() launches the first
   *  (a worktree behind main on startup) or while a restart waits for the old one to exit; starting another then
   *  would leave two servers racing for the port. */
  restartVite() {
    const old = this.vite;
    if (!old) return;
    this.vite = null;
    old.once('exit', () => { if (!this.stopping) this.startVite(); });
    old.kill('SIGTERM');
  }

  stop() {
    this.stopping = true;
    clearInterval(this.timer);
    this.tiles.stop();
    this.coarse?.stop();
    this.vite?.kill('SIGTERM');
    fs.rmSync(path.join(this.o.stateDir, 'service.pid'), { force: true });
  }
}

if (process.argv[1] && fs.realpathSync(process.argv[1]) === fs.realpathSync(new URL(import.meta.url).pathname)) {
  const service = new LocalhostGame(options(process.argv.slice(2)));
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => process.exit(0));
  // Never leave the dev server or the coarse service behind: also on an uncaught error.
  process.on('exit', () => service.stop());
  service.start().then(() => console.log(`localhost-game: http://${service.o.host}:${service.o.port}/?tiles=/tiles/tileset.json`
    + ` follows ${service.o.branch} in ${service.o.worktree}`), (e) => { console.error(e); process.exit(1); });
}
