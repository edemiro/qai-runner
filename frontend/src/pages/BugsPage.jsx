import { useCallback, useEffect, useState } from 'react';
import {
  Bug, Camera, ChevronRight, ExternalLink, Loader2, Trash2,
} from 'lucide-react';

import { api } from '../api';
import { DEFAULT_PLATFORM, PlatformTabs } from '../components/PlatformTabs';
import { useToast } from '../hooks/useToast';
import { OS_TABS } from '../lib/platforms';

/**
 * Defects raised off failed scenarios.
 *
 * A run says a scenario failed; a bug says what that turned out to mean, and
 * outlives the run that found it. Everything needed to read one — the title,
 * the body, the frame — was copied in when it was raised, so a bug still reads
 * correctly after its run, its scenario and its Test Set have all been deleted.
 */

const STATUS_LABEL = {
  open: 'Open',
  triaged: 'Triaged',
  fixed: 'Fixed',
  closed: 'Closed',
  'not-a-bug': 'Not a bug',
};

function when(seconds) {
  if (!seconds) return '—';
  return new Date(seconds * 1000).toLocaleString('tr-TR', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

function BugShot({ bugId }) {
  const [image, setImage] = useState(null);
  const [state, setState] = useState('idle');

  const load = async () => {
    if (image || state === 'loading') return;
    setState('loading');
    try {
      const data = await api.bugScreenshot(bugId);
      setImage(data.screenshot);
      setState('shown');
    } catch (err) {
      setState(err.message || 'No screenshot was kept with this bug.');
    }
  };

  if (image) {
    const src = `data:${image.startsWith('/9j/') ? 'image/jpeg' : 'image/png'};base64,${image}`;
    return <img className="bug-shot" src={src} alt="The screen when the scenario failed" />;
  }
  if (state !== 'idle' && state !== 'loading' && state !== 'shown') {
    return <span className="muted small">{state}</span>;
  }
  return (
    <button className="btn btn-ghost btn-sm" onClick={load} disabled={state === 'loading'}>
      {state === 'loading' ? <Loader2 size={14} className="spin" /> : <Camera size={14} />}
      Show the screen
    </button>
  );
}

export function BugsPage({ onOpenRun = null, initialPlatform = null }) {
  const toast = useToast();
  const [bugs, setBugs] = useState([]);
  const [counts, setCounts] = useState({});
  const [meta, setMeta] = useState({ codes: {}, notAppDefects: [], statuses: [] });
  const [status, setStatus] = useState('');
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState(null);
  const [loading, setLoading] = useState(true);
  /* The platform is above the status filter, not beside it: a web bug and a
     mobile bug are fixed by different people in different code, so "everything
     open" is a question worth asking one platform at a time. The status counts
     below are recounted within it for the same reason. */
  const [platform, setPlatform] = useState(initialPlatform || DEFAULT_PLATFORM);
  const [platformCounts, setPlatformCounts] = useState(null);
  /* And within Mobile, which phone — for the same reason, one step down. An
     iOS bug and an Android one are different code and usually different
     people. Which phone is read off the run that raised the bug; one filed by
     hand has no run and stays on both. */
  const [os, setOs] = useState('ios');
  const [osCounts, setOsCounts] = useState(null);

  // Bumped to re-read after a change of our own. The fetch itself lives in the
  // effect rather than in a callback the effect calls, so nothing sets state
  // while the effect body is still running.
  const [reload, setReload] = useState(0);
  const refresh = useCallback(() => setReload((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.bugs({
          status: status || null, search: search || null, kind: platform,
          os: platform === 'mobile' ? os : null,
        });
        if (cancelled) return;
        setBugs(data.bugs || []);
        setCounts(data.counts || {});
        setPlatformCounts(data.platformCounts || null);
        setOsCounts(data.osCounts || null);
        setMeta({
          codes: data.codes || {},
          notAppDefects: data.notAppDefects || [],
          statuses: data.statuses || [],
        });
      } catch (err) {
        if (!cancelled) toast.error(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [status, search, platform, os, reload, toast]);

  const selected = bugs.find((b) => b.id === selectedId) || null;

  const setBugStatus = async (bug, next) => {
    try {
      await api.updateBug(bug.id, { status: next });
      refresh();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const remove = async (bug) => {
    try {
      await api.deleteBug(bug.id);
      setSelectedId((current) => (current === bug.id ? null : current));
      refresh();
      toast.success('Bug deleted.');
    } catch (err) {
      toast.error(err.message);
    }
  };

  return (
    <main className="page">
      <header className="page-header">
        <div>
          <h1 className="page-title">Bug Report</h1>
          <p className="page-subtitle">
            What the failures turned out to mean. Raised from a scenario, kept
            after the run that found it is gone.
          </p>
        </div>
        <input
          className="bug-search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search title, body or scenario"
        />
        <PlatformTabs value={platform} onChange={setPlatform} counts={platformCounts} />
        {platform === 'mobile' && (
          <PlatformTabs
            options={OS_TABS} value={os} onChange={setOs}
            counts={loading ? null : osCounts} sub
          />
        )}
      </header>

      <div className="segmented">
        {[['', 'All'], ...(meta.statuses || []).map((s) => [s, STATUS_LABEL[s] || s])]
          .map(([value, label]) => (
            <button
              key={value || 'all'}
              className={status === value ? 'active' : ''}
              onClick={() => setStatus(value)}
            >
              {label}
              <span className="tab-count">{value ? (counts[value] || 0) : (counts.all || 0)}</span>
            </button>
          ))}
      </div>

      {loading ? (
        <p className="muted">Loading…</p>
      ) : bugs.length === 0 ? (
        <div className="empty-state card">
          <Bug size={30} />
          <h3>{status || search ? 'Nothing matches that' : 'No bugs raised yet'}</h3>
          <p>
            {status || search
              ? 'Clear the filter to see the rest.'
              : 'Open a failed scenario on Test Executions and raise one — the title, '
                + 'the steps to reproduce and the screen are written for you from the run.'}
          </p>
        </div>
      ) : (
        <div className="bugs-layout">
          <ul className="bug-list">
            {bugs.map((bug) => {
              const notApp = (meta.notAppDefects || []).includes(bug.code);
              return (
                <li key={bug.id}>
                  <button
                    className={`bug-item ${bug.id === selectedId ? 'active' : ''}`}
                    onClick={() => setSelectedId(bug.id)}
                  >
                    <div className="bug-item-main">
                      <span className="bug-item-title">{bug.title}</span>
                      <span className="bug-item-meta">
                        <span className={`bug-status s-${bug.status}`}>
                          {STATUS_LABEL[bug.status] || bug.status}
                        </span>
                        {bug.severity && (
                          <span className={`priority-tag p-${bug.severity.toLowerCase()}`}>
                            {bug.severity}
                          </span>
                        )}
                        {bug.code && (
                          <span className={`bug-code ${notApp ? 'soft' : ''}`}>{bug.code}</span>
                        )}
                        <span className="muted small">{when(bug.created_at)}</span>
                      </span>
                    </div>
                    <ChevronRight size={15} />
                  </button>
                </li>
              );
            })}
          </ul>

          <section className="card bug-detail">
            {!selected ? (
              <div className="empty-panel">
                <Bug size={24} />
                <p>Pick a bug to read it.</p>
              </div>
            ) : (
              <>
                <div className="card-head stacked">
                  <h2 className="card-title">{selected.title}</h2>
                  <p className="muted small">
                    {selected.code && (meta.codes[selected.code] || selected.code)}
                    {selected.url && ` · ${selected.url}`}
                  </p>
                </div>

                <div className="bug-actions">
                  <select
                    value={selected.status}
                    onChange={(event) => setBugStatus(selected, event.target.value)}
                    aria-label="Bug status"
                  >
                    {(meta.statuses || []).map((value) => (
                      <option key={value} value={value}>{STATUS_LABEL[value] || value}</option>
                    ))}
                  </select>
                  {onOpenRun && selected.run_id && (
                    <button
                      className="btn btn-ghost btn-sm"
                      onClick={() => onOpenRun(selected.run_id)}
                    >
                      <ExternalLink size={14} /> Open the run
                    </button>
                  )}
                  <button className="btn btn-danger btn-sm" onClick={() => remove(selected)}>
                    <Trash2 size={14} /> Delete
                  </button>
                </div>

                {/* The body is written as plain text with a few bold labels;
                    shown as-is rather than parsed, so nothing a run produced
                    can be swallowed by a markdown rule. */}
                <pre className="bug-body">{selected.detail}</pre>

                {selected.hasScreenshot && <BugShot bugId={selected.id} />}
              </>
            )}
          </section>
        </div>
      )}
    </main>
  );
}
