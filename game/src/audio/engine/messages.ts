import type { ChassisFrame } from './chassis';
import type { TyreFrame } from './tyre';
import type { V8Params } from './v8core';

/** Main thread ↔ v8.worklet.ts contract (kept apart so the main bundle never loads the worklet module). */
export const V8_PROCESSOR = 'turku-v8';
export interface V8ProcessorOptions { seed: number; partials: 16 | 32; running: boolean }
/** Posted once per frame per voice; `'stop'` ends the processor. */
export interface V8Message { p: V8Params; c?: ChassisFrame }

/** Main thread ↔ tyre.worklet.ts: the tyre voice of a car or scooter. One `TyreFrame` per frame; `'stop'` ends it. */
export const TYRE_PROCESSOR = 'turku-tyre';
export interface TyreProcessorOptions { seed: number; pitch: number; roll: number }
export type TyreMessage = TyreFrame | 'stop';
