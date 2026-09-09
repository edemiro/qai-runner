/**
 * What a screen shows when it has nothing to show.
 *
 * An empty list is the first thing a new user meets and the thing they meet
 * again every time a filter excludes everything, so it has to do three jobs: be
 * centred rather than a stray sentence pinned to the top-left, say plainly why
 * it is empty, and point at the one action that fills it. A bare "No suites
 * yet." does none of those.
 */
export function EmptyState({ icon: Icon, title, children, action, compact = false }) {
  return (
    <div className={`empty-panel ${compact ? 'compact' : ''}`}>
      {Icon && (
        <div className="empty-panel-mark" aria-hidden="true">
          <Icon size={compact ? 18 : 22} />
        </div>
      )}
      <h3 className="empty-panel-title">{title}</h3>
      {children && <p className="empty-panel-body">{children}</p>}
      {action && <div className="empty-panel-action">{action}</div>}
    </div>
  );
}
