import { Globe, Layers, Smartphone } from 'lucide-react';

/**
 * Web / mobile is the first cut a tester makes: the two do not share devices,
 * cannot run in the same execution, and are rarely worked on in the same
 * sitting. Filtering by it is the difference between a list and a pile.
 *
 * The counts sit on the chips so an empty result is explained before it is
 * reached — "Web 0" says the filter is right and the work is missing, which is
 * a different thing from a list that failed to load.
 */

const PLATFORMS = [
  { id: 'all', label: 'All', icon: Layers },
  { id: 'web', label: 'Web', icon: Globe },
  { id: 'mobile', label: 'Mobile', icon: Smartphone },
];

export function PlatformFilter({ value, onChange, counts }) {
  return (
    <div className="platform-filter" role="group" aria-label="Filter by platform">
      {PLATFORMS.map(({ id, label, icon: Icon }) => (
        <button
          key={id}
          className={`platform-chip ${value === id ? 'active' : ''}`}
          onClick={() => onChange(id)}
          aria-pressed={value === id}
        >
          <Icon size={13} />
          {label}
          <span className="platform-count">{counts[id] ?? 0}</span>
        </button>
      ))}
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
