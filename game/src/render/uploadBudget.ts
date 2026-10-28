/** Largest pending uploads first. A single texture is atomic in three.js, so oversized jobs run alone. */
export class UploadBudget {
  private jobs: { bytes: number; run: () => void; resolve: () => void; reject: (error: unknown) => void }[] = [];
  constructor(readonly maxBytes = 2 * 1024 ** 2, readonly maxMs = 2) {}
  get pending() { return this.jobs.length; }
  enqueue(bytes: number, run: () => void): Promise<void> {
    return new Promise((resolve, reject) => { this.jobs.push({ bytes, run, resolve, reject }); });
  }
  drain(now: () => number = () => performance.now()) {
    const start = now();
    let bytes = 0, count = 0;
    this.jobs.sort((a, b) => b.bytes - a.bytes);
    while (this.jobs.length) {
      if (count && (bytes + this.jobs[0].bytes > this.maxBytes || now() - start >= this.maxMs)) break;
      const job = this.jobs.shift()!;
      bytes += job.bytes; count++;
      try { job.run(); job.resolve(); } catch (error) { job.reject(error); }
    }
  }
}
