import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle, ArrowLeft, CheckCircle2, ChevronRight, CircleSlash, Clock, Code2, Copy,
  Download, ExternalLink, FileArchive, FileCode2, Film, Image as ImageIcon, Layers,
  ListChecks, Loader2, Play, RefreshCw, Search, Trash2, Wrench, X, XCircle,
} from 'lucide-react';
import { api } from '../api';
import { DEFAULT_PLATFORM, PlatformTabs } from '../components/PlatformTabs';
import { RunPlayer } from '../components/RunPlayer';
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

// A date with its time, for the head of an execution: "30 Sep, 19:22".
function formatStart(epochSeconds) {
  if (!epochSeconds) return '';
  return new Date(epochSeconds * 1000).toLocaleString(undefined, {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

/* "Hotel - Guests | [Jolly: IST] adults per room 5 - raise adults to the
   limit and control a further press is refused" — the scenario standard names
   the area, then the scenario, then what it controls. Read whole, every row of
   one set opened with the same area and the part that tells two rows apart
   came last, cut off. Split, the name leads, the area goes under it, and what
   it controls follows the name for as long as the line has room. A title not
   written to the standard — a goal typed in the chat — stays whole. */
function splitTitle(title) {
  const text = (title || '').trim();
  const bar = text.indexOf(' | ');
  if (bar <= 0) return { area: null, name: text || 'Untitled run', detail: null };
  const rest = text.slice(bar + 3);
  const dash = rest.indexOf(' - ');
  return {
    area: text.slice(0, bar),
    name: dash > 0 ? rest.slice(0, dash) : rest,
    detail: dash > 0 ? rest.slice(dash + 3) : null,
  };
}

/* Which runs need someone first: one still going, then the ones that failed,
   then the ones stopped, then the ones that passed. Within each, in the order
   the scenarios stand in their Test Set. */
const STATUS_RANK = { running: 0, failed: 1, cancelled: 2, passed: 3 };
const PRIORITY_RANK = { critical: 4, high: 3, medium: 2, low: 1 };

function byUrgency(a, b) {
  const rank = (STATUS_RANK[a.status] ?? 4) - (STATUS_RANK[b.status] ?? 4);
  if (rank) return rank;
  const ai = a.case_idx ?? Number.MAX_SAFE_INTEGER;
  const bi = b.case_idx ?? Number.MAX_SAFE_INTEGER;
  if (ai !== bi) return ai - bi;
  return (b.started_at || 0) - (a.started_at || 0);
}

function tallyOf(runs) {
  const tally = { passed: 0, failed: 0, cancelled: 0, running: 0 };
  for (const run of runs) {
    if (run.status in tally) tally[run.status] += 1;
  }
  return tally;
}

/* One group per execution, in the order of each one's newest run, so the one
   that just ran is on top. Runs started outside any execution — from the chat,
   or by hand in a workspace — share one group of their own. */
function groupRuns(runs) {
  const groups = new Map();
  for (const run of runs) {
    const key = run.suite_run_id || 'single';
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        suiteRunId: run.suite_run_id || null,
        name: run.suite_run_id ? (run.suite_run_name || 'Execution') : 'Single runs',
        startedAt: run.suite_run_started_at || run.started_at,
        size: run.suite_run_size || null,
        runs: [],
      });
    }
    groups.get(key).runs.push(run);
  }
  for (const group of groups.values()) {
    group.tally = tallyOf(group.runs);
    group.rows = rowsOf(group);
  }
  return [...groups.values()];
}

/* Runs of one execution that failed the same way are one row. Fourteen
   scenarios stopped by one rate limit read as fourteen failures to work
   through; they are one thing that went wrong, and the row says which
   scenarios it took with it. Only within one execution — the same words from
   two executions are two findings — and never a passed run. */
function rowsOf(group) {
  const sorted = [...group.runs].sort(byUrgency);
  const rows = [];
  const same = new Map();
  for (const run of sorted) {
    const headline = group.suiteRunId && run.status === 'failed' && run.failure?.headline;
    if (!headline) {
      rows.push({ key: run.id, runs: [run], many: false });
      continue;
    }
    const key = `${group.suiteRunId}::${headline.trim().toLowerCase().replace(/\s+/g, ' ')}`;
    if (!same.has(key)) {
      const row = { key, runs: [], many: false, headline };
      same.set(key, row);
      rows.push(row);
    }
    same.get(key).runs.push(run);
  }
  return rows.map((row) => (row.runs.length > 1
    ? { ...row, many: true }
    : { ...row, key: row.runs[0].id, many: false }));
}

