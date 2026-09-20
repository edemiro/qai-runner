import { useCallback, useEffect, useState } from 'react';
import {
  Ban, Bug, CheckCircle2, ChevronRight, ClipboardList, Clock, Download, Loader2, Radio, Square,
  Trash2, X, XCircle,
} from 'lucide-react';

import { EmptyState } from '../components/EmptyState';
import { DEFAULT_PLATFORM, PlatformTabs, PlatformTag } from '../components/PlatformTabs';
import { OS_TABS, matchesOs } from '../lib/platforms';
import { api } from '../api';
import { useToast } from '../hooks/useToast';

/**
 * Test Executions — a run of a Test Set, read the way a tester reads one.
 *
 * The question an execution answers is "which scenarios passed", so the
 * scenario is the row: its number in the Test Set, its priority, its verdict.
 * A failed Critical and a failed Low are not the same morning, which is why
 * priority sits on the row rather than being something you go and look up.
 *
 * Reports live here rather than on a separate screen: the JUnit and JSON a
 * build server reads are of the execution, not of any one scenario.
 */

const VERDICT = {
  passed: { icon: CheckCircle2, className: 'verdict-pass', label: 'Pass' },
  failed: { icon: XCircle, className: 'verdict-fail', label: 'Fail' },
  running: { icon: Loader2, className: 'verdict-running', label: 'Running' },
  // Its own state, not a failure: someone ended this run on purpose, and
  // reporting it red would have people chasing a break that never happened.
  cancelled: { icon: Ban, className: 'verdict-cancelled', label: 'Stopped' },
};

function Verdict({ status }) {
  const shape = VERDICT[status] || { icon: Clock, className: 'verdict-other', label: status || '—' };
  const Icon = shape.icon;
  return (
    <span className={`verdict ${shape.className}`}>
      <Icon size={13} className={status === 'running' ? 'spin' : ''} />
      {shape.label}
    </span>
  );
}

function when(seconds) {
  if (!seconds) return '—';
  return new Date(seconds * 1000).toLocaleString();
}

/** Short enough to sit beside a platform badge on one line. The full date is
 *  still on the execution's own header, where there is room for it. */
