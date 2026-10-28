/** Shared flow layout lets long build/draft status lines wrap without covering one another. */
export function statusBadges(parent: HTMLElement): HTMLElement {
  let stack = parent.querySelector<HTMLElement>('#status-badges');
  if (!stack) {
    stack = document.createElement('div');
    stack.id = 'status-badges';
    Object.assign(stack.style, { position: 'fixed', top: '8px', right: '8px', maxWidth: 'calc(100vw - 36px)',
      display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '4px', pointerEvents: 'none', zIndex: '20' });
    parent.append(stack);
  }
  return stack;
}