function worstPriority(runs) {
  return runs.map((run) => run.priority).filter(Boolean).sort(
    (a, b) => (PRIORITY_RANK[b.toLowerCase()] || 0) - (PRIORITY_RANK[a.toLowerCase()] || 0),
  )[0] || null;
}

/* The scenario's own steps where it has them: a written scenario is judged on
   those, and its agent actions can all pass while a step fails — "8/8" beside
   a red verdict. A run from the chat has no written steps, only actions. */
function stepsOf(run) {
  if (run.scenario_total > 0) {
    return { text: `${run.scenario_passed}/${run.scenario_total}`,
             hint: 'Scenario steps passed, of those the run reached' };
  }
  if (run.step_count) {
    return { text: `${run.step_count - run.failed_count}/${run.step_count}`,
             hint: 'Agent actions passed' };
  }
  return { text: '—', hint: 'Nothing recorded' };
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

/* In a list the verdict is an icon in its colour: the word took a column of
   its own on every row, and the group heading already counts them. */
function RunIcon({ status }) {
  const meta = STATUS_META[status] || STATUS_META.cancelled;
  const Icon = meta.icon;
  return (
    <span className={`run-icon ${meta.className}`} title={meta.label}>
      <Icon size={15} className={status === 'running' ? 'spin' : ''} aria-hidden="true" />
      <span className="visually-hidden">{meta.label}</span>
    </span>
  );
}

/* One run: what the scenario is, and — when it did not pass — why, in the same
   words the bug raised from it uses. */
function RunLine({ run, onOpen, why = true }) {
  const { area, name, detail } = splitTitle(run.title);
  const failure = why ? run.failure : null;
  const steps = stepsOf(run);
  return (
    <button type="button" className={`run-line s-${run.status}`} onClick={() => onOpen(run.id)}
            title={run.title}>
      <RunIcon status={run.status} />
      <span className="run-line-main">
        <span className="run-line-title">
          {run.case_idx != null && <span className="run-line-idx">#{run.case_idx}</span>}
          <span className="run-line-name">{name}</span>
          {detail && <span className="run-line-detail">{detail}</span>}
        </span>
        <span className="run-line-sub">
          {area && <span className="run-line-area">{area}</span>}
          {failure ? (
            <span className={`run-line-why ${failure.isAppDefect ? '' : 'soft'}`}>
              {failure.step != null && <span className="run-line-step">Step {failure.step}</span>}
              {failure.headline}
            </span>
          ) : !area && <span className="run-line-area">{runTarget(run)}</span>}
        </span>
      </span>
      <span className="run-line-priority">
        {run.priority && (
          <span className={`priority-tag p-${run.priority.toLowerCase()}`}>{run.priority}</span>
        )}
      </span>
      <span className="run-line-steps" title={steps.hint}>{steps.text}</span>
      <span className="run-line-duration">{formatDuration(run.duration_ms)}</span>
      <span className="run-line-when">{formatWhen(run.started_at)}</span>
    </button>
  );
}

/* Several runs that failed the same way: the reason once, the scenarios it
   took under it, each still opening its own report. */
function SameFailureRow({ row, open, onToggle, onOpen }) {
  const numbers = row.runs.map((run) => run.case_idx).filter((idx) => idx != null);
  const soft = !row.runs[0].failure?.isAppDefect;
  const priority = worstPriority(row.runs);
  const total = row.runs.reduce((sum, run) => sum + (run.duration_ms || 0), 0);
  return (
    <>
      <button
        type="button"
        className={`run-line run-line-same s-failed ${open ? 'open' : ''}`}
        aria-expanded={open}
        onClick={onToggle}
        title={row.runs.map((run) => run.title).join('\n')}
      >
        <RunIcon status="failed" />
        <span className="run-line-main">
          <span className="run-line-title">
            <span className={`run-line-name ${soft ? 'soft' : ''}`}>{row.headline}</span>
          </span>
          <span className="run-line-sub">
            <span className="same-count">{row.runs.length} scenarios</span>
            <span className="run-line-area">{numbers.map((idx) => `#${idx}`).join(' · ')}</span>
          </span>
        </span>
        <span className="run-line-priority">
          {priority && <span className={`priority-tag p-${priority.toLowerCase()}`}>{priority}</span>}
        </span>
        <span className="run-line-steps" />
        <span className="run-line-duration" title="All of them together">{formatDuration(total)}</span>
        <span className="run-line-when">
          <ChevronRight size={15} className="run-line-chevron" aria-hidden="true" />
        </span>
      </button>
      {open && (
        <ul className="run-line-members">
          {row.runs.map((run) => (
            <li key={run.id}><RunLine run={run} onOpen={onOpen} why={false} /></li>
          ))}
        </ul>
      )}
    </>
  );
}

/* How an execution went, as a bar before it is read as numbers. */
function TallyBar({ tally }) {
  const total = tally.passed + tally.failed + tally.cancelled + tally.running;
  if (!total) return null;
  return (
    <span className="runs-tally-bar" aria-hidden="true">
      {['failed', 'running', 'cancelled', 'passed'].map((status) => (tally[status] > 0 && (
        <span key={status} className={`seg ${status}`}
              style={{ flexGrow: tally[status] }} />
      )))}
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

/* Where the run was pointed, as the value and the line under it: the host
   says which environment, the path which page. The whole address used to be
   the value and again the line under it. */
function targetOf(run) {
  const address = run.app_id || '';
  if (address.startsWith('http')) {
    try {
      const url = new URL(address);
      return { main: url.host, sub: `${run.platform || 'Web'} · ${url.pathname}${url.search}` };
    } catch {
      // Not an address after all: said as it is below.
    }
  }
  return {
    main: run.device_name || address || '—',
    sub: [run.platform, run.device_name ? address : null].filter(Boolean).join(' · '),
  };
}

/* What the failure comes down to, labelled the way Bug Report labels it —
   the step the model judged, what it expected, what it found, the action under
   it and why the tool thinks so. Read from the run the way the bug is, so a
   report and the bug raised from it never tell two stories. */
function FailureFacts({ failure }) {
  const facts = [
    failure.expected && { key: 'expected', label: 'Expected', tone: 'success', value: failure.expected },
    failure.actual && { key: 'actual', label: 'Actual', tone: 'danger', value: failure.actual },
    failure.failingAction && {
      key: 'failing', label: 'Failing action', tone: 'warning',
      value: (
        <>
          <code className="fact-code">{failure.failingAction.action}</code>
          {' '}{failure.failingAction.message}
        </>
      ),
    },
    failure.codeText && {
      key: 'cause', label: 'Cause', tone: 'purple',
      value: (
        <>
          <span title={failure.code}>{failure.codeText}</span>
          {!failure.isAppDefect && <span className="fact-flag">Not an app defect</span>}
        </>
      ),
    },
  ].filter(Boolean);
  return (
    <section className="run-failure">
      <h3 className="run-failure-title">
        <XCircle size={15} aria-hidden="true" />
        {failure.step != null ? `Step ${failure.step} failed` : 'Why it did not pass'}
        <span className="run-failure-headline">{failure.headline}</span>
      </h3>
      {facts.length > 0 && (
        <dl className="facts">
          {facts.map((fact) => (
            <div className="fact" key={fact.key}>
              <dt className={`fact-label tone-${fact.tone}`}>{fact.label}</dt>
              <dd className="fact-value">{fact.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

function RunDetail({ runId, onBack, onDeleted, replaySessionFor, onReplay, onOpenExecution }) {
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

  const { area, name, detail } = splitTitle(run.title);
  const target = targetOf(run);
  // A failed run opens on what failed. Its error line says the same thing
  // again — "1 of 5 steps failed. Step 5 failed: …" — so it is only shown
  // when it says something the facts do not.
  const failure = run.status === 'failed' ? run.failure : null;
  const said = failure?.actual ? failure.actual.slice(0, 80) : null;
  const errorIsNews = run.error && !(said && run.error.includes(said));

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
            disabled={!replaySessionFor?.(run)}
            title={
              replaySessionFor?.(run)
                ? 'Re-execute the recorded steps, repairing any that no longer match'
                : (run.platform || '').toLowerCase() === 'web' || run.kind === 'web'
                  ? 'Open a page in Web first — a web run replays on a web page'
                  : 'Connect a device first — a device run replays on a device'
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

      <div className="run-head">
        <div className="run-crumbs">
          {run.suite_run_id ? (
            onOpenExecution ? (
              <button type="button" className="run-crumb-execution"
                      onClick={() => onOpenExecution(run.suite_run_id)}
                      title="Open this execution on Test Executions">
                <Layers size={13} aria-hidden="true" />
                {run.suite_run_name || 'Execution'}
              </button>
            ) : (
              <span className="run-crumb-execution"><Layers size={13} aria-hidden="true" />
                {run.suite_run_name || 'Execution'}</span>
            )
          ) : <span>Single run</span>}
          {run.case_idx != null && <span className="run-crumb-idx">#{run.case_idx}</span>}
          {area && <span>{area}</span>}
        </div>
        <h2 className="run-detail-title">{name}</h2>
        {detail && <p className="run-detail-sub">{detail}</p>}
      </div>
      {/* A run started from a Test Set is titled with its goal, so printing
          both gives the same sentence twice under itself. Folded: it is the
          precondition and the brief, read once, and it pushed the verdict
          below the first screen on every scenario that had one. */}
      {run.goal && run.goal !== run.title && (
        <details className="run-fold run-goal-fold">
          <summary>Goal and precondition</summary>
          <p className="run-fold-body run-detail-goal">{run.goal}</p>
        </details>
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
        <div className="stat" title={run.app_id || undefined}>
          <span className="stat-value">{target.main}</span>
          <span className="stat-label">{target.sub}</span>
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

      {failure && <FailureFacts failure={failure} />}

      {errorIsNews && (
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
            /* Keyed on the run: a second report opens on its own recording,
               not partway through the last one's. */
            <RunPlayer key={run.id} run={run} steps={visibleSteps} />
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

const PRIORITY_OPTIONS = [
  ['all', 'Any priority'], ['Critical', 'Critical'], ['High', 'High'],
  ['Medium', 'Medium'], ['Low', 'Low'], ['ungraded', 'Ungraded'],
];

export function RunsPage({ replaySessionFor, onReplay, selectedRunId, onSelectRun,
                           onOpenExecution = null }) {
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
  // Executions the tester opened or closed; the rest follow the default — the
  // newest open, the others one line each.
  const [folded, setFolded] = useState({});
  // Rows of several runs that failed the same way, opened to show them.
  const [unfolded, setUnfolded] = useState({});

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

  const groups = useMemo(() => groupRuns(filtered), [filtered]);

  /* Open by default: the newest execution. Narrowed by a filter or a search,
     every one that has a match — a match folded out of sight is not found. */
  const narrowed = filter !== 'all' || priority !== 'all' || Boolean(debounced);
  const isOpen = (group, index) => {
    if (narrowed) return true;
    if (group.key in folded) return !folded[group.key];
    return index === 0;
  };

  if (selectedRunId) {
    return (
      <main className="page">
        <RunDetail
          runId={selectedRunId}
          replaySessionFor={replaySessionFor}
          onReplay={onReplay}
          onOpenExecution={onOpenExecution}
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
          <p className="page-subtitle">
            Every agent run, grouped by the execution it ran in, with its steps,
            assertions and screenshots.
          </p>
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
          {/* `running` belongs here: without it the chips counted 27 + 21 + 1
              against an All of 50, and the run in flight — the one a tester
              opens the page to watch — could only be found by scrolling. */}
          {['all', 'passed', 'failed', 'running', 'cancelled'].map((value) => (
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

        {/* A second row of six chips was the widest thing on the page and the
            question least often asked of it, so it is one control that says
            what it is set to. */}
        <select
          className={`runs-priority ${priority !== 'all' ? 'active' : ''}`}
          value={priority}
          onChange={(event) => setPriority(event.target.value)}
          aria-label="Priority"
          title="Ungraded: runs started from the chat, outside any Test Set"
        >
          {PRIORITY_OPTIONS.map(([value, label]) => {
            const count = value === 'all'
              ? runs.length
              : value === 'ungraded'
                ? runs.filter((run) => !run.priority).length
                : runs.filter((run) => run.priority === value).length;
            /* An option with nothing behind it is noise — unless it is the one
               doing the filtering. Switching platform recounts every option,
               so a priority that exists on Web and not on Mobile vanished
               while it was still narrowing the list, and the page said "No
               runs yet" with the way out no longer on screen. */
            if (!count && value !== 'all' && priority !== value) return null;
            return <option key={value} value={value}>{label} ({count})</option>;
          })}
        </select>
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
        /* Three different nothings, and they used to be one. A filter that
           matched none of what is loaded is undone by clearing a chip; a
           platform that has never been run is not; and neither is the same as
           still loading. The one sentence they shared sent the reader to a
           "Studio" and an "Agent tab" that this product does not have. */
        <div className="empty-state">
          {loading ? <Loader2 size={34} className="spin" /> : <Clock size={34} />}
          <h3>
            {loading ? 'Loading runs…'
              : (filter !== 'all' || priority !== 'all' || debounced)
                ? 'Nothing matches'
                : `No ${platform} runs yet`}
          </h3>
          <p>
            {loading ? 'Reading the most recent runs.'
              : (filter !== 'all' || priority !== 'all' || debounced)
                ? `${runs.length} run${runs.length === 1 ? '' : 's'} here, none of `
                  + 'them matching the filters above.'
                : 'Run a Test Set from Test Executions, or drive a session on '
                  + `${platform === 'mobile' ? 'Mobile' : 'Web'} — every run lands `
                  + 'here with a full report and an exportable script.'}
          </p>
        </div>
      ) : (
        <div className="runs-groups">
          {groups.map((group, index) => {
            const open = isOpen(group, index);
            const listId = `runs-group-${index}`;
            const partial = group.size && group.size > group.runs.length;
            return (
              <section key={group.key} className={`runs-group ${open ? 'open' : ''}`}>
                <div className="runs-group-head">
                  <button
                    type="button"
                    className="runs-group-toggle"
                    aria-expanded={open}
                    aria-controls={listId}
                    onClick={() => setFolded((current) => ({ ...current, [group.key]: open }))}
                    title={group.suiteRunId ? undefined
                      : 'Started from the chat or a workspace, outside any execution'}
                  >
                    <ChevronRight size={15} className="runs-group-chevron" aria-hidden="true" />
                    <span className="runs-group-name">{group.name}</span>
                    <span className="runs-group-when">{formatStart(group.startedAt)}</span>
                  </button>
                  <span className="runs-group-summary">
                    <TallyBar tally={group.tally} />
                    <span className="runs-group-tally">
                      {group.tally.running > 0 && <span className="t-running">{group.tally.running} running</span>}
                      {group.tally.failed > 0 && <span className="t-failed">{group.tally.failed} failed</span>}
                      {group.tally.cancelled > 0 && <span className="t-stopped">{group.tally.cancelled} stopped</span>}
                      {group.tally.passed > 0 && <span className="t-passed">{group.tally.passed} passed</span>}
                    </span>
                    {/* A page of fifty can end partway through an execution;
                        the heading says so rather than calling it complete. */}
                    <span className="runs-group-count"
                          title={partial ? 'Load more below to see the rest' : undefined}>
                      {group.runs.length}{partial ? ` of ${group.size}` : ''} run{(partial ? group.size : group.runs.length) === 1 ? '' : 's'}
                    </span>
                    {group.suiteRunId && onOpenExecution && (
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm runs-group-open"
                        onClick={() => onOpenExecution(group.suiteRunId)}
                        title="Open this execution on Test Executions"
                      >
                        <ExternalLink size={13} />
                        Execution
                      </button>
                    )}
                  </span>
                </div>
                {open && (
                  <ul className="runs-group-list" id={listId}>
                    {group.rows.map((row) => (
                      <li key={row.key}>
                        {row.many ? (
                          <SameFailureRow
                            row={row}
                            open={Boolean(unfolded[row.key])}
                            onToggle={() => setUnfolded((current) => ({
                              ...current, [row.key]: !current[row.key],
                            }))}
                            onOpen={onSelectRun}
                          />
                        ) : (
                          <RunLine run={row.runs[0]} onOpen={onSelectRun} />
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            );
          })}
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
