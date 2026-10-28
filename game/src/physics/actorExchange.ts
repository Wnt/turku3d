/** Cumulative actor deltas. A final sleeping pose or removal stays pending until the renderer acknowledges it,
 * so skipping any number of shared snapshots cannot lose an update. Sources reuse unchanged pose objects. */
export class ActorUpdates<T extends { id?: string }> {
  private entries = new Map<string, { value: T | null; revision: number }>();
  sample(values: readonly T[], revision: number, acknowledged: number) {
    const present = new Set<string>();
    for (const value of values) {
      const id = value.id ?? 'player'; present.add(id);
      if (this.entries.get(id)?.value !== value) this.entries.set(id, { value, revision });
    }
    for (const [id, old] of this.entries) {
      if (!present.has(id) && old.value !== null) this.entries.set(id, { value: null, revision });
    }
    const updates: T[] = [], removed: string[] = [];
    for (const [id, entry] of this.entries) {
      if (entry.revision > acknowledged) {
        if (entry.value) updates.push(entry.value); else removed.push(id);
      } else if (entry.value === null) this.entries.delete(id);
    }
    return { updates, removed };
  }
}

/** Stable resting poses are retained on the main thread rather than decoded again every frame. */
export class ActorCache<T extends { id?: string }> {
  private entries = new Map<string, T>();
  private values: T[] = [];
  apply(updates: T[], removed: string[] = [], delta = true): T[] {
    if (!delta) this.entries.clear();
    for (const id of removed) this.entries.delete(id);
    for (const value of updates) this.entries.set(value.id ?? 'player', value);
    if (!delta || updates.length || removed.length) this.values = [...this.entries.values()];
    return this.values;
  }
}
