import { useEffect, useRef, useState } from 'react';
import {
  ArrowUp, CheckCircle2, ChevronRight, CircleSlash, Eraser,
  FileText, Image as ImageIcon, ImageOff, Loader2, PlayCircle, Send, Sparkles,
  Square, Target, XCircle, Zap,
} from 'lucide-react';

import { api } from '../api';

const ACTION_ICON = {
  click: '⊙',
  type: '⌨',
  clear: '⌫',
  scroll: '↕',
  swipe: '↔',
  key: '⎋',
  wait: '⏱',
  assert_visible: '✓',
  assert_text: '✓',
};

/**
 * What a run is for. The three are not settings — they are three different
 * jobs, and the agent's vocabulary differs for each, so the choice is made
 * before the sentence rather than guessed from it.
 */
const MODES = [
  {
    id: 'drive',
    label: 'Test the screen',
    icon: Zap,
    hint: 'Drive the app and check what you describe. Nothing is written to a Test Set.',
  },
  {
    id: 'write',
    label: 'Create Test Scenarios',
    icon: FileText,
    // Naming the set, and running what was written, both happen in the review
    // dialog this opens — so there is no separate "write & run" mode, and no
    // Test Set or execution field to fill in before the scenarios exist.
    hint: 'Write scenarios for this screen in the company standard, then review, '
      + 'edit and name them before they are saved — and run them from there if you want.',
  },
  {
    id: 'run',
    label: 'Run a Test Set',
    icon: PlayCircle,
    hint: 'Create an execution for a Test Set that already exists and run it.',
  },
];

const DEFAULT_EXAMPLES = [
  'Open the search bar, type "headphones" and verify results appear',
  'Log in with test@test.com / hunter2 and verify the home screen loads',
  'Add the first product to the cart and verify the cart badge shows 1',
];

