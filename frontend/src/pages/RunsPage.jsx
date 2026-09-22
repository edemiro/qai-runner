import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle, ArrowLeft, CheckCircle2, CircleSlash, Clock, Code2, Copy,
  Download, FileArchive, FileCode2, Film, Image as ImageIcon, ListChecks,
  Loader2, Play, RefreshCw, Search, Trash2, Wrench, X, XCircle,
} from 'lucide-react';
import { api } from '../api';
import { DEFAULT_PLATFORM, PlatformTabs } from '../components/PlatformTabs';
import { useToast } from '../hooks/useToast';
import { formatTokens } from '../lib/format';
import { OS_TABS } from '../lib/platforms';
import './runs.css';

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

/* The four questions actually asked of a long action list. "Failed" is the one
   that earns the toolbar: a thirty-step run that went wrong went wrong in one
   place, and finding it used to mean reading the twenty that passed. */
const STEP_FILTERS = [
  { id: 'all', label: 'All', match: () => true },
  { id: 'failed', label: 'Failed', match: (step) => step.status !== 'passed' },
  { id: 'assert', label: 'Assertions', match: (step) => Boolean(step.action?.startsWith('assert')) },
  { id: 'healed', label: 'Healed', match: (step) => Boolean(step.healed) },
];

/* A report opens with nothing chosen. `runId` is null so that the first run
   opened compares unequal to it and starts here too. */
const EMPTY_VIEW = {
  runId: null, tab: null, stepFilter: 'all', stepQuery: '', eventQuery: '', exportOpen: false,
};

