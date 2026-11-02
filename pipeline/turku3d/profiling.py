"""Inclusive stage CPU and named file-lock wait counters; never a shared profiling lock."""
import fcntl
import os
from pathlib import Path
import resource
import threading
import time

_local = threading.local()
_TICKS = os.sysconf('SC_CLK_TCK')


def cpu_seconds():
    """Self + reaped children + live descendants (including a persistent Node packager).

    Linux child accounting moves into cutime/cstime when reaped, so each process
    tree contributes once. Other platforms report self and reaped children only.
    """
    own = resource.getrusage(resource.RUSAGE_SELF)
    reaped = resource.getrusage(resource.RUSAGE_CHILDREN)
    total = own.ru_utime + own.ru_stime + reaped.ru_utime + reaped.ru_stime
    def descendants(pid):
        subtotal = 0.0
        try:
            children = Path(f'/proc/{pid}/task/{pid}/children').read_text().split()
        except FileNotFoundError:
            return subtotal
        for child in children:
            try:
                fields = Path(f'/proc/{child}/stat').read_text().rsplit(')', 1)[1].split()
                subtotal += sum(float(fields[i]) for i in (11, 12, 13, 14)) / _TICKS
                subtotal += descendants(child)
            except FileNotFoundError:
                pass  # A codec may finish between the two reads.
        return subtotal
    return total + descendants(os.getpid())


def lock_totals():
    return dict(getattr(_local, 'locks', {}))


def flock(handle, operation, category):
    started = time.perf_counter()
    try:
        return fcntl.flock(handle, operation)
    finally:
        totals = getattr(_local, 'locks', None)
        if totals is None:
            totals = _local.locks = {}
        totals[category] = totals.get(category, 0.0) + time.perf_counter() - started


def snapshot():
    return cpu_seconds(), lock_totals()


def elapsed(before):
    cpu, locks = before
    waits = {name: round(value - locks.get(name, 0.0), 6)
             for name, value in lock_totals().items() if value > locks.get(name, 0.0)}
    return dict(cpu_seconds=round(max(0.0, cpu_seconds() - cpu), 6),
                lock_wait_seconds=round(sum(waits.values()), 6), lock_wait_by_kind=waits)
