import { DT, MAX_SUBSTEPS } from './constants';

/** Single-writer shared mailbox. Odd sequence = being written; readers retry on the next frame rather than wait.
 * UTF-8 snapshots retain the full protocol (variable vehicle/pole lists and stable string IDs). */
export class SharedMailbox<T> {
  readonly buffer: SharedArrayBuffer;
  private header: Int32Array;
  private bytes: Uint8Array;
  private encoder = new TextEncoder();
  private decoder = new TextDecoder();
  private seen = 0;
  constructor(buffer: SharedArrayBuffer) {
    this.buffer = buffer;
    this.header = new Int32Array(buffer, 0, 2);
    this.bytes = new Uint8Array(buffer, 8);
  }
  static create<T>(capacity: number) { return new SharedMailbox<T>(new SharedArrayBuffer(capacity + 8)); }
  get publishedBytes() { return Atomics.load(this.header, 1); }
  write(value: T) {
    const encoded = this.encoder.encode(JSON.stringify(value));
    if (encoded.length > this.bytes.length) throw new Error(`physics mailbox overflow: ${encoded.length}/${this.bytes.length}`);
    Atomics.add(this.header, 0, 1);
    this.bytes.set(encoded);
    Atomics.store(this.header, 1, encoded.length);
    Atomics.add(this.header, 0, 1);
  }
  read(): T | null {
    const seq = Atomics.load(this.header, 0);
    if (seq === this.seen || (seq & 1)) return null;
    const length = Atomics.load(this.header, 1);
    const copy = this.bytes.slice(0, length);
    if (seq !== Atomics.load(this.header, 0)) return null;
    this.seen = seq;
    return JSON.parse(this.decoder.decode(copy)) as T;
  }
}

/** Worker wall clock; discard suspended-tab debt and run at most five fixed steps per turn. */
export class PhysicsClock {
  private last: number | null = null;
  advance(now: number, running: boolean) {
    const dt = this.last === null || !running ? 0 : Math.min(MAX_SUBSTEPS * DT, Math.max(0, (now - this.last) / 1000));
    this.last = running ? now : null;
    return dt;
  }
}
