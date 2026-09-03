import { useCallback, useEffect, useState } from 'react';
import {
  CheckCircle2, ChevronRight, Clock, Download, Loader2, XCircle,
} from 'lucide-react';

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

function duration(ms) {
  if (!ms) return '—';
  return ms < 60000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
}

export function ExecutionsPage({ onOpenRun }) {
  const toast = useToast();
  const [executions, setExecutions] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [execution, setExecution] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const data = await api.suiteRuns(null, 50);
      setExecutions(data.suiteRuns);
      setSelectedId((current) => current || data.suiteRuns[0]?.id || null);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    let cancelled = false;
    (async () => { if (!cancelled) await load(); })();
    return () => { cancelled = true; };
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    if (!selectedId) {
      queueMicrotask(() => { if (!cancelled) setExecution(null); });
      return () => { cancelled = true; };
    }
    (async () => {
      try {
        const detail = await api.suiteRun(selectedId);
        if (!cancelled) setExecution(detail);
      } catch (err) {
        if (!cancelled) toast.error(err.message);
      }
    })();
    return () => { cancelled = true; };
  }, [selectedId, toast]);

  return (
    <main className="page">
      <header className="page-header">
        <div>
        <h1 className="page-title">Test Executions</h1>
        <p className="page-subtitle">
          Every run of a Test Set, scenario by scenario, with the report a build server reads.
        </p>
        </div>
      </header>

      <div className="suites-layout">
        <aside className="suite-list card">
          {loading ? (
            <p className="muted small">Loading…</p>
          ) : executions.length === 0 ? (
            <p className="muted small">
              No execution yet. Open a Test Set and run it — the result lands here.
            </p>
          ) : (
            <ul className="execution-list">
              {executions.map((item) => (
                <li key={item.id}>
                  <button
                    className={`execution-item ${item.id === selectedId ? 'active' : ''}`}
                    onClick={() => setSelectedId(item.id)}
                  >
                    <div className="execution-item-main">
                      <span className="execution-name">{item.suite_name || 'Test Set'}</span>
                      <span className="execution-when">{when(item.started_at)}</span>
                    </div>
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

        {execution && (
          <div className="suite-detail">
            <div className="card">
              <div className="card-head">
                <div className="execution-headline">
                  <h2 className="card-title">{execution.suite_name || 'Test Set'}</h2>
                  <p className="muted small">
                    {when(execution.started_at)} · {duration(execution.duration_ms)} ·{' '}
                    {execution.workers} worker{execution.workers === 1 ? '' : 's'}
                  </p>
                  {/* The verdict before the detail: an execution is read as a
                      shape first — how much green, how much red — and only then
                      scenario by scenario. */}
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
                </div>
                <div className="row-actions">
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
                </div>
              </div>

              {execution.error && <p className="execution-error">{execution.error}</p>}

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
    </main>
  );
}
