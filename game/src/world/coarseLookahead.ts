/** Stable selection and one speculative task; core streaming never waits on this slot. */
export class CoarseLookahead {
  private candidate?: string;
  private task?: { id: string };

  select(candidate?: string) {
    // Consecutive motion checks reject placement velocity and brief heading changes.
    const selected = candidate && candidate === this.candidate
      && (!this.task || this.task.id === candidate) ? candidate : undefined;
    this.candidate = candidate;
    return selected;
  }

  run(id: string, operation: () => Promise<void> | null) {
    if (this.task) return;
    const task = this.task = { id };
    const pending = operation();
    if (!pending) { this.task = undefined; return; }
    void pending.finally(() => { if (this.task === task) this.task = undefined; });
  }
}
