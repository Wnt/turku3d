import type * as THREE from 'three/webgpu';

/** Detached resources wait for an entire subsequent frame, including nested shadows/reflections,
 * and for its submitted GPU work. New retirements during the wait belong to a later frame. */
export class FrameRetirement {
  private pending: (() => void)[] = [];
  retire(release: () => void) { this.pending.push(release); }
  async submitted(completed: () => Promise<void>) {
    if (!this.pending.length) return;
    const retired = this.pending;
    this.pending = [];
    try { await completed(); }
    finally { for (const release of retired) release(); }
  }
}

const queues = new WeakMap<THREE.WebGPURenderer, FrameRetirement>();
export function retireAfterFrame(renderer: THREE.WebGPURenderer, release: () => void) {
  let queue = queues.get(renderer);
  if (!queue) { queue = new FrameRetirement(); queues.set(renderer, queue); }
  queue.retire(release);
}
export function frameSubmitted(renderer: THREE.WebGPURenderer) {
  const device = (renderer.backend as { device?: GPUDevice }).device;
  void queues.get(renderer)?.submitted(() => device?.queue.onSubmittedWorkDone() ?? Promise.resolve());
}
