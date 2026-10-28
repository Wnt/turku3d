/** Static hosts cannot set navigation headers. Install in the game's own subpath, then reload once.
 * No storage dependency: standalone iOS launches and private sessions may deny sessionStorage. */
export async function isolatePage(): Promise<void> {
  if (globalThis.crossOriginIsolated || !('serviceWorker' in navigator) || !globalThis.isSecureContext) return;
  // A controlling worker already tried isolation. Unsupported browsers keep the self-clocked transport.
  if (navigator.serviceWorker.controller) return;
  try {
    const controlled = await new Promise<boolean>((resolve) => {
      const finish = (ok: boolean) => {
        clearTimeout(timer);
        navigator.serviceWorker.removeEventListener('controllerchange', changed);
        resolve(ok);
      };
      const changed = () => finish(true);
      const timer = setTimeout(() => finish(false), 4000);
      navigator.serviceWorker.addEventListener('controllerchange', changed);
      void navigator.serviceWorker.register(new URL('isolation.js', document.baseURI), { scope: './', updateViaCache: 'none' })
        .then(() => { if (navigator.serviceWorker.controller) finish(true); })
        .catch(error => { console.warn('[physics] isolation unavailable', error); finish(false); });
    });
    if (controlled) {
      location.reload();
      await new Promise(() => {});
    }
  } catch (error) { console.warn('[physics] isolation unavailable', error); }
}