function whenShort(seconds) {
  if (!seconds) return '—';
  const d = new Date(seconds * 1000);
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
    + ' · ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

function duration(ms) {
  if (!ms) return '—';
  return ms < 60000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
}

export function ExecutionsPage({
  onOpenRun, onWatch = null, focusId = null, onFocused = null,
  // Where a raised bug goes, so the tester lands on it rather than being told
  // it exists somewhere.
  onOpenBugs = null,
}) {
  const toast = useToast();
  const [executions, setExecutions] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [execution, setExecution] = useState(null);
  const [loading, setLoading] = useState(true);
  const [platform, setPlatform] = useState(DEFAULT_PLATFORM);
  // Which phone, once Mobile is the platform. Not shown on Web, where the
  // question does not arise.
  const [os, setOs] = useState('ios');

  const counts = {
    web: executions.filter((e) => (e.kind || 'web') !== 'mobile').length,
    mobile: executions.filter((e) => e.kind === 'mobile').length,
  };
  const onPlatform = executions.filter((e) => (e.kind || 'web') === platform);

  /* Within Mobile, iOS and Android are read apart for the same reason their
     Test Sets are: they are different screens, and a pass rate that mixes them
     describes neither. An execution that predates the field shows on both. */
  const osCounts = {
    ios: onPlatform.filter((e) => e.os !== 'android').length,
    android: onPlatform.filter((e) => e.os !== 'ios').length,
  };
  const visible = platform === 'mobile'
    ? onPlatform.filter((e) => matchesOs(e, os))
    : onPlatform;

  /* Derived rather than synced through an effect: switching platform with an
     execution of the other one open used to leave the detail pane reporting a
     run the list beside it no longer contained. Falling through to the first
     visible execution keeps the two panes describing the same thing. */
  const shownId = visible.some((item) => item.id === selectedId)
    ? selectedId
    : (visible[0]?.id ?? null);

  const load = useCallback(async () => {
    try {
      const data = await api.suiteRuns(null, 50);
      setExecutions(data.suiteRuns);
      // An execution just started elsewhere wins the selection, so starting one
      // lands on it rather than on whatever ran last.
      setSelectedId((current) => focusId || current || data.suiteRuns[0]?.id || null);
      if (focusId) {
        // …and brings its platform with it. Starting a mobile execution and
        // landing on a page filtered to web would hide the run just started.
        const focused = data.suiteRuns.find((item) => item.id === focusId);
        if (focused) setPlatform(focused.kind === 'mobile' ? 'mobile' : 'web');
        onFocused?.();
      }
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  }, [toast, focusId, onFocused]);

  useEffect(() => {
    let cancelled = false;
    (async () => { if (!cancelled) await load(); })();
    return () => { cancelled = true; };
  }, [load]);

  const removeExecution = async (id) => {
    try {
      await api.deleteSuiteRun(id);
      setSelectedId((current) => (current === id ? null : current));
      setExecution((current) => (current?.id === id ? null : current));
      await load();
      toast.success('Execution deleted.');
    } catch (err) {
      toast.error(err.message);
    }
  };

  const [stopping, setStopping] = useState(false);
  // Which scenario's bug is being composed, and the draft once it arrives.
  const [draft, setDraft] = useState(null);
  const [drafting, setDrafting] = useState(null);

  /* Compose a bug from the failed scenario and show it before saving. The run
     already holds the step that failed, what it was meant to prove, what the
     agent saw instead and a picture of the screen — asking a tester to retype
     that is asking them to transcribe, and what happens instead is that it gets
     written from memory an hour later, or not raised at all. Nothing is filed
     until it has been read: QAi is wrong often enough — an expected string no
     page renders, a limit a scenario invented — that half of what it would
     raise is about the scenario rather than the app. */
  const raiseBug = async (run) => {
    setDrafting(run.id);
    try {
      const data = await api.bugDraft(run.id);
      if (data.existingBugId) {
        toast.info('A bug was already raised for this scenario.');
        onOpenBugs?.(execution?.kind);
        return;
      }
      setDraft({ ...data, title: data.title, detail: data.detail });
    } catch (err) {
      toast.error(err.message);
    } finally {
      setDrafting(null);
    }
  };

  const saveBug = async () => {
    try {
      await api.createBug({
        title: draft.title,
        detail: draft.detail,
        code: draft.code,
        severity: draft.severity,
        runId: draft.runId,
        suiteRunId: draft.suiteRunId,
        caseId: draft.caseId,
        caseName: draft.caseName,
        suiteName: execution?.suite_name || null,
        url: draft.url,
        screenshot: draft.screenshot || null,
      });
      setDraft(null);
      toast.success('Bug raised.');
      onOpenBugs?.(execution?.kind);
    } catch (err) {
      toast.error(err.message);
    }
  };

  /* Stops the run on the server, not just on screen. The cases still queued
     never open a browser; the ones in flight are asked to stop and wind down
     with their steps recorded. That takes a few seconds — an agent checks
     between steps and a step is mostly one model call — so the button says so
     rather than appearing to do nothing. */
  const stopExecution = async (id) => {
    setStopping(true);
    try {
      const data = await api.cancelSuiteRun(id);
      toast.info(data.live
        ? 'Stopping — the scenarios still running are finishing their current step.'
        : 'Marked stopped. The run was no longer in flight on the server.');
      await load();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setStopping(false);
    }
  };

  // A running execution is watched, not refreshed by hand: poll while it is
  // still going and stop the moment it settles.
  const isRunning = execution?.status === 'running';
  useEffect(() => {
    if (!isRunning || !shownId) return undefined;
    let cancelled = false;
    const timer = setInterval(async () => {
      try {
        const [detail, list] = await Promise.all([
          api.suiteRun(shownId),
          api.suiteRuns(null, 50),
        ]);
        if (cancelled) return;
        setExecution(detail);
        setExecutions(list.suiteRuns);
      } catch {
        /* a dropped poll is not worth interrupting the watch for */
      }
    }, 4000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [isRunning, shownId]);

  useEffect(() => {
    let cancelled = false;
    if (!shownId) {
      queueMicrotask(() => { if (!cancelled) setExecution(null); });
      return () => { cancelled = true; };
    }
    (async () => {
      try {
        const detail = await api.suiteRun(shownId);
        if (!cancelled) setExecution(detail);
      } catch (err) {
        if (!cancelled) toast.error(err.message);
      }
    })();
    return () => { cancelled = true; };
  }, [shownId, toast]);

  return (
    <main className="page">
      <header className="page-header">
        <div>
        <h1 className="page-title">Test Executions</h1>
        <p className="page-subtitle">
          Every run of a Test Set, scenario by scenario, with the report a build server reads.
        </p>
        </div>
        {/* Counts withheld until they mean something: this header renders while
            the list is still loading, and a pair of hollow zeros would say the
            platforms are empty a moment before saying they are not. */}
        <PlatformTabs
          value={platform}
          onChange={setPlatform}
          counts={loading ? null : counts}
        />
        {platform === 'mobile' && (
          <PlatformTabs
            options={OS_TABS} value={os} onChange={setOs}
            counts={loading ? null : osCounts} sub
          />
        )}
      </header>

      <div className="suites-layout">
        <aside className="suite-list card">
          {loading ? (
            <p className="muted small">Loading…</p>
          ) : executions.length === 0 ? (
            <EmptyState icon={ClipboardList} title="No executions yet" compact>
              Open a Test Set and run it — every run lands here with its verdicts
              and a report CI can read.
            </EmptyState>
          ) : visible.length === 0 ? (
            <EmptyState icon={ClipboardList} title={`No ${platform} executions`} compact>
              Nothing has been run on {platform} yet.
            </EmptyState>
          ) : (
            <ul className="execution-list">
              {visible.map((item) => (
                <li key={item.id}>
                  <button
                    className={`execution-item ${item.id === shownId ? 'active' : ''}`}
                    onClick={() => setSelectedId(item.id)}
                  >
                    <div className="execution-item-main">
                      <span className="execution-name">{item.suite_name || 'Test Set'}</span>
                      {/* The platform is the tab above, not a badge on every
                          row — repeated down the list it only crowded the name
                          and the time, which are what tell the rows apart. */}
                      <span className="execution-when">{whenShort(item.started_at)}</span>
                    </div>
                    {/* Status, not just a score: a failed execution that never
                        got a scenario off the ground reads 0/0, exactly like
                        one that is still starting. */}
                    <Verdict status={item.status} />
                    <span className={`score ${item.passed === item.total ? 'all-pass' : 'has-fail'}`}>
                      {item.passed}/{item.total}
                    </span>
                    <ChevronRight size={14} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        {!execution && !loading && (
          <div className="suite-detail">
            <div className="card">
              <EmptyState icon={ClipboardList} title="Nothing selected">
                {executions.length
                  ? 'Pick an execution on the left to see how each scenario went.'
                  : 'Run a Test Set and its results will show up here.'}
              </EmptyState>
            </div>
          </div>
        )}

        {execution && (
          <div className="suite-detail">
            <div className="card">
              <div className="card-head">
                <div className="execution-headline">
                  <h2 className="card-title">{execution.suite_name || 'Test Set'}</h2>
                  <p className="muted small execution-meta">
                    <PlatformTag kind={execution.kind} />
                    {when(execution.started_at)} · {duration(execution.duration_ms)} ·{' '}
                    {execution.workers} worker{execution.workers === 1 ? '' : 's'}
                  </p>
                  {/* The verdict before the detail: an execution is read as a
                      shape first — how much green, how much red — and only then
                      scenario by scenario. */}
                  {execution.runs.length > 0 && (
                  <div className="execution-scoreline">
                    <div className="execution-bar">
                      <div
                        className="execution-bar-pass"
                        style={{ width: `${(execution.passed / Math.max(1, execution.runs.length)) * 100}%` }}
                      />
                      <div
                        className="execution-bar-fail"
                        style={{ width: `${(execution.failed / Math.max(1, execution.runs.length)) * 100}%` }}
                      />
                    </div>
                    <span className="execution-score-text">
                      <strong>{execution.passed}</strong> passed
                      {execution.failed > 0 && <> · <strong className="fail">{execution.failed}</strong> failed</>}
                      {' '}of {execution.runs.length}
                    </span>
                  </div>
                  )}
                </div>
                <div className="row-actions">
                  {/* A way back to the live view. Starting a run lands on it,
                      but anyone who navigated away had no route back while it
                      was still going. */}
                  {onWatch && execution.status === 'running' && (
                    <button
                      className="btn btn-primary btn-sm"
                      onClick={() => onWatch(execution.id, execution.kind || 'web')}
                    >
                      <Radio size={14} /> Watch
                    </button>
                  )}
                  {execution.status === 'running' && (
                    <button
                      className="btn btn-danger btn-sm"
                      onClick={() => stopExecution(execution.id)}
                      disabled={stopping}
                      title="Stop the run on the server — queued scenarios are dropped"
                    >
                      {stopping
                        ? <Loader2 size={14} className="spin" />
                        : <Square size={14} />}
                      {stopping ? 'Stopping…' : 'Stop'}
                    </button>
                  )}
                  <a
                    className="btn btn-sm"
                    href={api.suiteReportUrl(execution.id, 'junit')}
                    download
                  >
                    <Download size={14} /> JUnit
                  </a>
                  <a
                    className="btn btn-sm"
                    href={api.suiteReportUrl(execution.id, 'json')}
                    download
                  >
                    <Download size={14} /> JSON
                  </a>
                  <button
                    className="btn-icon danger"
                    onClick={() => removeExecution(execution.id)}
                    title="Delete this execution and its runs"
                    aria-label="Delete this execution"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>

              {/* A stopped run explains itself in the same place a failed one
                  does, but not in the same colour: red here would read as a
                  break, and nothing broke. */}
              {execution.error && (
                <p className={`execution-error ${execution.status === 'cancelled' ? 'is-note' : ''}`}>
                  {execution.error}
                </p>
              )}

              {/* An execution with no scenario rows is not a normal empty list
                  — it means the run never got as far as recording one, and the
                  page used to answer that with a bar, "0 passed of 0", and a
                  screenful of nothing. Say what happened instead. */}
              {execution.runs.length === 0 && (
                <EmptyState icon={ClipboardList} title="This execution recorded no scenarios" compact>
                  It ended before any scenario produced a result
                  {execution.error ? '' : ' — usually a device that was not connected, or a run that could not start'}.
                </EmptyState>
              )}

              <ul className="scenario-results">
                {execution.runs.map((run, index) => (
                  <li key={run.id} className={`scenario-result ${run.status}`}>
                    <span className="case-idx" title="Scenario number in the Test Set">
                      #{run.case_idx ?? index + 1}
                    </span>
                    <div className="scenario-body">
                      <span className="scenario-title">{run.title}</span>
                      <span className="scenario-meta">
                        {run.step_count} step{run.step_count === 1 ? '' : 's'}
                        {run.failed_count ? ` · ${run.failed_count} failed` : ''}
                        {' · '}{duration(run.duration_ms)}
                        {run.dataset_row ? ` · ${JSON.stringify(run.dataset_row)}` : ''}
                      </span>
                      {run.error && <span className="scenario-error">{run.error}</span>}
                    </div>
                    {run.layer && <span className="layer-tag">{run.layer}</span>}
                    {run.priority && (
                      <span className={`priority-tag p-${run.priority.toLowerCase()}`}>
                        {run.priority}
                      </span>
                    )}
                    <Verdict status={run.status} />
                    {run.status === 'failed' && (
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => raiseBug(run)}
                        disabled={drafting === run.id}
                        title="Raise a bug from this failure — written from the run"
                      >
                        {drafting === run.id
                          ? <Loader2 size={14} className="spin" />
                          : <Bug size={14} />}
                        Bug
                      </button>
                    )}
                    <button
                      className="btn btn-ghost btn-sm"
                      onClick={() => onOpenRun?.(run.id)}
                      title="Open the full step-by-step report for this scenario"
                    >
                      Details
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </div>

      {/* Read before it is filed. The body is editable because the tester
          knows things the run does not — which release, which account, whether
          this is the same defect as the one raised yesterday. */}
      {draft && (
        <div className="modal-overlay" onClick={() => setDraft(null)} role="dialog" aria-label="Raise a bug">
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h2 className="modal-title"><Bug size={16} /> Raise a bug</h2>
              <button className="icon-btn" onClick={() => setDraft(null)} aria-label="Close">
                <X size={18} />
              </button>
            </div>
            <p className="modal-sub">
              Written from the run: the step that failed, what it was meant to
              prove, what happened instead, and the screen at the time.
              {!draft.isAppDefect && (
                <strong className="bug-warn">
                  {' '}This failure describes the run rather than the product —
                  check the scenario and the environment before filing it.
                </strong>
              )}
            </p>
            <div className="modal-body bug-draft">
              <label>
                Title
                <input
                  value={draft.title}
                  onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                />
              </label>
              <div className="bug-draft-meta">
                <span className="bug-code">{draft.code}</span>
                {draft.severity && (
                  <span className={`priority-tag p-${draft.severity.toLowerCase()}`}>
                    {draft.severity}
                  </span>
                )}
                {draft.screenshot && <span className="muted small">screen attached</span>}
              </div>
              <label>
                Detail
                <textarea
                  rows={16}
                  value={draft.detail}
                  onChange={(e) => setDraft({ ...draft, detail: e.target.value })}
                />
              </label>
            </div>
            <div className="modal-foot">
              <button className="btn btn-ghost" onClick={() => setDraft(null)}>Cancel</button>
              <button className="btn btn-primary" onClick={saveBug} disabled={!draft.title.trim()}>
                <Bug size={14} /> Raise it
              </button>
            </div>
          </div>
        </div>
      )}

    </main>
  );
}
