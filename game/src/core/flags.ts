/** Temporary registered A/B experiments only. Read using featureFlag('literal_name'). */
export function featureFlag(name: string): boolean {
  return new URLSearchParams(globalThis.location?.search ?? '').get(`ab.${name}`) === '1';
}
