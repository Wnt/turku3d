import type * as THREE from 'three/webgpu';

type Retained = { usedTimes: number };
interface Pipelines {
  _releaseProgram(program: Retained): void;
  _releasePipeline(pipeline: Retained): void;
  dispose(): void;
}

/** r186 otherwise forgets a program as soon as its last draw leaves. Keep a bounded idle cache
 * of compiled code/pipelines, never render objects, bindings, tile geometry or textures. */
export function retainPrograms(pipelines: Pipelines, limit = 256) {
  const cleanups: (() => void)[] = [];
  for (const name of ['_releaseProgram', '_releasePipeline'] as const) {
    const release = pipelines[name].bind(pipelines), idle = new Set<Retained>();
    pipelines[name] = value => {
      idle.delete(value); idle.add(value);
      while (idle.size > limit) {
        const oldest = idle.values().next().value!;
        idle.delete(oldest);
        if (oldest.usedTimes === 0) release(oldest);
      }
    };
    cleanups.push(() => { for (const value of idle) if (value.usedTimes === 0) release(value); idle.clear(); });
  }
  const dispose = pipelines.dispose.bind(pipelines);
  pipelines.dispose = () => { for (const cleanup of cleanups) cleanup(); dispose(); };
}

export function retainRendererPrograms(renderer: THREE.WebGPURenderer) {
  retainPrograms((renderer as unknown as { _pipelines: Pipelines })._pipelines);
}
