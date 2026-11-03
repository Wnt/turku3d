/** Yield past animation callbacks so background preparation does not run before the live draw. */
export function afterRender(work: () => void) {
  requestAnimationFrame(() => { setTimeout(work, 0); });
}
