import { ChassisSynth } from './chassis';
import { V8_PROCESSOR, type V8Message, type V8ProcessorOptions } from './messages';
import { createV8, render, type V8Params } from './v8core';

/** AudioWorklet wrapper around the pure V8 and chassis DSP: one processor per audible truck. The main thread posts
 *  `{ p: V8Params, c?: ChassisFrame }` once per frame; each 128-sample quantum renders both into the mono output. */

declare const sampleRate: number;
declare function registerProcessor(name: string, ctor: unknown): void;
declare class AudioWorkletProcessor { readonly port: MessagePort; constructor(); }

class V8Processor extends AudioWorkletProcessor {
  private readonly voice;
  private readonly chassis;
  private params: V8Params;
  private extra = new Float32Array(128);
  private alive = true;

  constructor(options: { processorOptions: V8ProcessorOptions }) {
    super();
    const o = options.processorOptions;
    this.voice = createV8(o.seed, sampleRate, { partials: o.partials, running: o.running });
    this.chassis = new ChassisSynth(o.seed + 1, sampleRate);
    this.params = { rpm: 930, load: 0, throttle: 0, limiter: false, shifting: false, running: o.running, cockpit: false, partials: o.partials };
    this.port.onmessage = (event: MessageEvent<V8Message | 'stop'>) => {
      if (event.data === 'stop') { this.alive = false; return; }
      this.params = event.data.p;
      if (event.data.c) this.chassis.frame(event.data.c);
    };
  }

  process(_inputs: Float32Array[][], outputs: Float32Array[][]) {
    const out = outputs[0]?.[0];
    if (!out) return this.alive;
    if (this.extra.length !== out.length) this.extra = new Float32Array(out.length);
    render(this.voice, this.params, out, sampleRate);
    this.extra.fill(0); this.chassis.render(this.extra);
    for (let i = 0; i < out.length; i++) out[i] += this.extra[i];
    for (let c = 1; c < outputs[0].length; c++) outputs[0][c].set(out);
    return this.alive;
  }
}

registerProcessor(V8_PROCESSOR, V8Processor);
