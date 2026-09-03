import { useMemo, useState } from 'react';
import { Check, ChevronRight, Copy, MousePointerClick, RefreshCw, Search, Type, XCircle } from 'lucide-react';
import { api } from '../api';
import { useToast } from '../hooks/useToast';
import { elementLabel as label, roleColor } from '../lib/elements';

function matchesQuery(node, query) {
  if (!query) return true;
  const haystack = [node.role, node.id, node.text, node['content-desc'], node.nativeClass]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
  return haystack.includes(query);
}

function subtreeMatches(node, query) {
  if (matchesQuery(node, query)) return true;
  return (node.children || []).some((child) => subtreeMatches(child, query));
}

function TreeNode({ node, depth, query, selectedId, onHover, onLeave, onSelect }) {
  const [collapsed, setCollapsed] = useState(depth > 3);
  const children = node.children || [];
  const visibleChildren = query ? children.filter((child) => subtreeMatches(child, query)) : children;
  const hasChildren = visibleChildren.length > 0;
  const text = label(node);
  const isSelected = selectedId && node.elementId === selectedId;
  const dimmed = query && !matchesQuery(node, query);

  return (
    <div className="tree-node">
      <div
        className={`tree-row ${isSelected ? 'selected' : ''} ${dimmed ? 'dimmed' : ''} ${node.actionable === false ? 'inert' : ''}`}
        style={{ paddingLeft: `${depth * 13 + 6}px` }}
        onMouseEnter={() => onHover(node)}
        onMouseLeave={onLeave}
        onClick={() => onSelect(node)}
      >
        {hasChildren ? (
          <button
            className={`tree-caret ${collapsed ? '' : 'open'}`}
            onClick={(event) => {
              event.stopPropagation();
              setCollapsed((value) => !value);
            }}
            aria-label={collapsed ? 'Expand' : 'Collapse'}
          >
            <ChevronRight size={12} />
          </button>
        ) : (
          <span className="tree-caret-spacer" />
        )}

        <span className="role-chip" style={{ '--chip': roleColor(node.role) }}>
          {node.role}
        </span>

        {node.id && <span className="tree-id">#{node.id}</span>}
        {text && <span className="tree-text">“{text.length > 34 ? `${text.slice(0, 32)}…` : text}”</span>}
      </div>

      {hasChildren && !collapsed && (
        <div className="tree-children">
          {visibleChildren.map((child, index) => (
            <TreeNode
              key={child.elementId || index}
              node={child}
              depth={depth + 1}
              query={query}
              selectedId={selectedId}
              onHover={onHover}
              onLeave={onLeave}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function DetailRow({ name, value, copyable }) {
  const toast = useToast();
  if (value === null || value === undefined || value === '') return null;

  return (
    <div className="detail-row">
      <span className="detail-key">{name}</span>
      <span className="detail-value">{String(value)}</span>
      {copyable && (
        <button
          className="detail-copy"
          title={`Copy ${name}`}
          onClick={() => {
            navigator.clipboard.writeText(String(value));
            toast.success(`${name} copied`);
          }}
        >
          <Copy size={12} />
        </button>
      )}
    </div>
  );
}

export function Inspector({
  sessionId,
  tree,
  snapshotId,
  loading,
  selected,
  onSelect,
  onHover,
  onLeave,
  onRefresh,
}) {
  const toast = useToast();
  const [query, setQuery] = useState('');
  const [typeValue, setTypeValue] = useState('');
  const [pending, setPending] = useState(false);

  const normalizedQuery = query.trim().toLowerCase();
  const visible = useMemo(() => {
    if (!tree) return null;
    if (!normalizedQuery) return tree;
    return subtreeMatches(tree, normalizedQuery) ? tree : null;
  }, [tree, normalizedQuery]);

  const runAction = async (action, value) => {
    if (!selected || !sessionId) return;
    setPending(true);
    try {
      const result = await api.action(sessionId, {
        action,
        elementId: selected.elementId,
        xpath: selected.xpath,
        snapshotId,
        value: value ?? '',
      });
      toast.success(result.message);
      setTypeValue('');
      onRefresh?.();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="inspector">
      <div className="inspector-tree-pane">
        <div className="inspector-toolbar">
          <div className="search-field">
            <Search size={14} />
            <input
              placeholder="Filter by text, id or role…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            {query && (
              <button className="icon-btn-tiny" onClick={() => setQuery('')} aria-label="Clear filter">
                <XCircle size={13} />
              </button>
            )}
          </div>
          <button className="btn btn-ghost btn-sm" onClick={onRefresh} disabled={loading}>
            <RefreshCw size={13} className={loading ? 'spin' : ''} />
            Reload
          </button>
        </div>

        <div className="tree-scroll">
          {loading && !tree ? (
            <div className="pane-empty">Reading the screen…</div>
          ) : visible ? (
            <TreeNode
              node={visible}
              depth={0}
              query={normalizedQuery}
              selectedId={selected?.elementId}
              onHover={onHover}
              onLeave={onLeave}
              onSelect={onSelect}
            />
          ) : (
            <div className="pane-empty">
              {tree ? 'Nothing matches that filter.' : 'No screen data yet — reload the source.'}
            </div>
          )}
        </div>
      </div>

      <div className="inspector-detail-pane">
        {selected ? (
          <>
            <div className="detail-header">
              <span className="role-chip lg" style={{ '--chip': roleColor(selected.role) }}>
                {selected.role}
              </span>
              <span className="detail-title">{label(selected) || selected.elementId}</span>
            </div>

            <div className="detail-list">
              <DetailRow name="elementId" value={selected.elementId} copyable />
              <DetailRow name="resource-id" value={selected.id} copyable />
              <DetailRow name="text" value={selected.text} copyable />
              <DetailRow name="content-desc" value={selected['content-desc']} copyable />
              <DetailRow name="class" value={selected.nativeClass} copyable />
              <DetailRow name="bounds" value={selected.bounds} copyable />
              <DetailRow name="xpath" value={selected.xpath} copyable />
              <DetailRow name="actionable" value={selected.actionable ? 'yes' : 'no'} />
              <DetailRow name="enabled" value={selected.enabled === false ? 'no' : 'yes'} />
              {selected.scrollable && <DetailRow name="scrollable" value="yes" />}
              {selected.checked && <DetailRow name="checked" value="yes" />}
            </div>

            <div className="detail-actions">
              <button className="btn btn-primary btn-sm" onClick={() => runAction('click')} disabled={pending}>
                <MousePointerClick size={14} />
                Click
              </button>
              <div className="type-group">
                <input
                  className="text-input"
                  placeholder="Text to type…"
                  value={typeValue}
                  onChange={(event) => setTypeValue(event.target.value)}
                  onKeyDown={(event) => event.key === 'Enter' && typeValue && runAction('type', typeValue)}
                />
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => runAction('type', typeValue)}
                  disabled={pending || !typeValue}
                >
                  <Type size={14} />
                </button>
              </div>
              <button className="btn btn-ghost btn-sm" onClick={() => runAction('clear')} disabled={pending}>
                Clear
              </button>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  navigator.clipboard.writeText(selected.xpath || '');
                  toast.success('XPath copied');
                }}
              >
                <Check size={14} />
                Copy XPath
              </button>
            </div>
          </>
        ) : (
          <div className="pane-empty">
            Select a node in the tree, or use the pick tool on the device screen.
          </div>
        )}
      </div>
    </div>
  );
}
