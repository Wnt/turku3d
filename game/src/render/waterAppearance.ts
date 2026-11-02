/** Per-body sRGB measurements; missing imagery retains the unmeasured-water fallback. */
export function waterColour(data: Record<string, unknown>): [number, number, number] | undefined {
  const value = data.water_color;
  return Array.isArray(value) && value.length === 3 && value.every(v => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 1)
    ? value as [number, number, number] : undefined;
}

export function waterSharingKey(plane: string, data: Record<string, unknown>): string {
  return JSON.stringify([plane, data.water_body ?? null, waterColour(data) ?? null]);
}
