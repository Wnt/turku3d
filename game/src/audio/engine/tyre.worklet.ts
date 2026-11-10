import { TYRE_PROCESSOR, type TyreMessage, type TyreProcessorOptions } from './messages';
import { TyreSynth } from './tyre';

/** AudioWorklet wrapper around the tyre synthesis: one processor per audible car or scooter (the truck's tyres render
 *  inside its engine voice). The main thread posts one `TyreFrame` per frame; an idle voice renders silence for free. */

declare const sampleRate: number;
declare function registerProcessor(name: string, ctor: unknown): void;
declare class AudioWorkletProcessor { readonly port: MessagePort; constructor(); }

class TyreProcessor extends AudioWorkletProcessor {
  private readonly synth;
  private alive = true;

  constructor(options: { processorOptions: TyreProcessorOptions }) {
    super();
    const o = options.processorOptions;
    this.synth = new TyreSynth(sampleRate, o.seed, { pitch: o.pitch, roll: o.roll });
    this.port.onmessage = (event: MessageEvent<TyreMessage>) => {
      if (event.data === 'stop') this.alive = false; else this.synth.frame(event.data);
    };
  }

  process(_inputs: Float32Array[][], outputs: Float32Array[][]) {
    const out = outputs[0]?.[0];
    if (!out) return this.alive;
    this.synth.render(out);
    for (let c = 1; c < outputs[0].length; c++) outputs[0][c].set(out);
    return this.alive;
  }
}

registerProcessor(TYRE_PROCESSOR, TyreProcessor);
