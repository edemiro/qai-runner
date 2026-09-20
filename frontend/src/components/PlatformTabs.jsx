import { useRef } from 'react';
import { Globe, Smartphone } from 'lucide-react';

/**
 * Web or mobile — the first thing a tester decides and the last thing they
 * change in a sitting, so every list page opens on one platform rather than
 * on a pile of both.
 *
 * Not the sidebar's PLATFORM group, though it wears the same two glyphs on
 * purpose: those entries open a workspace to test *in*; this picks which
 * platform's records the current page is *about*. Same axis, different depth.
 *
 * Controlled — the page owns the value and seeds it with DEFAULT_PLATFORM.
 */

// Web first and by default: it is where most of the sets and runs are, and a
// tester who has not chosen should land on the fuller list, not the empty one.
export const DEFAULT_PLATFORM = 'web';

const PLATFORMS = [
  { id: 'web', label: 'Web', icon: Globe },
  { id: 'mobile', label: 'Mobile', icon: Smartphone },
];

/* `options` is how the same strip asks a different question — which mobile OS,
   on the page where devices are picked. A caller's own list lives with that
   caller; only the web/mobile pair is general enough to sit here.

   An option's icon is optional, because not every axis has a glyph worth
   drawing: lucide ships no platform logos, and "iOS" beside "Android" reads
   perfectly well without one invented for it. */
export function PlatformTabs({
  options = PLATFORMS, value, onChange, counts = null, disabled = false,
  // A second strip under the first, asking the narrower question. Drawn
  // lighter so the two read as a question and a follow-up rather than as two
  // competing choices.
  sub = false,
}) {
  const tabs = useRef([]);
  const fallback = options[0]?.id;

  // Anything that is not one of them — an 'all' left over from the old filter —
  // lands on the first rather than on a strip with nothing chosen and, because
  // of the roving tabindex below, nothing a keyboard can reach.
  const current = options.some((item) => item.id === value) ? value : fallback;

  // Arrows move the choice, not just the focus: with two tabs there is nothing
  // to browse, so Left and Right both mean "the other one".
  const onKeyDown = (event) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const index = options.findIndex((item) => item.id === current);
    const next = (index + (event.key === 'ArrowRight' ? 1 : -1) + options.length) % options.length;
    onChange?.(options[next].id);
    tabs.current[next]?.focus();
  };

  return (
    <div
      className={`platform-tabs ${sub ? 'sub' : ''}`}
      role="tablist"
      aria-label={sub ? 'Operating system' : 'Platform'}
      onKeyDown={disabled ? undefined : onKeyDown}
    >
      {options.map(({ id, label, icon: Icon }, i) => {
        const selected = id === current;
        // No counts, no badges. A "0" is a statement — the switch is right and
        // the work is missing — so a page passes counts only once it has them,
        // not while they are still loading as zeros.
        const count = counts?.[id];
        const hasCount = typeof count === 'number';
        return (
          <button
            key={id}
            ref={(el) => { tabs.current[i] = el; }}
            type="button"
            role="tab"
            aria-selected={selected}
            // Roving tabindex: Tab lands on the platform the page is on and
            // leaves the strip on the next press; the arrows move within it.
            tabIndex={selected ? 0 : -1}
            className={`platform-tab ${selected ? 'active' : ''} ${hasCount && count === 0 ? 'is-empty' : ''}`}
            onClick={() => onChange?.(id)}
            disabled={disabled}
          >
            {Icon && <Icon size={15} />}
            {label}
            {hasCount && <span className="platform-tab-count">{count}</span>}
          </button>
        );
      })}
    </div>
  );
}

/** The badge that says which platform one row belongs to. */
export function PlatformTag({ kind }) {
  const mobile = kind === 'mobile';
  return (
    <span className={`platform-tag ${mobile ? 'is-mobile' : 'is-web'}`}>
      {mobile ? <Smartphone size={11} /> : <Globe size={11} />}
      {mobile ? 'Mobile' : 'Web'}
    </span>
  );
}
