"""Persistent warm generator workers for the bulk farm (``shared_tiles.Farm``'s execute hook).

A one-shot build pays its interpreter start, imports, prepared-input key closures, area indexes
(``turku3d.spatial``) and node packager for every tile: about 3-4 CPU-s of a 9-25 s suburb tile on
the EPYC. Each concurrent build instead takes an idle ``python -m turku3d.coarse worker`` of its
revision and generation, sends it one tile and reads one result line. Products are byte-identical
to one-shot builds. A worker is replaced after ``max_builds`` tiles, when its resident memory
exceeds ``max_rss_gib`` after a tile, and after any failure, timeout or disk-floor stop. A worker
retains about 0.5 GiB across tiles (peak 2.1-2.3 GiB after t0_0_0, t0_-7_7 and t0_-5_4, against
1.8 GiB one-shot), so replacing it above 2 GiB keeps heavy tiles within the bulk admission
reservation per worker.
"""
import json
import os
import select
import signal
import subprocess
import threading
import time
from pathlib import Path

GIB = 2**30


def tree_cpu(pid):
    """CPU seconds of ``pid`` and its live descendants, including their reaped children."""
    total, stack, seen = 0, [pid], set()
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        try:
            fields = Path(f'/proc/{p}/stat').read_text().rsplit(')', 1)[1].split()
            total += sum(int(fields[i]) for i in (11, 12, 13, 14))
            for task in Path(f'/proc/{p}/task').iterdir():
                stack += [int(child) for child in (task / 'children').read_text().split()]
        except (FileNotFoundError, ProcessLookupError, ValueError):
            pass
    return total / os.sysconf('SC_CLK_TCK')


def resident(pid):
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return int(line.split()[1]) * 1024
    except FileNotFoundError:
        pass
    return 0


class Worker:
    def __init__(self, farm, runner, revision, root, number):
        repo, env = farm.build_environment(revision, root)
        logs = farm.root / 'logs'
        logs.mkdir(exist_ok=True)
        self.log_path = logs / f'{revision}-worker-{number}.log'
        with self.log_path.open('a') as log:
            self.proc = subprocess.Popen([str(runner), 'python', '-m', 'turku3d.coarse', 'worker'], cwd=repo, env=env,
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True,
                                         start_new_session=True)
        self.key, self.builds = (revision, str(root)), 0

    def build(self, tile, out, abort):
        """The worker's result for one tile; ``abort()`` returns a reason to stop it, polled every 2 s."""
        self.proc.stdin.write(json.dumps({'tile': tile, 'out': str(out)}) + '\n')
        self.proc.stdin.flush()
        self.builds += 1
        while True:
            ready, _, _ = select.select([self.proc.stdout], [], [], 2)
            if ready:
                line = self.proc.stdout.readline()
                if not line:
                    raise RuntimeError(f'generator worker exit {self.proc.wait()}; see {self.log_path.name}')
                return json.loads(line)
            reason = abort()
            if reason:
                self.stop()
                raise RuntimeError(reason)

    def stop(self):
        if self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait()
        self._release()

    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            self.proc.wait()
        self._release()

    def _release(self):
        for stream in (self.proc.stdin, self.proc.stdout):
            if not stream.closed:
                stream.close()


class WarmWorkers:
    def __init__(self, farm, runner, *, max_builds=40, max_rss_gib=2.0, timeout=1800):
        self.farm, self.runner = farm, runner
        self.max_builds, self.max_rss, self.timeout = max_builds, max_rss_gib * GIB, timeout
        self.idle, self.lock, self.started, self.busy = [], threading.Lock(), 0, 0

    def _take(self, revision, root):
        key = (revision, str(root))
        with self.lock:
            self.busy += 1
            for i, worker in enumerate(self.idle):
                if worker.key == key and worker.proc.poll() is None:
                    return self.idle.pop(i)
            self.started += 1
            number = self.started
        try:
            return Worker(self.farm, self.runner, revision, root, number)
        except BaseException:
            with self.lock:
                self.busy -= 1
            raise

    def _give(self, worker, healthy):
        with self.lock:
            self.busy -= 1
            keep = (healthy and worker.proc.poll() is None and worker.builds < self.max_builds
                    and resident(worker.proc.pid) < self.max_rss)
            if keep:
                self.idle.append(worker)
            # Idle workers keep their memory: never more workers than the farm admits builds.
            surplus = max(0, self.busy + len(self.idle) - max(1, self.farm.workers))
            retired, self.idle = self.idle[:surplus], self.idle[surplus:]
        for idle in retired:
            idle.close()
        if not keep:
            if healthy:
                worker.close()
            else:
                worker.stop()

    def __call__(self, revision, tile, root):
        """``Farm.execute``: build ``tile`` of ``revision`` into ``root / tile`` on a warm worker."""
        info = self.farm.queue_info.get((root.name, tile), {})
        if info.get('pid'):  # a recorded one-shot build from before a restart: adopt it
            return self.farm.run_build(revision, tile, root)
        deadline = time.monotonic() + self.timeout

        def abort():
            if self.farm.free() < self.farm.floor + .5 * GIB:
                return 'disk floor protection'
            if time.monotonic() > deadline:
                return 'build timeout'
            if info.get('cancelled'):
                return 'cancelled'
            return None

        worker = self._take(revision, root)
        healthy = False
        try:
            cpu = tree_cpu(worker.proc.pid)
            result = worker.build(tile, root / tile, abort)
            cpu = round(tree_cpu(worker.proc.pid) - cpu, 3)
            if result.get('code') != 0:
                message = result.get('stderr') or result.get('stdout') or 'generator worker failure'
                # One log per tile, as a one-shot build leaves it (failure classification reads it).
                with (self.farm.root / 'logs' / f'{revision}-{tile}.log').open('a') as log:
                    log.write(f'{message}\nworker log: {worker.log_path.name}\n')
                raise RuntimeError(f'generator exit {result.get("code")}: {message}'[-1000:])
            healthy = True
            return dict(returncode=0, worker=worker.proc.pid, worker_builds=worker.builds, cpu_seconds=cpu,
                        worker_rss_gib=round(resident(worker.proc.pid) / GIB, 3))
        finally:
            self._give(worker, healthy)

    def close(self):
        """Let every idle worker finish and exit; their CPU then counts as reaped children."""
        with self.lock:
            idle, self.idle = self.idle, []
        for worker in idle:
            worker.close()
