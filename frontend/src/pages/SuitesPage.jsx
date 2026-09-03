import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ChevronRight,
  Download,
  Eye,
  EyeOff,
  FileCode2,
  Layers,
  Play,
  Plus,
  Sparkles,
  Square,
  Trash2,
} from 'lucide-react';

import { ScenarioGenerator } from '../components/ScenarioGenerator';
import { api } from '../api';
import { useToast } from '../hooks/useToast';

const EMPTY_CASE = { name: '', goal: '', url: '', tags: '', dataset: '' };

/** Parse the tag input the same way everywhere: comma or space separated. */
function parseTags(raw) {
  return (raw || '')
    .split(/[,\s]+/)
    .map((tag) => tag.trim().toLowerCase())
    .filter(Boolean);
}

/**
 * A dataset is entered as CSV or JSON, because that is what teams already have.
 * Returns {rows} or {error} rather than throwing — the caller shows the error
 * inline next to the field instead of as a toast.
 */
function parseDataset(raw) {
  const text = (raw || '').trim();
  if (!text) return { rows: null };

  if (text.startsWith('[') || text.startsWith('{')) {
    try {
      const parsed = JSON.parse(text);
      const rows = Array.isArray(parsed) ? parsed : parsed.rows;
      if (!Array.isArray(rows)) return { error: 'JSON must be a list of rows.' };
      return { rows: rows.filter((row) => row && typeof row === 'object') };
    } catch (err) {
      return { error: `That is not valid JSON: ${err.message}` };
    }
  }

  const lines = text.split(/\r?\n/).filter((line) => line.trim());
  if (lines.length < 2) return { error: 'CSV needs a header row and at least one data row.' };
  const headers = lines[0].split(',').map((h) => h.trim());
  const rows = lines.slice(1).map((line) => {
    const cells = line.split(',');
    return Object.fromEntries(headers.map((header, i) => [header, (cells[i] ?? '').trim()]));
  });
  return { rows };
}

const BANDS = ['Critical', 'High', 'Medium', 'Low'];

/** What a Test Set actually protects, at a glance. A set that is all Low and
 *  one that is half Critical are different objects, and the count alone hides
 *  that. */
function PriorityStrip({ cases }) {
  const counts = BANDS.map((band) => ({
    band,
    n: cases.filter((item) => item.priority === band).length,
  })).filter((entry) => entry.n);
  if (!counts.length) return null;
  return (
    <div className="priority-strip">
      {counts.map(({ band, n }) => (
        <span key={band} className={`priority-tag p-${band.toLowerCase()}`}>
          {band} {n}
        </span>
      ))}
    </div>
  );
}

