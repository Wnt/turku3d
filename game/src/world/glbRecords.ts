// Bound structured-clone decoding as well as JSON parsing: one giant worker reply still stalls the UI.
export type JsonRecord = [parent: number, key: string | number, kind: 0 | 1 | 2, value: unknown];
export function* jsonRecords(root: object): Generator<JsonRecord> {
  let serial = 0;
  function* visit(object: object, parent: number): Generator<JsonRecord> {
    for (const [key, value] of Object.entries(object)) {
      if (value !== null && typeof value === 'object') {
        const id = ++serial;
        yield [parent, key, Array.isArray(value) ? 2 : 1, id];
        yield* visit(value, id);
      } else yield [parent, key, 0, value];
    }
  }
  yield* visit(root, 0);
}
export function applyJsonRecords(objects: Record<string, unknown>[], records: JsonRecord[]) {
  for (const [parent, key, kind, value] of records) {
    const item = kind ? (kind === 2 ? [] : {}) : value;
    if (kind) objects[value as number] = item as Record<string, unknown>;
    // Match JSON.parse semantics for arbitrary metadata keys without invoking Object.prototype setters.
    Object.defineProperty(objects[parent], key, { value: item, writable: true, enumerable: true, configurable: true });
  }
}

/** Top-level scalars and the keys of top-level objects: enough to check a document without delivering it. */
export interface JsonPeek { scalars: Record<string, unknown>; keys: Record<string, string[]> }
export function peekJson(root: unknown): JsonPeek {
  const peek: JsonPeek = { scalars: {}, keys: {} };
  if (root === null || typeof root !== 'object' || Array.isArray(root)) return peek;
  for (const [key, value] of Object.entries(root)) {
    if (value === null || typeof value !== 'object') peek.scalars[key] = value;
    else if (!Array.isArray(value)) peek.keys[key] = Object.keys(value);
  }
  return peek;
}
