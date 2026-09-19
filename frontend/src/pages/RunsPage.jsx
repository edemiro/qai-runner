import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle, ArrowLeft, CheckCircle2, CircleSlash, Clock, Code2, Copy,
  Download, FileArchive, FileCode2, Film, Image as ImageIcon, ListChecks,
  Loader2, Play, RefreshCw, Search, Trash2, Wrench, X, XCircle,
} from 'lucide-react';
import { api } from '../api';
import { useToast } from '../hooks/useToast';
import { formatTokens } from '../lib/format';

const EXPORT_LABELS = {
  pytest: { label: 'pytest + Appium', hint: 'Python, Appium-Python-Client' },
  webdriverio: { label: 'WebdriverIO', hint: 'JavaScript, WDIO mobile' },
  gherkin: { label: 'Gherkin', hint: 'Plain .feature file' },
  'playwright-python': { label: 'Playwright (Python)', hint: 'pytest-playwright' },
  'playwright-ts': { label: 'Playwright (TS)', hint: '@playwright/test' },
};

const STATUS_META = {
  passed: { icon: CheckCircle2, label: 'Passed', className: 'passed' },
  failed: { icon: XCircle, label: 'Failed', className: 'failed' },
  cancelled: { icon: CircleSlash, label: 'Stopped', className: 'cancelled' },
  running: { icon: Loader2, label: 'Running', className: 'running' },
};

