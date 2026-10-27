// The coarse tile service (tools/dev/coarse-tiles, pipeline/turku3d/coarse/service.py) as a supervised child of the
// localhost game (README.md here): started from the followed worktree, restarted when a landing changes a file it
// loaded, after its running builds finish (POST /coarse/drain, at most `drainMs`), and after a crash with a backoff.
// It runs in its own process group, so a restart or the service's exit also stops the builds it spawned.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';

/** Changes that restart the service whatever it reports loading: its launcher and the pipeline environment. */
const LAUNCHER = ['tools/dev/coarse-tiles', 'pipeline/env/'];
/** Before the service has reported its modules (`/coarse/status` `modules`): anything it could import. */
const ANY_MODULE = 'pipeline/turku3d/';
const RETRY_PORT_MS = 10_000;

/** Whether `port` on `host` can be bound now (no other coarse service, for example a hand-started one, holds it). */
export function portFree(host, port) {
  return new Promise((resolve) => {
    const probe = net.createServer();
    probe.once('error', () => resolve(false));
    probe.listen(port, host, () => probe.close(() => resolve(true)));
  });
}

/** Whether a landing that changed `changed` (repo-relative paths) reaches a process that loaded `modules` (or null:
 *  not known yet). */
export function affects(changed, modules) {
  return changed.some(p => LAUNCHER.some(l => l.endsWith('/') ? p.startsWith(l) : p === l)
    || (modules ? modules.includes(p) : p.startsWith(ANY_MODULE)));
}

export class CoarseService {
  /**
   * `command()`: { cmd, args, cwd, env } of the service (read at every start, so it runs the worktree's files then);
   * `head()`: the commit the worktree is at; `onState(status)`: called on every state change with { commit, state }.
   */
  constructor({ command, head, host, port, logFile, log, onState = () => {}, drainMs = 60_000, pollMs = 500,
    backoffMs = 2000 }) {
    Object.assign(this, { command, head, host, port, logFile, log, onState, drainMs, pollMs, backoffMs });
    this.child = null;
    this.status = { commit: null, state: 'off' };
    this.modules = null;
    this.failures = 0;
    this.stopping = false;
    this.restarting = null;
    this.timer = null;
  }

  get url() { return `http://${this.host}:${this.port}/coarse/`; }

  set(state, extra = {}) {
    const status = { commit: this.status.commit, state };
    if (status.state === this.status.state && status.commit === this.status.commit && !Object.keys(extra).length) return;
    this.status = status;
    this.log({ type: 'coarse', commit: status.commit, state, ...extra });
    this.onState(status);
  }

  async start() {
    clearTimeout(this.timer);
    if (this.stopping || this.child) return;
    if (!await portFree(this.host, this.port)) {
      this.set('port in use', this.status.state === 'port in use' ? {} : { port: this.port });
      this.timer = setTimeout(() => { void this.start(); }, RETRY_PORT_MS);
      return;
    }
    if (this.stopping || this.child) return;
    const { cmd, args, cwd, env } = this.command();
    const out = fs.openSync(this.logFile, 'a');
    const child = spawn(cmd, args, { cwd, env, stdio: ['ignore', out, out], detached: true });
    fs.closeSync(out);
    this.child = child;
    this.modules = null;
    this.status = { ...this.status, commit: this.head() };
    const started = Date.now();
    this.set('starting', { pid: child.pid });
    child.on('exit', (code, signal) => {
      child.exited = true;
      if (this.child !== child) return; // a restart or stop() took it down
      this.child = null;
      this.failures = Date.now() - started > 60_000 ? 1 : this.failures + 1;
      const delay = Math.min(60_000, this.backoffMs * 2 ** (this.failures - 1));
      this.set('exited', { code, signal, retry: delay / 1000 });
      if (!this.stopping) this.timer = setTimeout(() => { void this.start(); }, delay);
    });
    child.on('error', () => {}); // a failed spawn also exits
    while (this.child === child && !this.stopping) {
      const status = await this.get('status');
      if (this.child !== child) return;
      if (status) {
        this.modules = status.modules ?? null;
        this.set('running');
        return;
      }
      await new Promise(r => setTimeout(r, this.pollMs));
    }
  }

  /** The service's JSON answer to `path`, or null (down, starting, or not answering in time). */
  async get(path, init = {}) {
    try {
      const res = await fetch(this.url + path, { ...init, signal: AbortSignal.timeout(2000) });
      return res.ok ? await res.json() : null;
    } catch { return null; }
  }

  /** Whether a landing that changed `changed` reaches the running service; its loaded files are re-read first. */
  async affects(changed) {
    if (this.child && this.status.state === 'running') {
      const status = await this.get('status');
      if (status?.modules) this.modules = status.modules;
    }
    return affects(changed, this.modules);
  }

  /** Drains the running service, stops it and starts it again from the worktree. A call while the service drains
   *  joins that restart (its start reads the newest checkout); one after the new process spawned restarts again. */
  restart() {
    if (this.restarting) return this.restarting;
    this.restarting = this.drainAndStop().then(() => {
      this.restarting = null;
      return this.start();
    });
    return this.restarting;
  }

  async drainAndStop() {
    clearTimeout(this.timer);
    const child = this.child;
    if (!child) return;
    this.set('draining');
    const until = Date.now() + this.drainMs;
    // A service without the drain route (older code) keeps starting builds; the deadline bounds the wait then.
    await this.get('drain', { method: 'POST' });
    while (Date.now() < until && !child.exited && !this.stopping) {
      const status = await this.get('status');
      if (!status || !status.building?.length) break;
      await new Promise(r => setTimeout(r, this.pollMs));
    }
    this.set('restarting');
    this.child = null;
    await this.kill(child);
  }

  /** SIGTERM to the child's process group (its builds too), SIGKILL after 5 s. */
  kill(child) {
    if (child.exited) return Promise.resolve();
    return new Promise((resolve) => {
      const force = setTimeout(() => signal(child, 'SIGKILL'), 5000);
      child.once('exit', () => { clearTimeout(force); resolve(); });
      signal(child, 'SIGTERM');
    });
  }

  /** Synchronous, so it also runs from process.on('exit'). */
  stop() {
    this.stopping = true;
    clearTimeout(this.timer);
    const child = this.child;
    this.child = null;
    if (child && !child.exited) signal(child, 'SIGTERM');
  }
}

function signal(child, name) {
  try { process.kill(-child.pid, name); } catch { /* already gone */ }
}
