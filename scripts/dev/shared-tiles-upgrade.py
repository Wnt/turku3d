#!/usr/bin/env python3
"""Upgrade this loop's farm daemon, preserving and adopting its running build processes."""
import argparse
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import time
from urllib.request import urlopen

from shared_tiles import atomic_json, process_alive, process_ticks, read_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--script', type=Path, required=True)
    parser.add_argument('--runner', type=Path, required=True)
    parser.add_argument('--host', default='192.168.1.114')
    parser.add_argument('--port', default=7950, type=int)
    args = parser.parse_args()
    root = args.root.resolve()
    pid = int((root / 'service.pid').read_text())
    command = Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode()
    if str(root) not in command or 'shared_tiles.py' not in command:
        raise RuntimeError('PID is not this farm daemon')
    url = f'http://{args.host}:{args.port}/health'
    state = json.load(urlopen(url, timeout=5))
    stopped = False
    try:
        # Freeze only the scheduler for this short handoff; child build sessions keep running.
        os.kill(pid, signal.SIGSTOP)
        stopped = True
        pending = {(r['generation'], r['tile']): r for r in read_json(root / 'pending.json', [])}
        adopted = []
        for process in Path('/proc').iterdir():
            if not process.name.isdigit():
                continue
            try:
                status = (process / 'status').read_text()
                parent = next(x.split()[1] for x in status.splitlines() if x.startswith('PPid:'))
                if int(parent) != pid:
                    continue
                argv = (process / 'cmdline').read_bytes().decode().strip('\0').split('\0')
                if '--out' not in argv or 'turku3d.coarse' not in argv:
                    continue
                out = Path(argv[argv.index('--out') + 1])
                if not out.is_relative_to(root / 'products'):
                    raise RuntimeError('worker output is outside own farm')
                key = (out.parent.name, out.name)
                if key not in pending:
                    continue
                child = int(process.name)
                pending[key].update(pid=child, start_ticks=process_ticks(child))
                adopted.append(child)
            except (FileNotFoundError, ProcessLookupError):
                continue
        atomic_json(root / 'pending.json', list(pending.values()))
        os.kill(pid, signal.SIGTERM)
        os.kill(pid, signal.SIGCONT)
        stopped = False
        deadline = time.monotonic() + 5
        while process_alive(pid) and time.monotonic() < deadline:
            time.sleep(.05)
        if process_alive(pid):
            raise RuntimeError('old daemon did not exit; queued handoff is saved')
        with (root.parent / 'service.log').open('a') as log:
            proc = subprocess.Popen(['python3', str(args.script), '--root', str(root),
                                     '--runner', str(args.runner), '--host', args.host,
                                     '--port', str(args.port), '--workers', str(state['workers'])],
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        print(json.dumps(dict(daemon=proc.pid, adopted=adopted, pending=len(pending))), flush=True)
    finally:
        if stopped and process_alive(pid):
            os.kill(pid, signal.SIGCONT)


if __name__ == '__main__':
    main()
