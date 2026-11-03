// GPU completion epochs from queue.onSubmittedWorkDone(): a slot freed now may be reused once a fence issued after
// the free has resolved, i.e. the GPU finished every submission that could still read it. One fence in flight at a
// time. Without onSubmittedWorkDone (unit tests), every epoch counts as done.
export function gpuFence(device: GPUDevice) {
  const queue = device.queue as GPUQueue & { onSubmittedWorkDone?: () => Promise<undefined> };
  let issued = 0, done = 0, waiting = false;
  return {
    /** The epoch whose fence must resolve before something freed now is reusable. */
    after: () => issued + 1,
    done: (epoch: number) => typeof queue.onSubmittedWorkDone !== 'function' || done >= epoch,
    /** Issue the next fence unless one is in flight. */
    request() {
      if (waiting || typeof queue.onSubmittedWorkDone !== 'function') return;
      waiting = true;
      const id = ++issued;
      queue.onSubmittedWorkDone().then(() => { done = id; waiting = false; }, () => { waiting = false; });
    },
  };
}
