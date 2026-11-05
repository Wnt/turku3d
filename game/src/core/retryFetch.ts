/** Retries of transient fetch failures: a gateway answer (502, 503, 504) or a network error while a server restarts
 *  behind the share's forwarder (tools/share/deploy.sh restarts city-web). Installed over `globalThis.fetch` at boot,
 *  so three.js loaders (GLTFLoader, FileLoader, KTX2Loader, ImageBitmapLoader) and our own fetches share it.
 *
 *  Only idempotent reads of static content retry. Streams that run their own outage handling keep plain fetch:
 *  /coarse and /cities tiles (world/coarseTiles.ts ServiceDown), multiplayer /live.json and /scores, dev /__ routes. */

/** Waits before tries 2, 3 and 4: four tries over about 10 s. */
export const RETRY_DELAYS_MS: readonly number[] = [1000, 3000, 6000];
const TRANSIENT = new Set([502, 503, 504]);
const OWN_RETRIES = /^\/(?:coarse(?:\/|$)|cities\/|__|live\.json$|scores\/|telemetry$)/;

export interface RetryOptions {
  delays?: readonly number[];
  sleep?: (ms: number, signal?: AbortSignal | null) => Promise<void>;
  /** Which requests retry; default: GET/HEAD outside the streams with their own handling. */
  retries?: (url: URL, method: string) => boolean;
}

export function retriedByDefault(url: URL, method: string): boolean {
  return (method === 'GET' || method === 'HEAD') && !OWN_RETRIES.test(url.pathname);
}

const abortable = (ms: number, signal?: AbortSignal | null) => new Promise<void>((resolve, reject) => {
  if (signal?.aborted) { reject(signal.reason); return; }
  const timer = setTimeout(() => { signal?.removeEventListener('abort', onAbort); resolve(); }, ms);
  const onAbort = () => { clearTimeout(timer); reject(signal!.reason); };
  signal?.addEventListener('abort', onAbort, { once: true });
});

/** `base` with transient failures retried after `delays`; the last answer or error is returned as is. */
export function retryingFetch(base: typeof fetch, { delays = RETRY_DELAYS_MS, sleep = abortable, retries = retriedByDefault }: RetryOptions = {}): typeof fetch {
  return async (input, init) => {
    const request = typeof Request !== 'undefined' && input instanceof Request ? input : null;
    const href = request ? request.url : String(input instanceof URL ? input.href : input);
    const method = (init?.method ?? request?.method ?? 'GET').toUpperCase();
    const signal = init?.signal ?? request?.signal ?? null;
    let url: URL;
    try { url = new URL(href, globalThis.location?.href ?? 'http://localhost/'); } catch { return base(input, init); }
    if (!retries(url, method) || init?.body != null) return base(input, init);
    for (let attempt = 0; ; attempt++) {
      const last = attempt >= delays.length;
      let response: Response;
      try {
        response = await base(request ? request.clone() : input, init);
      } catch (error) {
        if (last || signal?.aborted || (error as Error)?.name === 'AbortError') throw error;
        console.info(`[fetch] ${url.pathname}: ${String(error)}; retry ${attempt + 1}/${delays.length}`);
        await sleep(delays[attempt], signal);
        continue;
      }
      if (last || !TRANSIENT.has(response.status)) return response;
      console.info(`[fetch] ${url.pathname}: HTTP ${response.status}; retry ${attempt + 1}/${delays.length}`);
      void response.body?.cancel().catch(() => {});
      await sleep(delays[attempt], signal);
    }
  };
}

let installed = false;
/** Replaces `globalThis.fetch` once (main thread boot). */
export function installFetchRetry() {
  if (installed) return;
  installed = true;
  globalThis.fetch = retryingFetch(globalThis.fetch.bind(globalThis));
}

/** An optional asset's load: a failure is one console warning and `fallback`, never a failed game load. */
export async function optional<T>(name: string, load: Promise<T>, fallback: T): Promise<T> {
  try { return await load; } catch (error) {
    console.warn(`[assets] optional ${name} unavailable, continuing without it: ${String((error as Error)?.message ?? error)}`);
    return fallback;
  }
}