function formatDuration(ms) {
  if (ms == null) return '—';
  if (ms < 1000) return `${ms}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

function formatWhen(epochSeconds) {
  if (!epochSeconds) return '—';
  const date = new Date(epochSeconds * 1000);
  const diffMin = (Date.now() - date.getTime()) / 60000;
  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return `${Math.floor(diffMin)}m ago`;
  if (diffMin < 60 * 24) return `${Math.floor(diffMin / 60)}h ago`;
  return date.toLocaleDateString();
}

function StatusBadge({ status }) {
  const meta = STATUS_META[status] || STATUS_META.cancelled;
  const Icon = meta.icon;
  return (
    <span className={`run-status ${meta.className}`}>
      <Icon size={12} className={status === 'running' ? 'spin' : ''} />
      {meta.label}
    </span>
  );
}

function Lightbox({ src, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="lightbox" onClick={onClose} role="dialog" aria-label="Screenshot">
      <img src={src} alt="Screenshot, full size" onClick={(e) => e.stopPropagation()} />
      <button className="lightbox-close" onClick={onClose} aria-label="Close">
        <X size={20} />
      </button>
    </div>
  );
}

function StepScreenshot({ runId, stepId }) {
  const [image, setImage] = useState(null);
  const [loading, setLoading] = useState(false);
  const [zoomed, setZoomed] = useState(false);

  // A step whose frame was never stored 404s. Remembered rather than retried:
  // the button used to flash its spinner and return to idle with nothing said,
  // and every further click repeated the round-trip, so it read as broken.
  const [failed, setFailed] = useState(null);

  const load = async () => {
    if (image || loading || failed) return;
    setLoading(true);
    try {
      const data = await api.stepScreenshot(runId, stepId);
      setImage(data.screenshot);
    } catch (err) {
      setFailed(err.message || 'No screenshot was recorded for this step.');
    } finally {
      setLoading(false);
    }
  };

  if (failed) return <span className="muted small">{failed}</span>;

  if (image) {
    // Read off the payload rather than assumed: a browser step is captured as
    // JPEG (a sixth of the bytes, 200ms cheaper) while a phone still sends PNG.
    const src = `data:${image.startsWith('/9j/') ? 'image/jpeg' : 'image/png'};base64,${image}`;
    return (
      <>
        <img
          className="step-shot"
          src={src}
          alt="Screen after this step"
          title="Click to view full size"
          onClick={() => setZoomed(true)}
        />
        {zoomed && <Lightbox src={src} onClose={() => setZoomed(false)} />}
      </>
    );
  }

  return (
    <button className="btn btn-ghost btn-sm" onClick={load} disabled={loading}>
      {loading ? <Loader2 size={13} className="spin" /> : <ImageIcon size={13} />}
      Show screenshot
    </button>
  );
}

function ExportPanel({ run }) {
  const toast = useToast();
  // A web run exports to Playwright, a device run to Appium — the backend
  // decides which list applies so the UI never offers a nonsense pairing.
  const available = run.exportFormats?.length ? run.exportFormats : ['pytest', 'webdriverio', 'gherkin'];
  const [format, setFormat] = useState(available[0]);
  const [output, setOutput] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.exportRun(run.id, format);
        if (!cancelled) setOutput(data);
      } catch (err) {
        if (!cancelled) toast.error(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [run.id, format, toast]);

  const selectFormat = (next) => {
    if (next === format) return;
    setLoading(true);
    setFormat(next);
  };

  const download = () => {
    if (!output) return;
    const blob = new Blob([output.content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = output.filename;
    anchor.click();
    URL.revokeObjectURL(url);
    toast.success(`${output.filename} downloaded`);
  };

  return (
    <div className="export-panel">
      <div className="export-tabs">
        {available.map((id) => {
          const meta = EXPORT_LABELS[id] || { label: id, hint: '' };
          return (
            <button
              key={id}
              className={`export-tab ${format === id ? 'active' : ''}`}
              onClick={() => selectFormat(id)}
              title={meta.hint}
            >
              {meta.label}
            </button>
          );
        })}
        <div className="export-actions">
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => {
              navigator.clipboard.writeText(output?.content || '');
              toast.success('Script copied');
            }}
            disabled={!output}
          >
            <Copy size={13} />
            Copy
          </button>
          <button className="btn btn-primary btn-sm" onClick={download} disabled={!output}>
            <Download size={13} />
            Download
          </button>
        </div>
      </div>
      <pre className="export-code">
        {loading ? 'Generating…' : output?.content || 'Nothing to export yet.'}
      </pre>
    </div>
  );
}

function RunDetail({ runId, onBack, onDeleted, activeSessionId, onReplay }) {
  const toast = useToast();
  const [run, setRun] = useState(null);
  const [loading, setLoading] = useState(true);
  /* Null until the reader picks one, so a run with written steps opens on
     them: those are the scenario, and the agent's own actions are the
     drill-down rather than the headline. Derived instead of set in an effect,
     which would fight the reader's own choice on every re-render. */
  const [tab, setTab] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.run(runId);
        if (!cancelled) setRun(data);
      } catch (err) {
        if (!cancelled) toast.error(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [runId, toast]);

  if (loading && !run) return <div className="pane-empty">Loading run…</div>;
  if (!run) return <div className="pane-empty">This run no longer exists.</div>;

  const activeTab = tab ?? (run.scenarioSteps?.length ? 'scenario' : 'steps');
  const passed = run.steps.filter((s) => s.status === 'passed').length;
  const assertions = run.steps.filter((s) => s.action?.startsWith('assert')).length;
  const allEvents = run.pageEvents || [];
  const pageErrors = allEvents.filter((event) => event.level === 'error');
  // Warnings are recorded but never decide a verdict — a 404 on a tracking
  // pixel is a fact about the page, not evidence the feature is broken. They
  // still have to be visible, or "1 page error" is unactionable.
  const pageNotices = allEvents.filter((event) => event.level !== 'error');
  const healed = run.steps.filter((s) => s.healed).length;

  return (
    <div className="run-detail">
      <div className="run-detail-header">
        <button className="btn btn-ghost btn-sm" onClick={onBack}>
          <ArrowLeft size={14} />
          All runs
        </button>
        <div className="run-detail-actions">
          <a
            className="btn btn-ghost btn-sm"
            href={api.runReportUrl(run.id, 'junit')}
            title="JUnit XML — the format Jenkins, GitHub Actions and GitLab read natively"
          >
            <FileCode2 size={13} />
            JUnit
          </a>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => onReplay(run)}
            disabled={!activeSessionId}
            title={
              activeSessionId
                ? 'Re-execute the recorded steps, repairing any that no longer match'
                : 'Connect a device or open a page first'
            }
          >
            <Play size={13} />
            Replay
          </button>
          <button
            className="btn btn-danger btn-sm"
            onClick={async () => {
              try {
                await api.deleteRun(run.id);
                toast.success('Run deleted');
                onDeleted();
              } catch (err) {
                toast.error(err.message);
              }
            }}
          >
            <Trash2 size={13} />
            Delete
          </button>
        </div>
      </div>

      <h2 className="run-detail-title">{run.title}</h2>
      <p className="run-detail-goal">{run.goal}</p>

      <div className="run-stats">
        <div className="stat">
          <StatusBadge status={run.status} />
        </div>
        <div className="stat">
          <span className="stat-value">
            {passed}/{run.step_count}
          </span>
          <span className="stat-label">steps passed</span>
        </div>
        <div className="stat">
          <span className="stat-value">{assertions}</span>
          <span className="stat-label">assertions</span>
        </div>
        <div className="stat">
          <span className="stat-value">{formatDuration(run.duration_ms)}</span>
          <span className="stat-label">duration</span>
        </div>
        <div className="stat">
          <span className="stat-value">{run.device_name || '—'}</span>
          <span className="stat-label">
            {run.platform} {run.app_id ? `· ${run.app_id}` : ''}
          </span>
        </div>
        {/* Runs from before the counter existed have no figure; showing a dash
            there would read as "cost nothing", so the tile is left out. */}
        {run.llm_calls != null && (
          <div className="stat">
            <span className="stat-value">
              {formatTokens(
                (run.input_tokens || 0) + (run.output_tokens || 0)
                  + (run.cache_read_tokens || 0) + (run.cache_write_tokens || 0),
              )}
            </span>
            <span
              className="stat-label"
              title={`${run.input_tokens || 0} input · ${run.output_tokens || 0} output · `
                + `${run.cache_read_tokens || 0} cache read · ${run.cache_write_tokens || 0} cache write`}
            >
              tokens · {run.llm_calls} call{run.llm_calls === 1 ? '' : 's'}
            </span>
          </div>
        )}
      </div>

      {run.error && (
        <div className="banner danger">
          <XCircle size={15} />
          <span>{run.error}</span>
        </div>
      )}

      {run.verdict_note && run.verdict_note !== run.error && (
        <div className="banner warn">
          <AlertTriangle size={15} />
          <span>{run.verdict_note}</span>
        </div>
      )}

      {healed > 0 && (
        <div className="banner info">
          <Wrench size={15} />
          <span>
            {healed} step{healed === 1 ? '' : 's'} were repaired against the current
            page. Re-export the script to pick up the new selectors.
          </span>
        </div>
      )}

      <div className="detail-tabs">
        {run.scenarioSteps?.length > 0 && (
          <button
            className={`detail-tab ${activeTab === 'scenario' ? 'active' : ''}`}
            onClick={() => setTab('scenario')}
          >
            <ListChecks size={13} />
            Scenario steps
            <span className="tab-count">{run.scenarioSteps.length}</span>
          </button>
        )}
        <button className={`detail-tab ${activeTab === 'steps' ? 'active' : ''}`} onClick={() => setTab('steps')}>
          {run.scenarioSteps?.length > 0 ? 'Agent actions' : 'Steps'}
        </button>
        <button className={`detail-tab ${activeTab === 'export' ? 'active' : ''}`} onClick={() => setTab('export')}>
          <Code2 size={13} />
          Export script
        </button>
        {allEvents.length > 0 && (
          <button
            className={`detail-tab ${activeTab === 'errors' ? 'active' : ''}`}
            onClick={() => setTab('errors')}
          >
            <AlertTriangle size={13} />
            Page errors
            <span className="tab-count">{pageErrors.length || allEvents.length}</span>
          </button>
        )}
        {run.artifacts?.length > 0 && (
          <button
            className={`detail-tab ${activeTab === 'artifacts' ? 'active' : ''}`}
            onClick={() => setTab('artifacts')}
          >
            <Film size={13} />
            Artifacts
            <span className="tab-count">{run.artifacts.length}</span>
          </button>
        )}
      </div>

      {activeTab === 'scenario' && (
        /* The scenario as the tester wrote it, judged step by step. The other
           tab holds what the agent did to get there — one step can be several
           clicks, and mixing the two is what made a report hard to read. */
        <ol className="scenario-report">
          {run.scenarioSteps.map((step) => (
            <li key={step.id} className={`scenario-report-step ${step.status}`}>
              <span className="scenario-report-idx">{step.idx}</span>
              <div className="scenario-report-body">
                <span className="scenario-report-action">{step.action}</span>
                {step.expected && (
                  <span className="scenario-report-expected">
                    Expected: {step.expected}
                  </span>
                )}
                {step.message && (
                  <span className="scenario-report-message">{step.message}</span>
                )}
              </div>
              <span className="scenario-report-meta">
                {step.actions_used != null && (
                  <span className="muted small">
                    {step.actions_used} action{step.actions_used === 1 ? '' : 's'}
                  </span>
                )}
                <span className={`verdict verdict-${step.status === 'passed' ? 'pass' : step.status === 'failed' ? 'fail' : 'other'}`}>
                  {step.status === 'passed' ? 'Pass' : step.status === 'failed' ? 'Fail' : step.status}
                </span>
              </span>
            </li>
          ))}
        </ol>
      )}

      {activeTab === 'steps' && (
        <ol className="report-steps">
          {run.steps.map((step) => (
            <li key={step.id} className={`report-step ${step.status}`}>
              <div className="report-step-head">
                <span className="report-index">{step.idx}</span>
                <span className="report-action">{step.action.replace('_', ' ')}</span>
                {step.target && <span className="report-target">{step.target}</span>}
                {step.value && <span className="report-value">“{step.value}”</span>}
                <span className="report-spacer" />
                {step.healed && (
                  <span className="assert-tag healed" title="The recorded selector no longer matched; QAi re-found this element.">
                    <Wrench size={10} /> healed
                  </span>
                )}
                {step.action?.startsWith('assert') && <span className="assert-tag">assertion</span>}
                <span className="report-duration">
                  <Clock size={11} />
                  {formatDuration(step.duration_ms)}
                </span>
                {step.status === 'passed' ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
              </div>
              {step.reason && <p className="report-reason">{step.reason}</p>}
              {step.message && <p className="report-message">{step.message}</p>}
              {step.hasScreenshot && <StepScreenshot runId={run.id} stepId={step.id} />}
            </li>
          ))}
        </ol>
      )}

      {activeTab === 'export' && <ExportPanel run={run} />}

      {activeTab === 'errors' && (
        <div className="page-errors">
          <p className="muted small">
            What the browser itself reported while this run was in flight.
          </p>

          {pageErrors.length > 0 && (
            <>
              <h4 className="scan-section">
                Sonucu etkileyenler ({pageErrors.length})
              </h4>
              <ul className="error-list">
                {pageErrors.map((event, index) => (
                  <PageEventRow key={`e-${index}`} event={event} />
                ))}
              </ul>
            </>
          )}

          {pageNotices.length > 0 && (
            <>
              <h4 className="scan-section">Bilgi amaçlı ({pageNotices.length})</h4>
              <p className="muted small">
                Kaydedildi ama koşumu düşürmedi: bir takip pikselinin ya da
                fontun 404 vermesi, tıklanan kontrolün bozuk olduğunu göstermez.
              </p>
              <ul className="error-list">
                {pageNotices.map((event, index) => (
                  <PageEventRow key={`n-${index}`} event={event} muted />
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {activeTab === 'artifacts' && (
        <ul className="artifact-list">
          {run.artifacts.map((artifact) => (
            <li key={artifact.id} className="artifact-row">
              {artifact.kind === 'video' ? <Film size={16} /> : <FileArchive size={16} />}
              <div className="artifact-body">
                <span className="artifact-label">{artifact.label || artifact.kind}</span>
                <span className="muted small">
                  {formatBytes(artifact.size_bytes)}
                  {artifact.kind === 'trace' && ' · open with: npx playwright show-trace <file>'}
                </span>
              </div>
              <a className="btn btn-sm" href={api.artifactUrl(artifact.id)}>
                <Download size={13} /> Download
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * One recorded browser event. Says which click produced it, what kind of
 * request it was, and whose server answered — the three things needed to tell
 * a real defect from third-party noise.
 */
function PageEventRow({ event, muted = false }) {
  return (
    <li className={`error-row ${muted ? 'notice' : ''}`}>
      <span className={`error-kind ${event.kind}`}>{event.kind}</span>
      <div className="error-body">
        <span className="error-text">{event.text}</span>
        {event.url && <span className="error-url">{event.url}</span>}
        <span className="error-meta">
          {event.step_idx != null && <span>adım {event.step_idx}</span>}
          {event.resourceType && <span>{event.resourceType}</span>}
          {event.thirdParty ? <span>üçüncü parti</span> : null}
        </span>
      </div>
      {event.status != null && <span className="error-status">{event.status}</span>}
    </li>
  );
}

function formatBytes(bytes) {
  if (!bytes) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

const PAGE_SIZE = 50;

export function RunsPage({ activeSessionId, onReplay, selectedRunId, onSelectRun }) {
  const toast = useToast();
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('all');
  const [priority, setPriority] = useState('all');
  const [search, setSearch] = useState('');
  const [debounced, setDebounced] = useState('');
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  const [reloadKey, setReloadKey] = useState(0);

  const load = useCallback(() => {
    setLoading(true);
    setReloadKey((key) => key + 1);
  }, []);

  // Typing a query should not fire a request per keystroke.
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.runs(PAGE_SIZE, debounced);
        if (!cancelled) {
          setRuns(data.runs || []);
          setHasMore(Boolean(data.hasMore));
        }
      } catch (err) {
        if (!cancelled) toast.error(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [reloadKey, debounced, toast]);

  const loadMore = async () => {
    setLoadingMore(true);
    try {
      const data = await api.runs(PAGE_SIZE, debounced, runs.length);
      setRuns((current) => [...current, ...(data.runs || [])]);
      setHasMore(Boolean(data.hasMore));
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoadingMore(false);
    }
  };

  const filtered = useMemo(() => {
    const byStatus = filter === 'all' ? runs : runs.filter((run) => run.status === filter);
    if (priority === 'all') return byStatus;
    // "Ungraded" is its own answer, not a missing one: a run started from the
    // chat belongs to no Test Set and so carries no priority.
    if (priority === 'ungraded') return byStatus.filter((run) => !run.priority);
    return byStatus.filter((run) => run.priority === priority);
  }, [runs, filter, priority]);

  if (selectedRunId) {
    return (
      <main className="page">
        <RunDetail
          runId={selectedRunId}
          activeSessionId={activeSessionId}
          onReplay={onReplay}
          onBack={() => onSelectRun(null)}
          onDeleted={() => {
            onSelectRun(null);
            load();
          }}
        />
      </main>
    );
  }

  return (
    <main className="page">
      <header className="page-header">
        <div>
          <h1 className="page-title">Test Runs</h1>
          <p className="page-subtitle">Every agent run is recorded with its steps, assertions and screenshots.</p>
        </div>
        <button className="btn btn-ghost" onClick={load} disabled={loading}>
          <RefreshCw size={15} className={loading ? 'spin' : ''} />
          Refresh
        </button>
      </header>

      <div className="run-search">
        <Search size={14} />
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search runs by title, goal, device or URL…"
          aria-label="Search runs"
        />
        {debounced && (
          <span className="muted small">
            {runs.length} match{runs.length === 1 ? '' : 'es'}
          </span>
        )}
      </div>

      <div className="filter-row">
        {['all', 'passed', 'failed', 'cancelled'].map((value) => (
          <button
            key={value}
            className={`filter-chip ${filter === value ? 'active' : ''}`}
            onClick={() => setFilter(value)}
          >
            {value === 'all' ? 'All' : STATUS_META[value].label}
            <span className="filter-count">
              {value === 'all' ? runs.length : runs.filter((run) => run.status === value).length}
            </span>
          </button>
        ))}
      </div>

      <div className="filter-row">
        {['all', 'Critical', 'High', 'Medium', 'Low', 'ungraded'].map((value) => {
          const count = value === 'all'
            ? runs.length
            : value === 'ungraded'
              ? runs.filter((run) => !run.priority).length
              : runs.filter((run) => run.priority === value).length;
          if (!count && value !== 'all') return null;
          return (
            <button
              key={value}
              className={`filter-chip ${priority === value ? 'active' : ''}`}
              onClick={() => setPriority(value)}
              title={value === 'ungraded' ? 'Runs started from the chat, outside any Test Set' : undefined}
            >
              {value === 'all' ? 'Any priority' : value === 'ungraded' ? 'Ungraded' : value}
              <span className="filter-count">{count}</span>
            </button>
          );
        })}
      </div>

      {filtered.length === 0 ? (
        <div className="empty-state">
          <Clock size={34} />
          <h3>{loading ? 'Loading runs…' : 'No runs yet'}</h3>
          <p>
            Connect a device in Studio, then describe a scenario in the Agent tab. Each run lands here with a
            full report and an exportable script.
          </p>
        </div>
      ) : (
        <div className="run-list">
          {filtered.map((run) => (
            <button key={run.id} className="run-row" onClick={() => onSelectRun(run.id)}>
              <StatusBadge status={run.status} />
              <div className="run-row-main">
                <span className="run-row-title">{run.title}</span>
                <span className="run-row-meta">
                  {run.device_name || 'Unknown device'} · {run.platform || '—'} · {run.model || '—'}
                </span>
              </div>
              {/* Always rendered, empty or not: a conditional cell would shift
                  every column on rows that have no priority. */}
              <span className="run-row-priority">
                {run.priority && (
                  <span className={`priority-tag p-${run.priority.toLowerCase()}`}>
                    {run.priority}
                  </span>
                )}
              </span>
              <span className="run-row-steps">
                {run.step_count - run.failed_count}/{run.step_count} steps
              </span>
              <span className="run-row-duration">{formatDuration(run.duration_ms)}</span>
              <span className="run-row-when">{formatWhen(run.started_at)}</span>
            </button>
          ))}
          {hasMore && (
            <button className="btn btn-ghost load-more" onClick={loadMore} disabled={loadingMore}>
              {loadingMore ? 'Loading…' : 'Load more'}
            </button>
          )}
        </div>
      )}
    </main>
  );
}