export function SuitesPage({ onOpenRun }) {
  const toast = useToast();

  const [suites, setSuites] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [suite, setSuite] = useState(null);
  const [loading, setLoading] = useState(true);

  const [newSuiteName, setNewSuiteName] = useState('');
  const [draft, setDraft] = useState(EMPTY_CASE);
  const [datasetError, setDatasetError] = useState(null);
  const [showCaseForm, setShowCaseForm] = useState(false);
  const [showGenerator, setShowGenerator] = useState(false);

  /* Selection is held by case id, not by Test Set, and survives switching sets.
     That is what makes "combine two sets into one execution" the same gesture
     as "pick four scenarios out of this one" — no separate mode for it. */
  const [picked, setPicked] = useState(() => new Map());
  const [executionName, setExecutionName] = useState('');

  const [options, setOptions] = useState({
    workers: 2,
    tags: '',
    headless: true,
    trace: true,
    recordVideo: false,
    failOnPageError: true,
  });

  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState([]);
  const [summary, setSummary] = useState(null);
  const [history, setHistory] = useState([]);
  const abortRef = useRef(null);

  const loadSuites = useCallback(async () => {
    try {
      const data = await api.suites();
      setSuites(data.suites);
      setSelectedId((current) => current || data.suites[0]?.id || null);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    let cancelled = false;
    // Wrapped rather than called directly: every setState inside loadSuites
    // happens after an await, so none of them land in this commit.
    (async () => {
      if (!cancelled) await loadSuites();
    })();
    return () => {
      cancelled = true;
    };
  }, [loadSuites]);

  useEffect(() => {
    let cancelled = false;

    if (!selectedId) {
      queueMicrotask(() => {
        if (!cancelled) setSuite(null);
      });
      return () => {
        cancelled = true;
      };
    }

    (async () => {
      try {
        const [detail, runs] = await Promise.all([
          api.suite(selectedId),
          api.suiteRuns(selectedId, 10),
        ]);
        if (cancelled) return;
        setSuite(detail);
        setHistory(runs.suiteRuns);
      } catch (err) {
        if (!cancelled) toast.error(err.message);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [selectedId, toast]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const createSuite = async (event) => {
    event.preventDefault();
    const name = newSuiteName.trim();
    if (!name) return;
    try {
      const created = await api.createSuite({ name, kind: 'web', tags: [] });
      setNewSuiteName('');
      setSelectedId(created.id);
      await loadSuites();
      toast.success(`Test set “${name}” created.`);
    } catch (err) {
      toast.error(err.message);
    }
  };

  const addCase = async (event) => {
    event.preventDefault();
    if (!suite) return;
    if (!draft.name.trim() || !draft.goal.trim()) {
      toast.warning('A case needs a name and a goal.');
      return;
    }

    const { rows, error } = parseDataset(draft.dataset);
    if (error) {
      setDatasetError(error);
      return;
    }

    try {
      await api.addCase(suite.id, {
        name: draft.name.trim(),
        goal: draft.goal.trim(),
        url: draft.url.trim() || null,
        tags: parseTags(draft.tags),
        dataset: rows,
      });
      setDraft(EMPTY_CASE);
      setDatasetError(null);
      setShowCaseForm(false);
      setSuite(await api.suite(suite.id));
      await loadSuites();
      toast.success('Case added.');
    } catch (err) {
      toast.error(err.message);
    }
  };

  const removeCase = async (caseId) => {
    try {
      await api.deleteCase(caseId);
      setSuite(await api.suite(suite.id));
      await loadSuites();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const toggleCase = async (item) => {
    try {
      await api.updateCase(item.id, {
        name: item.name, goal: item.goal, url: item.url,
        tags: item.tags, enabled: !item.enabled,
      });
      setSuite(await api.suite(suite.id));
      await loadSuites();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const removeSuite = async () => {
    if (!suite) return;
    try {
      await api.deleteSuite(suite.id);
      setSelectedId(null);
      setSuite(null);
      await loadSuites();
      toast.info('Test set deleted.');
    } catch (err) {
      toast.error(err.message);
    }
  };

  const togglePick = (item) => {
    setPicked((current) => {
      const next = new Map(current);
      if (next.has(item.id)) next.delete(item.id);
      else next.set(item.id, { name: item.name, suite: suite.name });
      return next;
    });
  };

  const pickAllInSet = () => {
    setPicked((current) => {
      const next = new Map(current);
      const enabled = suite.cases.filter((c) => c.enabled);
      const allIn = enabled.every((c) => next.has(c.id));
      enabled.forEach((c) => (allIn ? next.delete(c.id) : next.set(c.id, { name: c.name, suite: suite.name })));
      return next;
    });
  };

  const pickedSets = new Set([...picked.values()].map((p) => p.suite));

  const runPicked = async () => {
    if (!picked.size || running) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setRunning(true);
    setProgress([]);
    setSummary(null);
    try {
      await api.createExecution(
        {
          caseIds: [...picked.keys()],
          name: executionName.trim() || null,
          workers: Number(options.workers) || 1,
          headless: options.headless,
          trace: options.trace,
          recordVideo: options.recordVideo,
          failOnPageError: options.failOnPageError,
        },
        (event) => {
          if (event.event === 'case_started') {
            setProgress((current) => [...current, { ...event, status: 'running' }]);
          } else if (event.event === 'case_finished') {
            setProgress((current) => current.map((row) => (
              row.case === event.caseId && row.status === 'running'
                ? { ...row, ...event, status: event.status } : row)));
          } else if (event.event === 'suite_finished') {
            setSummary(event);
          } else if (event.event === 'error') {
            toast.error(event.message);
          }
        },
        controller.signal,
      );
      setPicked(new Map());
      setExecutionName('');
      const runs = await api.suiteRuns(selectedId, 10);
      setHistory(runs.suiteRuns);
    } catch (err) {
      if (err.name !== 'AbortError') toast.error(err.message);
    } finally {
      setRunning(false);
      abortRef.current = null;
    }
  };

  const runSuite = async () => {
    if (!suite || running) return;
    const controller = new AbortController();
    abortRef.current = controller;

    setRunning(true);
    setProgress([]);
    setSummary(null);

    try {
      await api.runSuite(
        suite.id,
        {
          workers: Number(options.workers) || 1,
          tags: parseTags(options.tags).length ? parseTags(options.tags) : null,
          headless: options.headless,
          trace: options.trace,
          recordVideo: options.recordVideo,
          failOnPageError: options.failOnPageError,
        },
        (event) => {
          if (event.event === 'case_started') {
            setProgress((current) => [...current, { ...event, status: 'running' }]);
          } else if (event.event === 'case_finished') {
            setProgress((current) =>
              current.map((row) => (row.case === event.caseId && row.status === 'running'
                ? { ...row, ...event, status: event.status }
                : row)),
            );
          } else if (event.event === 'suite_finished') {
            setSummary(event);
          } else if (event.event === 'error') {
            toast.error(event.message);
          }
        },
        controller.signal,
      );
    } catch (err) {
      if (err.name !== 'AbortError') toast.error(err.message);
    } finally {
      setRunning(false);
      abortRef.current = null;
      try {
        setHistory((await api.suiteRuns(suite.id, 10)).suiteRuns);
      } catch {
        /* the history strip is not worth an error toast */
      }
    }
  };

  const executionCount = useMemo(() => {
    if (!suite) return 0;
    return suite.cases
      .filter((item) => item.enabled)
      .reduce((total, item) => total + (item.dataset?.length || 1), 0);
  }, [suite]);

  if (loading) {
    return (
      <main className="page">
        <p className="muted">Loading suites…</p>
      </main>
    );
  }

  return (
    <main className="page">
      <header className="page-header">
        <div>
          <h1 className="page-title">Test Sets</h1>
          <p className="page-subtitle">
            Scenarios live here. Pull them into an execution to run them — locally or from CI.
          </p>
        </div>
      </header>

      <div className="suites-layout">
        {/* ------------------------------------------------------- list ---- */}
        <aside className="suite-list card">
          <form className="suite-create" onSubmit={createSuite}>
            <input
              value={newSuiteName}
              onChange={(event) => setNewSuiteName(event.target.value)}
              placeholder="New test set name"
              aria-label="New test set name"
            />
            <button className="btn btn-primary btn-sm" type="submit" disabled={!newSuiteName.trim()}>
              <Plus size={14} /> Add
            </button>
          </form>

          {suites.length === 0 ? (
            <p className="muted small suite-empty">
              No suites yet. Create one above, then add cases — or save a run you
              already like from the Test Runs page.
            </p>
          ) : (
            <ul className="suite-items">
              {suites.map((item) => (
                <li key={item.id}>
                  <button
                    className={`suite-item ${item.id === selectedId ? 'active' : ''}`}
                    onClick={() => setSelectedId(item.id)}
                  >
                    <Layers size={15} />
                    <span className="suite-item-name">{item.name}</span>
                    <span className="pill">{item.case_count}</span>
                    <ChevronRight size={14} className="suite-item-chevron" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        {/* ---------------------------------------------------- detail ----- */}
        {!suite ? (
          <section className="card suite-detail empty-state">
            <Layers size={28} />
            <p>Select a suite, or create one to get started.</p>
          </section>
        ) : (
          <section className="suite-detail">
            <div className="card">
              <div className="card-head">
                <div>
                  <h2 className="card-title">{suite.name}</h2>
                  <p className="muted small">
                    {suite.cases.filter((c) => c.enabled).length} enabled ·{' '}
                    {executionCount} execution{executionCount === 1 ? '' : 's'}
                    {executionCount !== suite.cases.filter((c) => c.enabled).length
                      && ' (dataset rows expand)'}
                  </p>
                  <PriorityStrip cases={suite.cases} />
                </div>
                <div className="row-actions">
                  <button className="btn btn-sm" onClick={pickAllInSet} disabled={!suite.cases.length}>
                    Select all
                  </button>
                  <button className="btn btn-sm" onClick={() => setShowGenerator((v) => !v)}>
                    <Sparkles size={14} /> Generate
                  </button>
                  <button className="btn btn-sm" onClick={() => setShowCaseForm((v) => !v)}>
                    <Plus size={14} /> Case
                  </button>
                  <button className="btn btn-danger btn-sm" onClick={removeSuite}>
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>

              {picked.size > 0 && (
                <div className="pick-bar">
                  <span className="pick-count">
                    <strong>{picked.size}</strong> scenario{picked.size === 1 ? '' : 's'} picked
                    {pickedSets.size > 1 && ` from ${pickedSets.size} Test Sets`}
                  </span>
                  <input
                    className="pick-name"
                    type="text"
                    value={executionName}
                    onChange={(event) => setExecutionName(event.target.value)}
                    placeholder={pickedSets.size > 1
                      ? `Execution name — e.g. “${[...pickedSets].join(' + ')}”`
                      : 'Execution name — optional'}
                    disabled={running}
                  />
                  <button className="btn btn-primary btn-sm" onClick={runPicked} disabled={running}>
                    <Play size={14} /> Run execution
                  </button>
                  <button className="btn btn-ghost btn-sm" onClick={() => setPicked(new Map())} disabled={running}>
                    Clear
                  </button>
                </div>
              )}

              {showGenerator && (
                <ScenarioGenerator
                  suiteId={suite.id}
                  onAdded={async () => {
                    setShowGenerator(false);
                    setSuite(await api.suite(suite.id));
                    await loadSuites();
                  }}
                />
              )}

              {showCaseForm && (
                <form className="case-form" onSubmit={addCase}>
                  <div className="field-row">
                    <label>
                      Name
                      <input
                        value={draft.name}
                        onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                        placeholder="Sepete ürün eklenebiliyor"
                      />
                    </label>
                    <label>
                      URL
                      <input
                        value={draft.url}
                        onChange={(e) => setDraft({ ...draft, url: e.target.value })}
                        placeholder="https://example.com/"
                      />
                    </label>
                  </div>

                  <label>
                    Goal
                    <textarea
                      rows={3}
                      value={draft.goal}
                      onChange={(e) => setDraft({ ...draft, goal: e.target.value })}
                      placeholder={'Ara kutusuna {{terim}} yaz, Ara\'ya bas, sonuç çıktığını doğrula.'}
                    />
                    <span className="field-hint">
                      Use <code>{'{{placeholders}}'}</code> to pull values from the dataset below.
                    </span>
                  </label>

                  <div className="field-row">
                    <label>
                      Tags
                      <input
                        value={draft.tags}
                        onChange={(e) => setDraft({ ...draft, tags: e.target.value })}
                        placeholder="smoke, checkout"
                      />
                    </label>
                    <label>
                      Dataset (CSV or JSON, optional)
                      <textarea
                        rows={3}
                        value={draft.dataset}
                        onChange={(e) => {
                          setDraft({ ...draft, dataset: e.target.value });
                          setDatasetError(null);
                        }}
                        placeholder={'terim\nistanbul\nankara'}
                      />
                      {datasetError && <span className="field-error">{datasetError}</span>}
                    </label>
                  </div>

                  <div className="row-actions end">
                    <button type="button" className="btn btn-sm" onClick={() => setShowCaseForm(false)}>
                      Cancel
                    </button>
                    <button type="submit" className="btn btn-primary btn-sm">Add case</button>
                  </div>
                </form>
              )}

              {suite.cases.length === 0 ? (
                <p className="muted small">No cases yet.</p>
              ) : (
                <ul className="case-list">
                  {suite.cases.map((item) => (
                    <li key={item.id} className={`case-row ${item.enabled ? '' : 'disabled'}`}>
                      <input
                        type="checkbox"
                        checked={picked.has(item.id)}
                        onChange={() => togglePick(item)}
                        aria-label={`Select ${item.name}`}
                        title="Pick this scenario for an execution"
                      />
                      <span className="case-idx" title="Scenario number in this Test Set">
                        #{item.idx}
                      </span>
                      <div className="case-main">
                        <span className="case-name">{item.name}</span>
                        <span className="case-goal">{item.goal}</span>
                      </div>
                      <div className="case-meta">
                        {item.layer && <span className="layer-tag">{item.layer}</span>}
                        {item.priority && (
                          <span className={`priority-tag p-${item.priority.toLowerCase()}`}>
                            {item.priority}
                          </span>
                        )}
                        {item.dataset && <span className="pill">×{item.dataset.length}</span>}
                        {item.tags.map((tag) => (
                          <span key={tag} className="tag">{tag}</span>
                        ))}
                      </div>
                      {/* Picking and enabling are different decisions: one is
                          "run this now", the other is "this scenario is out of
                          service". They get separate controls. */}
                      <button
                        className="btn-icon"
                        onClick={() => toggleCase(item)}
                        title={item.enabled
                          ? 'Disable — leave it out of runs'
                          : 'Enable — include it in runs'}
                        aria-label={item.enabled ? `Disable ${item.name}` : `Enable ${item.name}`}
                      >
                        {item.enabled ? <Eye size={14} /> : <EyeOff size={14} />}
                      </button>
                      <button
                        className="btn-icon danger"
                        onClick={() => removeCase(item.id)}
                        aria-label={`Delete ${item.name}`}
                      >
                        <Trash2 size={14} />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {/* ------------------------------------------------- run ------- */}
            <div className="card">
              <div className="card-head">
                <h2 className="card-title">Run</h2>
                {running ? (
                  <button className="btn btn-danger btn-sm" onClick={() => abortRef.current?.abort()}>
                    <Square size={14} /> Stop
                  </button>
                ) : (
                  <button
                    className="btn btn-primary btn-sm"
                    onClick={runSuite}
                    disabled={executionCount === 0}
                  >
                    <Play size={14} /> Run suite
                  </button>
                )}
              </div>

              <div className="run-options">
                <label>
                  Workers
                  <input
                    type="number"
                    min={1}
                    max={8}
                    value={options.workers}
                    onChange={(e) => setOptions({ ...options, workers: e.target.value })}
                  />
                </label>
                <label>
                  Only these tags
                  <input
                    value={options.tags}
                    onChange={(e) => setOptions({ ...options, tags: e.target.value })}
                    placeholder="smoke"
                  />
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={options.headless}
                    onChange={(e) => setOptions({ ...options, headless: e.target.checked })}
                  />
                  Headless
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={options.trace}
                    onChange={(e) => setOptions({ ...options, trace: e.target.checked })}
                  />
                  Record trace
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={options.recordVideo}
                    onChange={(e) => setOptions({ ...options, recordVideo: e.target.checked })}
                  />
                  Record video
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={options.failOnPageError}
                    onChange={(e) => setOptions({ ...options, failOnPageError: e.target.checked })}
                  />
                  Fail on console/network errors
                </label>
              </div>

              {progress.length > 0 && (
                <ul className="progress-list">
                  {progress.map((row, index) => (
                    <li key={`${row.case}-${index}`} className={`progress-row ${row.status}`}>
                      <span className="progress-dot" />
                      <span className="progress-label">{row.label}</span>
                      {row.durationMs != null && (
                        <span className="progress-time">{(row.durationMs / 1000).toFixed(1)}s</span>
                      )}
                      {row.runId && (
                        <button className="btn-link" onClick={() => onOpenRun?.(row.runId)}>
                          report
                        </button>
                      )}
                      {row.error && <span className="progress-error">{row.error}</span>}
                    </li>
                  ))}
                </ul>
              )}

              {summary && (
                <div className={`suite-summary ${summary.failed ? 'failed' : 'passed'}`}>
                  <strong>
                    {summary.passed} passed · {summary.failed} failed
                  </strong>
                  <div className="row-actions">
                    <a
                      className="btn btn-sm"
                      href={api.suiteReportUrl(summary.suiteRunId, 'junit')}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <FileCode2 size={14} /> JUnit XML
                    </a>
                    <a
                      className="btn btn-sm"
                      href={api.suiteReportUrl(summary.suiteRunId, 'json')}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <Download size={14} /> JSON
                    </a>
                  </div>
                </div>
              )}
            </div>

            {/* --------------------------------------------- CI recipe ----- */}
            <div className="card">
              <h2 className="card-title">Run this from CI</h2>
              <p className="muted small">
                The same suite, headless, with a report your build server already
                knows how to read. A non-zero exit code fails the build.
              </p>
              <pre className="code-block">
{`python -m cli run --suite ${suite.id} \\
    ${parseTags(options.tags).map((t) => `--tag ${t} `).join('')}--workers ${options.workers} \\
    --junit results.xml --json report.json`}
              </pre>
            </div>

            {history.length > 0 && (
              <div className="card">
                <h2 className="card-title">Recent suite runs</h2>
                <ul className="history-list">
                  {history.map((row) => (
                    <li key={row.id} className="history-row">
                      <span className={`badge ${row.status === 'passed' ? 'ok' : 'bad'}`}>
                        {row.status}
                      </span>
                      <span className="muted small">
                        {row.passed}/{row.total} passed
                      </span>
                      <span className="muted small">
                        {new Date(row.started_at * 1000).toLocaleString()}
                      </span>
                      <a
                        className="btn-link"
                        href={api.suiteReportUrl(row.id, 'junit')}
                        target="_blank"
                        rel="noreferrer"
                      >
                        report
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        )}
      </div>
    </main>
  );
}