function formatDuration(ms) {
  if (ms == null) return '—';
  if (ms < 1000) return `${ms}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

/** What this run was pointed at: the phone, or the site's host.
 *  The full address is the same on every web row and says nothing the host
 *  does not — the path lives on the run itself. */
function runTarget(run) {
  const name = run.device_name || run.app_id || '';
  if (!name) return 'Unknown target';
  try {
    return name.startsWith('http') ? new URL(name).host : name;
  } catch {
    return name;
  }
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

/** A search field for the lists inside a report.
 *  The global reset strips an input of its border and background, so a field
 *  is only visible inside something that draws them — this is that something,
 *  sized to sit in a toolbar rather than to head a page. */
function ReportSearch({ value, onChange, placeholder, label }) {
  return (
    <span className="report-search">
      <Search size={13} />
      <input
        type="search"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        aria-label={label}
      />
      {value && (
        <button
          className="report-search-clear"
          onClick={() => onChange('')}
          aria-label="Clear search"
        >
          <X size={12} />
        </button>
      )}
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
  /* Which tab, which filter, what was typed — everything the reader chose
     about *this* run, carrying the id it was chosen on.

     Opening a second run reuses this component rather than remounting it, so
     choices made about the last one would otherwise carry over: a tab the new
     run has no data for, or a filter nothing in it matches, and the report
     opens on an empty panel. Reading the id back rather than resetting the
     fields in an effect keeps that a derivation — an effect would land a
     render late, after the stale view had already been drawn once.

     `tab` is null until it is picked. Test Executions now opens a scenario row
     in place and shows the written steps there, so by the time anyone asks for
     the full report the cheap questions are answered, and what is left is the
     expensive half: the screenshots, the agent's own actions, the console. */
  const [chosen, setChosen] = useState(EMPTY_VIEW);
  const view = chosen.runId === runId ? chosen : EMPTY_VIEW;
  const setView = (patch) => setChosen({ ...view, ...patch, runId });

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

  const activeTab = view.tab ?? 'steps';
  const passed = run.steps.filter((s) => s.status === 'passed').length;
  const assertions = run.steps.filter((s) => s.action?.startsWith('assert')).length;
  const scenarioTotal = run.scenarioSteps?.length || 0;
  const scenarioPassed = (run.scenarioSteps || []).filter((s) => s.status === 'passed').length;
  const allEvents = run.pageEvents || [];
  const pageErrors = allEvents.filter((event) => event.level === 'error');
  // Warnings are recorded but never decide a verdict — a 404 on a tracking
  // pixel is a fact about the page, not evidence the feature is broken. They
  // still have to be visible, or "1 page error" is unactionable.
  const pageNotices = allEvents.filter((event) => event.level !== 'error');
  const healed = run.steps.filter((s) => s.healed).length;

  /* Plain filters rather than memos: these run after the two early returns
     above, where a hook cannot go, and the longest list here is tens of rows. */
  const stepNeedle = view.stepQuery.trim().toLowerCase();
  const stepMatch = (STEP_FILTERS.find((f) => f.id === view.stepFilter) || STEP_FILTERS[0]).match;
  const visibleSteps = run.steps.filter((step) => {
    if (!stepMatch(step)) return false;
    if (!stepNeedle) return true;
    return [step.action, step.target, step.value, step.reason, step.message]
      .some((field) => field && String(field).toLowerCase().includes(stepNeedle));
  });
  /* A short list is read, not searched, and a toolbar over six rows is just
     more to look at. */
  const stepsNeedToolbar = run.steps.length > 8;

  const eventNeedle = view.eventQuery.trim().toLowerCase();
  const eventMatch = (event) => !eventNeedle
    || [event.text, event.url, event.kind, event.resourceType]
      .some((field) => field && String(field).toLowerCase().includes(eventNeedle));
  const shownErrors = pageErrors.filter(eventMatch);
  const shownNotices = pageNotices.filter(eventMatch);

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
      {/* A run started from a Test Set is titled with its goal, so printing
          both gives the same sentence twice under itself. */}
      {run.goal && run.goal !== run.title && (
        <p className="run-detail-goal">{run.goal}</p>
      )}

      <div className="run-stats">
        <div className="stat">
          <StatusBadge status={run.status} />
        </div>
        {/* For a written scenario the headline is its own steps, not the
            agent's clicks. Every click can succeed while the step they were
            meant to prove does not — a step runs out of its action budget, or
            the agent closes it as failed — and "36/36 steps passed" beside a
            red verdict reads as a contradiction when it is simply counting
            something else. Both are shown, each named for what it is. */}
        {scenarioTotal > 0 ? (
          <>
            <div className="stat">
              <span className={`stat-value ${scenarioPassed < scenarioTotal ? 'bad' : ''}`}>
                {scenarioPassed}/{scenarioTotal}
              </span>
              <span className="stat-label">scenario steps</span>
            </div>
            <div className="stat">
              <span className="stat-value">{passed}/{run.step_count}</span>
              <span className="stat-label">agent actions</span>
            </div>
          </>
        ) : (
          <div className="stat">
            <span className="stat-value">
              {passed}/{run.step_count}
            </span>
            <span className="stat-label">agent actions</span>
          </div>
        )}
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

      {/* The agent's own actions lead, because they are the half of the report
          that is only here: the screenshots hang off them. Neither of the first
          two tabs carries a count — the tiles above already read "34/36 agent
          actions" and "9/12 scenario steps", which is the same number and more
          of the answer. The last two do, because nothing else states them. */}
      <div className="detail-tabs">
        <button
          className={`detail-tab ${activeTab === 'steps' ? 'active' : ''}`}
          onClick={() => setView({ tab: 'steps' })}
        >
          <ImageIcon size={13} />
          {run.scenarioSteps?.length > 0 ? 'Agent actions' : 'Steps'}
        </button>
        {run.scenarioSteps?.length > 0 && (
          <button
            className={`detail-tab ${activeTab === 'scenario' ? 'active' : ''}`}
            onClick={() => setView({ tab: 'scenario' })}
          >
            <ListChecks size={13} />
            Scenario steps
          </button>
        )}
        {allEvents.length > 0 && (
          <button
            className={`detail-tab ${activeTab === 'errors' ? 'active' : ''}`}
            onClick={() => setView({ tab: 'errors' })}
          >
            <AlertTriangle size={13} />
            Page errors
            <span className="tab-count">{allEvents.length}</span>
          </button>
        )}
        {run.artifacts?.length > 0 && (
          <button
            className={`detail-tab ${activeTab === 'artifacts' ? 'active' : ''}`}
            onClick={() => setView({ tab: 'artifacts' })}
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
        <>
          {stepsNeedToolbar && (
            <div className="report-toolbar">
              <div className="report-toolbar-chips">
                {STEP_FILTERS.map(({ id, label, match }) => {
                  const count = id === 'all' ? run.steps.length : run.steps.filter(match).length;
                  if (!count && id !== 'all') return null;
                  return (
                    <button
                      key={id}
                      className={`filter-chip ${view.stepFilter === id ? 'active' : ''}`}
                      onClick={() => setView({ stepFilter: id })}
                    >
                      {label}
                      <span className="filter-count">{count}</span>
                    </button>
                  );
                })}
              </div>
              <ReportSearch
                value={view.stepQuery}
                onChange={(next) => setView({ stepQuery: next })}
                placeholder="Find an action, element or message…"
                label="Search agent actions"
              />
            </div>
          )}

          {run.steps.length === 0 ? (
            /* Nothing was filtered away — the run never recorded an action.
               Offering to clear a filter here would send the reader looking
               for a control that is not on screen. */
            <p className="muted small report-none">
              This run recorded no agent actions. It ended before the first one
              was taken — the banner above usually says why.
            </p>
          ) : visibleSteps.length === 0 ? (
            <p className="muted small report-none">
              No action matches this filter.{' '}
              <button
                className="linklike"
                onClick={() => setView({ stepFilter: 'all', stepQuery: '' })}
              >
                Show all {run.steps.length}
              </button>
            </p>
          ) : (
            <ol className="report-steps">
              {visibleSteps.map((step) => (
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
        </>
      )}

      {activeTab === 'errors' && (
        <div className="page-errors">
          {/* Console and network noise is the longest list on this page — a
              single search over both halves is what makes it readable, because
              the thing being looked for is usually a host or a status code
              rather than a section. */}
          {allEvents.length > 8 && (
            <div className="report-toolbar">
              <span className="muted small">
                Koşum sürerken tarayıcının kendi bildirdikleri.
              </span>
              <ReportSearch
                value={view.eventQuery}
                onChange={(next) => setView({ eventQuery: next })}
                placeholder="Adres, metin ya da tür ara…"
                label="Sayfa olaylarında ara"
              />
            </div>
          )}

          {shownErrors.length > 0 && (
            <>
              <h4 className="scan-section">
                Sonucu etkileyenler ({shownErrors.length})
              </h4>
              <ul className="error-list">
                {shownErrors.map((event, index) => (
                  <PageEventRow key={`e-${index}`} event={event} />
                ))}
              </ul>
            </>
          )}

          {shownNotices.length > 0 && (
            <>
              <h4 className="scan-section">Bilgi amaçlı ({shownNotices.length})</h4>
              {/* True of every run and read once, so it waits behind a line
                  instead of taking a paragraph above the list every visit. */}
              <details className="run-fold">
                <summary>Bunlar neden sonucu düşürmedi?</summary>
                <p className="muted small run-fold-body">
                  Kaydedildi ama koşumu düşürmedi: bir takip pikselinin ya da
                  fontun 404 vermesi, tıklanan kontrolün bozuk olduğunu göstermez.
                </p>
              </details>
              <ul className="error-list">
                {shownNotices.map((event, index) => (
                  <PageEventRow key={`n-${index}`} event={event} muted />
                ))}
              </ul>
            </>
          )}

          {shownErrors.length === 0 && shownNotices.length === 0 && (
            <p className="muted small report-none">
              Bu aramaya uyan olay yok.{' '}
              <button className="linklike" onClick={() => setView({ eventQuery: '' })}>
                {allEvents.length} olayın hepsini göster
              </button>
            </p>
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

      {/* Exporting writes a new script; reading the report is why anyone is
          here. It used to sit in the tab strip at the same weight as the
          evidence, which is how a reader looking for a screenshot ended up
          reading Playwright. */}
      {/* Keyed on the run so a second one opens with the fold shut. `open` is
          DOM state React does not reset on its own, and a fold left open while
          the flag beside it was reset would sit there empty. */}
      <details
        key={run.id}
        className="run-fold run-export-fold"
        onToggle={(event) => setView({ exportOpen: event.currentTarget.open })}
      >
        <summary>
          <Code2 size={13} />
          Export this run as a script
        </summary>
        <div className="run-fold-body">
          {view.exportOpen && <ExportPanel run={run} />}
        </div>
      </details>
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
  const [platform, setPlatform] = useState(DEFAULT_PLATFORM);
  /* Counted on the server over every run, not over the page just fetched: this
     list pages fifty at a time, so a count taken from what is loaded would say
     "Mobile 0" until the tester scrolled far enough to prove otherwise. */
  const [platformCounts, setPlatformCounts] = useState(null);
  /* And within Mobile, which phone. An iPhone run and a Pixel run are two
     different apps with different selectors and different bugs; read in one
     list, a failure on one looked like a failure on both. Server-side, like
     the platform above it, because the list pages fifty at a time. */
  const [os, setOs] = useState('ios');
  const [osCounts, setOsCounts] = useState(null);
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
        const data = await api.runs(
          PAGE_SIZE, debounced, 0, platform, platform === 'mobile' ? os : null,
        );
        if (!cancelled) {
          setRuns(data.runs || []);
          setHasMore(Boolean(data.hasMore));
          setPlatformCounts(data.counts || null);
          setOsCounts(data.osCounts || null);
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
  }, [reloadKey, debounced, platform, os, toast]);

  const loadMore = async () => {
    setLoadingMore(true);
    try {
      const data = await api.runs(
        PAGE_SIZE, debounced, runs.length, platform,
        platform === 'mobile' ? os : null,
      );
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
        <PlatformTabs value={platform} onChange={setPlatform} counts={platformCounts} />
        {platform === 'mobile' && (
          <PlatformTabs
            options={OS_TABS} value={os} onChange={setOs}
            counts={loading ? null : osCounts} sub
          />
        )}
      </header>

      {/* Search and both filters read as one control, on one line where the
          width allows. Stacked as three rows they pushed the list itself off
          the first screen on a laptop. */}
      <div className="runs-toolbar">
        <div className="run-search">
          <Search size={14} />
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search runs by title, goal, device or URL…"
            aria-label="Search runs"
          />
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
      </div>

      {/* The tab above counts every run on this platform; the chips count the
          page that is loaded. With 112 runs behind a 50-row page the two
          numbers look like a contradiction, so the difference is said out loud
          rather than left to be worked out. */}
      {(platform === 'mobile' ? osCounts?.[os] : platformCounts?.[platform]) > runs.length && (
        <p className="muted small run-scope">
          Showing the {runs.length} most recent of
          {' '}{platform === 'mobile' ? osCounts[os] : platformCounts[platform]}.
          The counts above are of these.
        </p>
      )}

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
                {/* Clamped to two lines, and these names carry the route, the
                    passenger mix and the cabin — on a laptop the third line is
                    where the useful half of that lives, with no way to read it
                    but the tooltip. */}
                <span className="run-row-title" title={run.title}>{run.title}</span>
                {/* Where it ran, and nothing else. This line used to carry the
                    platform and the model as well: the platform is the tab
                    above, and the model is the same on every row until someone
                    switches provider — fifty repetitions of the same two facts,
                    taking the width the title needed. Both are still on the run
                    itself, which is where a question about one is asked. */}
                <span className="run-row-meta">{runTarget(run)}</span>
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
