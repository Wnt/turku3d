const POLE_FINE = 50;

/** The wallet cannot go into debt; the hourly score records the full penalty. */
export function applyPoleFine(wallet: number): number {
  return Math.max(0, wallet - POLE_FINE);
}

export class LinePool {
  private remaining: number[] = [];
  private previous = -1;
  constructor(private readonly count: number, private readonly random: () => number = Math.random) {
    if (count < 2) throw new Error('line pool needs at least two lines');
  }
  next(): number {
    if (!this.remaining.length) {
      this.remaining = Array.from({ length: this.count }, (_, i) => i);
      if (this.previous >= 0) this.remaining.splice(this.previous, 1);
    }
    const at = Math.min(this.remaining.length - 1, Math.floor(this.random() * this.remaining.length));
    const [choice] = this.remaining.splice(at, 1);
    this.previous = choice;
    return choice;
  }
}
