/** Blocked storage degrades to in-memory save slots; browsers can reject even the property access. */
export function browserStorage(): Storage | null {
  try { return window.localStorage; } catch { return null; }
}

export function sessionStore(): Storage | null {
  try { return window.sessionStorage; } catch { return null; }
}
