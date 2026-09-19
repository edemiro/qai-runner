import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ArrowRight,
  ChevronDown,
  ChevronRight,
  Eye,
  EyeOff,
  Layers,
  Loader2,
  Play,
  Plus,
  Sparkles,
  Trash2,
} from 'lucide-react';

import { EmptyState } from '../components/EmptyState';
import { PlatformFilter, PlatformTag } from '../components/PlatformFilter';
import { ScenarioGenerator } from '../components/ScenarioGenerator';

// Sentinel for "a Test Set that does not exist yet" in the move-to picker.
const MOVE_NEW = '__new__';
import { StepEditor } from '../components/StepEditor';
import { api } from '../api';
import { useToast } from '../hooks/useToast';

const EMPTY_CASE = { name: '', goal: '', url: '', tags: '', dataset: '', steps: [] };

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

export function SuitesPage({ onRunHere, onOpenExecution }) {
  const toast = useToast();

  const [suites, setSuites] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [platform, setPlatform] = useState('all');
  const [newSuiteKind, setNewSuiteKind] = useState('web');
  const [suite, setSuite] = useState(null);
  const [loading, setLoading] = useState(true);

  const [newSuiteName, setNewSuiteName] = useState('');
  // Optional module ("Uçuş Arama") the new set files under; sets sharing one
  // are listed together under a heading.
  const [newSuiteModule, setNewSuiteModule] = useState('');
  const [draft, setDraft] = useState(EMPTY_CASE);
  const [datasetError, setDatasetError] = useState(null);
  const [showCaseForm, setShowCaseForm] = useState(false);
  const [showGenerator, setShowGenerator] = useState(false);

  /* Selection is held by case id, not by Test Set, and survives switching sets.
     That is what makes "combine two sets into one execution" the same gesture
     as "pick four scenarios out of this one" — no separate mode for it. */
  const [picked, setPicked] = useState(() => new Map());
  // Where picked scenarios go when moved: an existing set's id, or NEW_SET
  // with a name (and optional module) for one created on the spot.
  const [moveTarget, setMoveTarget] = useState('');
  const [moveNewName, setMoveNewName] = useState('');
  const [moveNewModule, setMoveNewModule] = useState('');
  const [moving, setMoving] = useState(false);
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
  const [history, setHistory] = useState([]);
  // Which scenarios have their steps open. Collapsed by default so the set
  // reads as a list of scenarios rather than a wall of steps.
  const [openSteps, setOpenSteps] = useState(() => new Set());

  const toggleSteps = (caseId) => {
    setOpenSteps((current) => {
      const next = new Set(current);
      if (next.has(caseId)) next.delete(caseId); else next.add(caseId);
      return next;
    });
  };

  const counts = {
    all: suites.length,
    web: suites.filter((item) => (item.kind || 'web') !== 'mobile').length,
    mobile: suites.filter((item) => item.kind === 'mobile').length,
  };
  const visible = platform === 'all'
    ? suites
    : suites.filter((item) => (item.kind || 'web') === platform);

  /* Derived rather than synced through an effect: filtering to a platform the
     selected set is not on used to leave that set open on the right while the
     list beside it said there was nothing — two panes disagreeing about what is
     on screen. Falling through to the first visible set keeps them agreeing
     without a second copy of the selection to keep in step. */
  const shownId = visible.some((item) => item.id === selectedId)
    ? selectedId
    : (visible[0]?.id ?? null);

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

    if (!shownId) {
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
          api.suite(shownId),
          api.suiteRuns(shownId, 10),
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
  }, [shownId, toast]);


  const createSuite = async (event) => {
    event.preventDefault();
    const name = newSuiteName.trim();
    if (!name) return;
    try {
      const created = await api.createSuite({
        name, kind: newSuiteKind, tags: [], module: newSuiteModule.trim() || null,
      });
      setNewSuiteName('');
      setNewSuiteModule('');
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
        // Blank rows are dropped rather than sent: an empty step is one the
        // runner would skip and the report would have to explain.
        steps: draft.steps.filter((step) => step.action.trim()),
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

  /** Re-file the picked scenarios into another set — chosen, or named and
   *  created here — so a set that grew from several requests is split back
   *  into one set per request without retyping anything. */
  const movePicked = async () => {
    if (!picked.size || moving) return;
    let targetId = moveTarget;
    if (!targetId) {
      toast.warning('Pick a Test Set to move the scenarios into, or name a new one.');
      return;
    }
    setMoving(true);
    try {
      if (targetId === MOVE_NEW) {
        const name = moveNewName.trim();
        if (!name) {
          toast.warning('Name the new Test Set.');
          return;
        }
        const created = await api.createSuite({
          name, kind: suite.kind, tags: [], module: moveNewModule.trim() || suite.module || null,
        });
        targetId = created.id;
      }
      const result = await api.moveCases([...picked.keys()], targetId);
      toast.success(`${result.moved} scenario${result.moved === 1 ? '' : 's'} moved to “${result.suiteName}”.`);
      setPicked(new Map());
      setMoveTarget('');
      setMoveNewName('');
      setMoveNewModule('');
      setSuite(await api.suite(suite.id));
      await loadSuites();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setMoving(false);
    }
  };

  /** Start the picked scenarios as an execution and go watch it.
   *
   *  Started on the server rather than streamed to this page: a streamed run
   *  dies the moment you navigate away, which is what left executions sitting
   *  at "running" with nothing in them. The scenarios run in their own browsers
   *  — not in the workspace session — so Test Executions, not the Web or Mobile
   *  screen, is where the run is actually visible.
   */
  const runPicked = async () => {
    if (!picked.size || running) return;
    if (!executionName.trim()) {
      toast.warning('Name the execution so it can be told apart in the list.');
      return;
    }
    setRunning(true);
    try {
      const { suiteRunId } = await api.startExecution({
        caseIds: [...picked.keys()],
        name: executionName.trim(),
        workers: Number(options.workers) || 1,
        headless: options.headless,
        trace: options.trace,
        recordVideo: options.recordVideo,
        failOnPageError: options.failOnPageError,
      });
      toast.success(`“${executionName.trim()}” started.`);
      setPicked(new Map());
      setExecutionName('');
      onOpenExecution?.(suiteRunId);
    } catch (err) {
      toast.error(`Execution could not start: ${err.message}`);
    } finally {
      setRunning(false);
    }
  };

  /** Run the whole set — same server-side start as a hand-picked execution,
   *  so it survives leaving this page, and lands where it can be watched. */
  const runSuite = async () => {
    if (!suite || running) return;
    setRunning(true);
    try {
      const { suiteRunId } = await api.startSuiteRun(suite.id, {
        workers: Number(options.workers) || 1,
        tags: parseTags(options.tags).length ? parseTags(options.tags) : null,
        headless: options.headless,
        trace: options.trace,
        recordVideo: options.recordVideo,
        failOnPageError: options.failOnPageError,
      });
      toast.success(`“${suite.name}” started.`);
      onOpenExecution?.(suiteRunId);
    } catch (err) {
      toast.error(`Execution could not start: ${err.message}`);
    } finally {
      setRunning(false);
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
            <input
              className="suite-create-module"
              value={newSuiteModule}
              onChange={(event) => setNewSuiteModule(event.target.value)}
              placeholder="Module (optional) — e.g. Uçuş Arama"
              aria-label="Module the new test set belongs to"
              title="Sets sharing a module are listed together"
            />
            {/* Chosen at creation, not later: the platform decides which
                scenarios can go in and which device can run them, so a set
                that changed platform afterwards would strand its own cases. */}
            <select
              value={newSuiteKind}
              onChange={(event) => setNewSuiteKind(event.target.value)}
              aria-label="Platform for the new test set"
            >
              <option value="web">Web</option>
              <option value="mobile">Mobile</option>
            </select>
            <button className="btn btn-primary btn-sm" type="submit" disabled={!newSuiteName.trim()}>
              <Plus size={14} /> Add
            </button>
          </form>

          <PlatformFilter value={platform} onChange={setPlatform} counts={counts} />

          {suites.length === 0 ? (
            <EmptyState icon={Layers} title="No Test Sets yet" compact>
              Name one above to get started, then generate scenarios into it — or
              save a run you already like from Test Runs.
            </EmptyState>
          ) : visible.length === 0 ? (
            <EmptyState icon={Layers} title={`No ${platform} Test Sets`} compact>
              Create one with the platform picker set to {platform}.
            </EmptyState>
          ) : (
            <ul className="suite-items">
              {/* Sets file under a module ("Uçuş Arama" holding one-way,
                  round-trip, multi-city…), so the list is drawn as groups with a
                  heading each. Sets with no module come first, unheaded. */}
              {Object.entries(
                visible.reduce((groups, item) => {
                  const key = (item.module || '').trim();
                  (groups[key] ||= []).push(item);
                  return groups;
                }, {}),
              )
                .sort(([a], [b]) => (a === '' ? -1 : b === '' ? 1 : a.localeCompare(b)))
                .map(([module, items]) => (
                  <li key={module || '__none__'} className="suite-group">
                    {module && (
                      <div className="suite-group-label" title={`Module: ${module}`}>
                        {module}
                        <span className="suite-group-count">{items.length}</span>
                      </div>
                    )}
                    <ul className="suite-items">
                      {items.map((item) => (
                        <li key={item.id}>
                          <button
                            className={`suite-item ${item.id === shownId ? 'active' : ''}`}
                            onClick={() => setSelectedId(item.id)}
                          >
                            <Layers size={15} />
                            <span className="suite-item-name">{item.name}</span>
                            <PlatformTag kind={item.kind} />
                            <span className="pill">{item.case_count}</span>
                            <ChevronRight size={14} className="suite-item-chevron" />
                          </button>
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
            </ul>
          )}
        </aside>

        {/* ---------------------------------------------------- detail ----- */}
        {!suite ? (
          <section className="card suite-detail">
            <EmptyState icon={Layers} title="Nothing selected">
              {suites.length
                ? 'Pick a Test Set on the left to see and run its scenarios.'
                : 'Create a Test Set to start collecting scenarios.'}
            </EmptyState>
          </section>
        ) : (
          <section className="suite-detail">
            <div className="card">
              <div className="card-head">
                <div>
                  {suite.module && (
                    <div className="suite-module-label" title="Module this set belongs to">
                      {suite.module}
                    </div>
                  )}
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

                  {/* Splitting a set: the picked scenarios go to another set,
                      existing or named here, so one set that collected several
                      requests can be broken back into one set per request. */}
                  <div className="pick-move">
                    <select
                      value={moveTarget}
                      onChange={(event) => setMoveTarget(event.target.value)}
                      disabled={moving || running}
                      aria-label="Test Set to move the picked scenarios into"
                    >
                      <option value="">Move to…</option>
                      {suites.filter((s) => s.id !== suite.id && s.kind === suite.kind).map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.module ? `${s.module} / ` : ''}{s.name}
                        </option>
                      ))}
                      <option value={MOVE_NEW}>＋ New Test Set…</option>
                    </select>
                    {moveTarget === MOVE_NEW && (
                      <>
                        <input
                          type="text"
                          value={moveNewName}
                          onChange={(event) => setMoveNewName(event.target.value)}
                          placeholder="New set name — e.g. Tek yön uçuş ara"
                          disabled={moving}
                        />
                        <input
                          type="text"
                          value={moveNewModule}
                          onChange={(event) => setMoveNewModule(event.target.value)}
                          placeholder={suite.module ? `Module — ${suite.module}` : 'Module (optional)'}
                          disabled={moving}
                        />
                      </>
                    )}
                    {moveTarget && (
                      <button className="btn btn-sm" onClick={movePicked} disabled={moving || running}>
                        {moving ? <Loader2 size={14} className="spin" /> : <ArrowRight size={14} />}
                        Move {picked.size}
                      </button>
                    )}
                  </div>
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

                  <StepEditor
                    steps={draft.steps}
                    onChange={(steps) => setDraft({ ...draft, steps })}
                  />

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
                        {/* Steps are opened per scenario rather than printed
                            under every one: ten scenarios at ten steps each
                            filled the page and left the set impossible to
                            scan. The count stays visible; the detail is a
                            click away. */}
                        {item.steps?.length > 0 && (
                          <>
                            <button
                              type="button"
                              className="case-steps-toggle"
                              onClick={() => toggleSteps(item.id)}
                              aria-expanded={openSteps.has(item.id)}
                            >
                              {openSteps.has(item.id)
                                ? <ChevronDown size={12} />
                                : <ChevronRight size={12} />}
                              {item.steps.length} step{item.steps.length === 1 ? '' : 's'}
                            </button>
                            {openSteps.has(item.id) && (
                              <ol className="case-steps">
                                {item.steps.map((step, i) => (
                                  <li key={i}>
                                    <span className="case-step-action">{step.action}</span>
                                    {step.expected && (
                                      <span className="case-step-expected">{step.expected}</span>
                                    )}
                                  </li>
                                ))}
                              </ol>
                            )}
                          </>
                        )}
                      </div>
                      <div className="case-meta">
                        {/* The step count lives on the toggle now, so it is not
                            repeated here. */}
                        {item.priority && (
                          <span className={`priority-tag p-${item.priority.toLowerCase()}`}>
                            {item.priority}
                          </span>
                        )}
                        {item.layer && <span className="layer-tag">{item.layer}</span>}
                        {item.dataset && <span className="pill">×{item.dataset.length}</span>}
                        {/* A tag that just repeats the layer is noise — the layer
                            chip already says it, so it is dropped here. */}
                        {item.tags
                          .filter((tag) => tag.toLowerCase() !== (item.layer || '').toLowerCase())
                          .map((tag) => (
                            <span key={tag} className="tag">{tag}</span>
                          ))}
                      </div>
                      {/* Actions stay grouped on the right so a long title or a
                          stack of chips never pushes them onto their own line. */}
                      <div className="case-actions">
                        {/* Trying one scenario against the browser or device
                            already open, without waiting for a whole Test Set
                            run — this is how a scenario gets debugged while it
                            is being written. */}
                        {onRunHere && (
                          <button
                            className="btn-icon"
                            onClick={() => onRunHere(item)}
                            title={item.steps?.length
                              ? 'Run here, step by step, on the connected session'
                              : 'Run here on the connected session'}
                            aria-label={`Run ${item.name} on the connected session`}
                          >
                            <Play size={14} />
                          </button>
                        )}
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
                      </div>
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
                  <button className="btn btn-sm" disabled>
                    <Loader2 size={14} className="spin" /> Starting…
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


              {/* Progress and the report live on Test Executions now, which is
                  where a run is opened the moment it starts. */}
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
