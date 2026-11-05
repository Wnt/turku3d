import type { ChassisFrame } from './chassis';
import type { V8Params } from './v8core';

/** Main thread ↔ v8.worklet.ts contract (kept apart so the main bundle never loads the worklet module). */
export const V8_PROCESSOR = 'turku-v8';
export interface V8ProcessorOptions { seed: number; partials: 16 | 32; running: boolean }
/** Posted once per frame per voice; `'stop'` ends the processor. */
export interface V8Message { p: V8Params; c?: ChassisFrame }
