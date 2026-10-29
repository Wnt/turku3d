/** The service did not answer (connection error, or the dev server's 502-504 for it): it is restarting. */
export class ServiceDown extends Error { constructor(readonly retryAfter = 0) { super('draft service temporarily unavailable'); } }
export const gatewayDown = (status: number) => status === 408 || status >= 500;
export const backoff = (attempt: number) => Math.min(30_000, 2000 * 2 ** Math.min(attempt, 4)) * (0.75 + Math.random() * 0.5);
export const retryAfter = (response: Response) => {
  const value = response.headers?.get('Retry-After');
  if (!value) return 0;
  const seconds = Number(value);
  return Number.isFinite(seconds) ? Math.max(0, seconds * 1000) : Math.max(0, Date.parse(value) - Date.now()) || 0;
};