function StepEntry({ entry }) {
  const [open, setOpen] = useState(false);
  const isAssertion = entry.action?.startsWith('assert');
  const icon = ACTION_ICON[entry.action] || '•';

  return (
    <div className={`step-entry ${entry.status}`}>
      <button className="step-head" onClick={() => setOpen((value) => !value)}>
        <span className="step-glyph" aria-hidden="true">{icon}</span>
        <span className="step-label">
          <strong>{entry.action.replace('_', ' ')}</strong>
          {entry.target && <span className="step-target">{entry.target}</span>}
          {entry.value && <span className="step-value">“{entry.value}”</span>}
        </span>
        <span className="step-meta">
          {isAssertion && <span className="assert-tag">assertion</span>}
          {entry.durationMs != null && <span className="step-duration">{entry.durationMs}ms</span>}
          {entry.status === 'running' && <Loader2 size={13} className="spin" />}
          {entry.status === 'passed' && <CheckCircle2 size={13} />}
          {entry.status === 'failed' && <XCircle size={13} />}
          <ChevronRight size={13} className={`step-caret ${open ? 'open' : ''}`} />
        </span>
      </button>

      {open && (
        <div className="step-body">
          {entry.reason && (
            <p className="step-reason">
              <Target size={12} /> {entry.reason}
            </p>
          )}
          {entry.message && <p className="step-message">{entry.message}</p>}
          {entry.element && (
            <dl className="step-element">
              {entry.element.id && (
                <>
                  <dt>id</dt>
                  <dd>{entry.element.id}</dd>
                </>
              )}
              {entry.element.role && (
                <>
                  <dt>role</dt>
                  <dd>{entry.element.role}</dd>
                </>
              )}
              {entry.element.xpath && (
                <>
                  <dt>xpath</dt>
                  <dd className="wrap">{entry.element.xpath}</dd>
                </>
              )}
            </dl>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Suggestions arrive either as plain strings (the mobile workspace's fixed
 * examples) or as objects read off the live page. Normalising here keeps both
 * callers simple and the rendering in one place.
 */
function normaliseSuggestions(examples) {
  return (examples || []).map((example, index) =>
    typeof example === 'string'
      ? { id: `ex-${index}`, kind: 'prompt', label: null, text: example, hint: null }
      : example,
  );
}

export function AgentPanel({
  timeline, status, currentStep, maxSteps, onStart, onStop, onReset, onOpenRun, runId,
  onWriteScenarios = null,
  placeholder = 'What should QAi test? e.g. “Search for headphones and verify results appear”',
  examples = DEFAULT_EXAMPLES,
  pageSummary = null,
  onAction = null,
  suggestionsLoading = false,
  heading = 'Describe a test scenario',
  intro = 'QAi reads the live screen, decides one action at a time, executes it on the device'
    + ' and records every step. Finish with an assertion so the run has a verdict.',
}) {
  const [goal, setGoal] = useState('');
  const [mode, setMode] = useState('drive');
  const [testSet, setTestSet] = useState('');
  // Only meaningful when running an existing Test Set: a set can
  // be executed many times, and "Regression" beats reading the Test Set's own
  // name back in the Test Executions list on every single run.
  const [executionName, setExecutionName] = useState('');
  const [useVision, setUseVision] = useState(true);
  // Empty means "whatever Settings says". Kept per run so the same scenario can
  // be tried on two models back to back without editing a global default.
  const [model, setModel] = useState('');
  const [effort, setEffort] = useState('');
  const [models, setModels] = useState([]);
  const [suites, setSuites] = useState([]);
  const endRef = useRef(null);
  const running = status === 'running';

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.providers();
        const active = (data.providers || []).find(
          (entry) => entry.id === data.active?.provider,
        );
        if (!cancelled) setModels(active?.models || []);
      } catch {
        // The pickers are a convenience: without the list the run still goes
        // out on the Settings default, so a failure here is not worth a toast.
        if (!cancelled) setModels([]);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.suites();
        if (!cancelled) setSuites(data.suites || []);
      } catch {
        // Typing a name still works; the list is a convenience.
        if (!cancelled) setSuites([]);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Actions and prompts are presented differently, so they are split once here
  // rather than branching inside the render.
  const all = normaliseSuggestions(examples);
  const actions = onAction ? all.filter((item) => item.kind === 'action') : [];
  const prompts = all.filter((item) => item.kind !== 'action');

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [timeline]);

  /**
   * The mode is turned into a plain instruction rather than a flag on the
   * request. The agent already understands `write_scenarios` and
   * `run_test_set`; what it cannot do is guess which of the three jobs the
   * sentence meant, and that is exactly what the picker settles.
   */
  const knownSet = suites.some(
    (suite) => suite.name.trim().toLowerCase() === testSet.trim().toLowerCase(),
  );

  /**
   * One labeled line per field, rather than folding everything into a single
   * sentence. The agent is told to copy a labeled line verbatim into the
   * matching action field (Test Set → `value`, Execution → `execution`,
   * Açıklama → `brief`) instead of re-deriving it from prose — so what the
   * tester typed into a specific box is what actually reaches the backend,
   * not the model's paraphrase of it.
   */
  const instruction = () => {
    const where = testSet.trim();
    const exec = executionName.trim();
    const description = goal.trim();
    // A field left blank produces no line at all, rather than a placeholder
    // string the model has to recognise as "empty" — omission is what "empty"
    // already means to the backend (name the set after the screen, generate
    // the execution's name), so there is nothing for the model to translate.
    const testSetLine = where ? [`Test Set: "${where}"`] : [];
    const executionLine = exec ? [`Execution: "${exec}"`] : [];
    const descriptionLine = description ? [`Açıklama: ${description}`] : [];

    if (mode === 'write') {
      return [
        'Bu ekranın test senaryolarını çıkar ve bir test sete ekle.',
        ...testSetLine, ...descriptionLine,
      ].join('\n');
    }
    if (mode === 'run') {
      return [
        `"${where}" adlı test setin execution'ını oluştur ve koştur.`,
        ...executionLine,
      ].join('\n');
    }
    return description;
  };

  const submit = () => {
    if (running) return;
    if (mode === 'drive' && !goal.trim()) return;
    if (mode === 'run' && (!testSet.trim() || !knownSet)) return;
    // Writing scenarios opens a review dialog instead of saving straight away:
    // the tester ticks, edits and names before anything lands, and runs them
    // from there — which is why there is no separate write-and-run mode.
    if (mode === 'write' && onWriteScenarios) {
      onWriteScenarios(goal.trim());
      setGoal('');
      return;
    }
    // No step ceiling from here: the server enforces MAX_AGENT_STEPS, and a
    // number the tester has to guess before the run is a worse guard than one.
    onStart(instruction(), { useVision, model, effort });
    // The goal is echoed at the top of the timeline once the run starts;
    // leaving it in the box too reads as "not sent yet".
    setGoal('');
  };

  return (
    <div className="agent-panel">
      <div className="agent-timeline">
        {timeline.length === 0 ? (
          <div className="agent-empty">
            <div className="agent-empty-mark">
              <Send size={20} />
            </div>
            <h3>{heading}</h3>
            <p>{intro}</p>

            {pageSummary && (
              <p className="page-read">
                <strong>{pageSummary.title || pageSummary.url}</strong>
                {' — '}
                {pageSummary.kind} sayfası · {pageSummary.controls} kontrol
                {pageSummary.textboxes ? ` · ${pageSummary.textboxes} form alanı` : ''}
              </p>
            )}

            {suggestionsLoading && <span className="muted small">Sayfa okunuyor…</span>}

            {/* Actions run on click and cost nothing, so they get their own
                compact row above the fold. Leaving them at the bottom of the
                scrolling list hid the most useful thing on the panel. */}
            {actions.length > 0 && (
              <div className="action-row">
                {actions.map((action) => (
                  <button
                    key={action.id}
                    className="action-chip"
                    onClick={() => onAction?.(action)}
                    title={action.text}
                  >
                    <Sparkles size={13} />
                    <span>
                      <strong>{action.label}</strong>
                      {action.hint && <em>{action.hint}</em>}
                    </span>
                  </button>
                ))}
              </div>
            )}

            {prompts.length > 0 && (
              <div className="example-list">
                {prompts.map((suggestion) => (
                  <button
                    key={suggestion.id}
                    className="example-chip"
                    // A prompt fills the composer so it can be edited before it
                    // runs; nothing is sent until the user presses send.
                    onClick={() => setGoal(suggestion.text)}
                    title="Düzenleyip çalıştır"
                  >
                    <span className="chip-body">
                      {suggestion.label && <strong>{suggestion.label}</strong>}
                      <span>{suggestion.text}</span>
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          timeline.map((entry) => {
            if (entry.type === 'goal') {
              return (
                <div key={entry.key} className="goal-banner">
                  <span className="goal-tag">Goal</span>
                  <span>{entry.text}</span>
                </div>
              );
            }
            if (entry.type === 'thought') {
              return (
                <div key={entry.key} className="thought">
                  {entry.text.replace(/```json[\s\S]*?```/g, '').trim()}
                </div>
              );
            }
            // The scenario's own steps head the actions taken to satisfy them,
            // so the timeline reads as "step 2 of 5, and here is what it did".
            if (entry.type === 'scenario-step') {
              return (
                <div key={entry.key} className={`scenario-step-entry ${entry.status}`}>
                  <span className="scenario-step-idx">
                    {entry.index}/{entry.total}
                  </span>
                  <div className="scenario-step-body">
                    <div className="scenario-step-action">{entry.action}</div>
                    {entry.expected && (
                      <div className="scenario-step-expected">Expected: {entry.expected}</div>
                    )}
                    {entry.message && (
                      <div className="scenario-step-message">{entry.message}</div>
                    )}
                  </div>
                  {entry.status !== 'running' && (
                    <span
                      className={`verdict verdict-${entry.status === 'passed' ? 'pass' : 'fail'}`}
                    >
                      {entry.status === 'passed' ? 'Pass' : 'Fail'}
                    </span>
                  )}
                </div>
              );
            }
            if (entry.type === 'step') return <StepEntry key={entry.key} entry={entry} />;
            if (entry.type === 'error') {
              return (
                <div key={entry.key} className="verdict failed">
                  <XCircle size={15} />
                  <span>{entry.text}</span>
                </div>
              );
            }
            if (entry.type === 'verdict') {
              const Icon =
                entry.status === 'passed' ? CheckCircle2 : entry.status === 'cancelled' ? CircleSlash : XCircle;
              return (
                <div key={entry.key} className={`verdict ${entry.status}`}>
                  <Icon size={15} />
                  <div>
                    <strong>
                      {entry.status === 'passed'
                        ? 'Run passed'
                        : entry.status === 'cancelled'
                          ? 'Run stopped'
                          : 'Run failed'}
                    </strong>
                    {entry.text && <p>{entry.text}</p>}
                  </div>
                  {runId && (
                    <button className="btn btn-ghost btn-sm" onClick={() => onOpenRun(runId)}>
                      Open report
                    </button>
                  )}
                </div>
              );
            }
            return null;
          })
        )}
        <div ref={endRef} />
      </div>

      <div className="agent-composer">
        {running && (
          <div className="run-progress">
            <Loader2 size={13} className="spin" />
            <span>
              Step {currentStep} of {maxSteps}
            </span>
            <div className="progress-track">
              <div className="progress-fill" style={{ width: `${Math.min(100, (currentStep / Math.max(maxSteps, 1)) * 100)}%` }} />
            </div>
            <button className="btn btn-danger btn-sm" onClick={onStop}>
              <Square size={12} />
              Stop
            </button>
          </div>
        )}

        <div className="composer-box">
          <div className="composer-modes" role="group" aria-label="What this run should do">
            {MODES.map((entry) => (
              <button
                key={entry.id}
                className={`mode-chip ${mode === entry.id ? 'active' : ''}`}
                onClick={() => setMode(entry.id)}
                disabled={running}
                title={entry.hint}
              >
                <entry.icon size={13} />
                {entry.label}
              </button>
            ))}
          </div>

          {/* Only running asks for these up front. Writing scenarios names its
              Test Set and its execution in the review dialog, once the
              scenarios exist and there is something to name them after. */}
          {mode === 'run' && (
            <div className="guided-fields">
              <label className="field-group">
                <span className="field-label">Test Set</span>
                {/* A datalist rather than a select, so the list filters as you
                    type on an account with many sets. */}
                <input
                  className="composer-target"
                  type="text"
                  list="agent-test-sets"
                  value={testSet}
                  onChange={(event) => setTestSet(event.target.value)}
                  placeholder="Which Test Set to run"
                  disabled={running}
                />
              </label>
              <datalist id="agent-test-sets">
                {suites.map((suite) => (
                  <option key={suite.id} value={suite.name}>
                    {suite.case_count} scenario{suite.case_count === 1 ? '' : 's'}
                  </option>
                ))}
              </datalist>

              <label className="field-group">
                <span className="field-label">Execution name</span>
                <input
                  className="composer-target"
                  type="text"
                  value={executionName}
                  onChange={(event) => setExecutionName(event.target.value)}
                  placeholder="Optional — auto-named otherwise"
                  disabled={running}
                />
              </label>

              {testSet.trim() && !knownSet && (
                <span className="composer-target-note">
                  No Test Set by that name{suites.length ? ` — ${suites.map((s) => s.name).join(', ')}` : ' yet'}
                </span>
              )}
            </div>
          )}

          {mode !== 'run' && (
            <label className="field-group">
              {mode !== 'drive' && <span className="field-label">Açıklama</span>}
              <textarea
                className="composer-input"
                rows={2}
                placeholder={mode === 'drive'
                  ? placeholder
                  : 'Nasıl testler istiyorsun? Örn. “ödeme akışını ve hatalı kart senaryolarını kapsasın”'}
                value={goal}
                onChange={(event) => setGoal(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    submit();
                  }
                }}
                disabled={running}
              />
            </label>
          )}

          <div className="composer-footer">
            {/* The two pickers share one row at equal width; the toggle sits
                with the actions below. A select is as wide as its longest
                option — a model id — so left to itself it squeezed everything
                else into a ragged wrap. */}
            <div className="composer-options">
              {models.length > 0 && (
                <select
                  className="option-select"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  disabled={running}
                  aria-label="Model for this run"
                  title="Use a different model for this run only. Settings keeps its own default."
                >
                  <option value="">Model: auto</option>
                  {models.map((entry) => (
                    <option key={entry.id} value={entry.id} title={entry.note || ''}>{entry.id}</option>
                  ))}
                </select>
              )}
              <select
                className="option-select"
                value={effort}
                onChange={(e) => setEffort(e.target.value)}
                disabled={running}
                aria-label="How hard the model thinks about each step"
                title="How hard the model thinks about each step. Faster is cheaper; more thorough handles crowded screens better."
              >
                <option value="">Speed: auto</option>
                <option value="low">Fast</option>
                <option value="medium">Balanced</option>
                <option value="high">Thorough</option>
              </select>
            </div>

            {/* Clearing is an action on the conversation, not a setting for the
                next run, so it sits with the send button rather than among the
                options it kept being mistaken for. */}
            <div className="composer-send">
              <button
                className={`toggle-chip ${useVision ? 'on' : ''}`}
                onClick={() => setUseVision((value) => !value)}
                disabled={running}
                /* Its own icon, not an eye: the Web tab has an eye too, for
                   whether the browser has a window, and the two were being
                   read as the same switch. */
                title={useVision
                  ? 'Sending the screenshot with the element tree. The model can then see '
                    + 'icons, canvas and anything the tree does not describe — at about 1.6s '
                    + 'a step. Applies to the next run.'
                  : 'Sending the element tree only. About 1.6s a step faster, but the model '
                    + 'is blind to icon-only and custom-drawn UI. Applies to the next run.'}
              >
                {useVision ? <ImageIcon size={13} /> : <ImageOff size={13} />}
                {useVision ? 'Vision on' : 'Vision off'}
              </button>
              <span className="composer-spacer" />
              {timeline.length > 0 && !running && (
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={onReset}
                  title="Clear this conversation and go back to the suggestions"
                >
                  <Eraser size={13} /> Clear
                </button>
              )}
              <button
                className="btn btn-primary btn-icon"
                onClick={submit}
                disabled={running || (mode === 'drive' && !goal.trim()) || (mode === 'run' && !knownSet)}
                title={MODES.find((m) => m.id === mode)?.hint}
              >
                <ArrowUp size={17} />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
