import { useCallback, useEffect, useState } from 'react';

/**
 * Fullscreen for one element — the browser panel or the device mirror.
 *
 * The real fullscreen API is used rather than a CSS overlay, because the panel
 * is watched for minutes at a time while a test runs: this way the page fills
 * the display with no window chrome around it, and Escape leaves it because
 * that is what Escape already does everywhere else.
 *
 * The state is read back from the document rather than remembered, since a
 * viewer can leave fullscreen by ways this hook never hears about — Escape,
 * the system, or another element claiming it.
 */
export function useFullscreen(ref) {
  const [isFullscreen, setIsFullscreen] = useState(false);

  useEffect(() => {
    // Both sides are null before the ref attaches and while nothing is
    // fullscreen, so the element has to be there for the comparison to mean
    // anything — otherwise the panel opens claiming to already be fullscreen.
    const sync = () =>
      setIsFullscreen(
        Boolean(ref.current) && document.fullscreenElement === ref.current,
      );
    document.addEventListener('fullscreenchange', sync);
    sync();
    return () => document.removeEventListener('fullscreenchange', sync);
  }, [ref]);

  const toggle = useCallback(async () => {
    const node = ref.current;
    if (!node) return;
    try {
      if (document.fullscreenElement === node) {
        await document.exitFullscreen();
      } else {
        // Some browsers reject this outside a user gesture; there is nothing
        // to recover, and the panel is still perfectly usable at its own size.
        await node.requestFullscreen({ navigationUI: 'hide' });
      }
    } catch {
      setIsFullscreen(Boolean(node) && document.fullscreenElement === node);
    }
  }, [ref]);

  const supported =
    typeof document !== 'undefined' && Boolean(document.fullscreenEnabled);

  return { isFullscreen, toggle, supported };
}
