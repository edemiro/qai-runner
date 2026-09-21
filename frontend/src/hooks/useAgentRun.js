import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';

/**
 * Drives one agent run and turns its NDJSON event stream into a timeline.
 *
 * The loop itself lives on the server, so there is exactly one place that
 * enforces the step ceiling and honours a stop request. This hook only renders
 * what it is told and can abort the stream.
 */
export function useAgentRun(sessionId, { onFinished } = {}) {
  const [timeline, setTimeline] = useState([]);
  const [status, setStatus] = useState('idle'); // idle | running | passed | failed | cancelled
  const [runId, setRunId] = useState(null);
  // Where a held run is waiting, and whether it will hold again. Both come
  // off the stream, so a reconnecting panel shows the buttons without
  // having to ask.
  const [waitingAt, setWaitingAt] = useState(null);
  const [stepping, setStepping] = useState(false);
  const [currentStep, setCurrentStep] = useState(0);
  const [maxSteps, setMaxSteps] = useState(0);
  // Only set while running a scenario that was written as steps.
  const [scenarioProgress, setScenarioProgress] = useState(null);
  // Scenarios the agent wrote from a chat request, waiting for the tester to
  // review before anything is saved: {scenarios, suggestedName, readFrom, kind}.
  const [proposed, setProposed] = useState(null);
  const abortRef = useRef(null);
  // Held in a ref so a changing callback identity never restarts a live run.
  const finishedRef = useRef(onFinished);

  useEffect(() => {
    finishedRef.current = onFinished;
  }, [onFinished]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const append = useCallback((entry) => {
    setTimeline((current) => [...current, { key: `${current.length}-${Date.now()}`, ...entry }]);
  }, []);

  const appendToken = useCallback((text, step) => {
    setTimeline((current) => {
      const last = current[current.length - 1];
      if (last && last.type === 'thought' && last.step === step) {
        const updated = [...current];
        updated[updated.length - 1] = { ...last, text: last.text + text };
        return updated;
      }
      return [...current, { key: `t-${step}-${current.length}`, type: 'thought', step, text }];
    });
  }, []);

  const reset = useCallback(() => {
    setTimeline([]);
    setStatus('idle');
    setWaitingAt(null);
    setStepping(false);
    setRunId(null);
    setCurrentStep(0);
    setScenarioProgress(null);
    setProposed(null);
  }, []);

  const clearProposed = useCallback(() => setProposed(null), []);

  const start = useCallback(
    async (goal, {
      useVision = true, model = '', effort = '', steps = null,
      // The session to run on, when the caller has just opened one and React
      // has not re-rendered with it yet. Running a saved scenario opens a
      // browser first if none is open, and the hook's own `sessionId` is a
      // render behind at that moment.
      on = null,
      // Hold from the first step — debug mode asked for before the run,
      // rather than arrived at by a failure.
      stepFromTheStart = false,
    } = {}) => {
      const session = on || sessionId;
      if (!session || status === 'running') return;

      const controller = new AbortController();
      abortRef.current = controller;

      setTimeline([{ key: 'goal', type: 'goal', text: goal }]);
      setStatus('running');
      setWaitingAt(null);
      setStepping(Boolean(stepFromTheStart));
      setCurrentStep(0);
      setScenarioProgress(null);
      setProposed(null);
      // The real ceiling is the server's MAX_AGENT_STEPS and it comes back on
      // run_started; showing a guessed number until then would be a lie.
      setMaxSteps(0);

      try {
        await api.runAgent(
          session,
          // Omitted rather than sent empty: the backend reads an absent field
          // as "use the Settings default", which is what a blank picker means.
          {
            goal,
            useVision,
            ...(steps?.length ? { steps } : {}),
            ...(model ? { model } : {}),
            ...(effort ? { effort } : {}),
            ...(stepFromTheStart ? { stepping: true } : {}),
          },
          (event) => {
            switch (event.event) {
              case 'run_started':
                setRunId(event.runId);
                setMaxSteps(event.maxSteps ?? 0);
                break;
              case 'thinking':
                setCurrentStep(event.step);
                break;
              case 'token':
                appendToken(event.text, event.step);
                break;
              case 'message':
                append({ type: 'thought', step: event.step, text: event.text });
                break;
              // A scenario run as written steps: each one opens, is judged and
              // closes on its own, so the timeline shows where the run is in
              // the scenario rather than only what the agent last clicked.
              case 'scenario_step_started':
                setScenarioProgress({ index: event.index, total: event.total });
                append({
                  type: 'scenario-step',
                  index: event.index,
                  total: event.total,
                  action: event.action,
                  expected: event.expected,
                  status: 'running',
                });
                break;
              case 'scenario_step_finished':
                setTimeline((current) => {
                  const at = current.findLastIndex(
                    (entry) => entry.type === 'scenario-step' && entry.index === event.index,
                  );
                  if (at === -1) return current;
                  const updated = [...current];
                  updated[at] = {
                    ...updated[at],
                    status: event.status,
                    message: event.message,
                  };
                  return updated;
                });
                break;
              case 'step_started':
                append({
                  type: 'step',
                  step: event.step,
                  action: event.action,
                  target: event.target,
                  value: event.value,
                  reason: event.reason,
                  status: 'running',
                });
                break;
              case 'step_finished':
                setTimeline((current) => {
                  const index = current.findLastIndex(
                    (entry) => entry.type === 'step' && entry.step === event.step,
                  );
                  if (index === -1) return current;
                  const updated = [...current];
                  updated[index] = {
                    ...updated[index],
                    status: event.status,
                    message: event.message,
                    element: event.element,
                    durationMs: event.durationMs,
                    stepId: event.stepId,
                  };
                  return updated;
                });
                break;
              // The agent wrote scenarios from a chat request. They are not
              // saved: they are handed up for review, and the workspace opens
              // the review dialog on this.
              case 'scenarios_proposed':
                setProposed({
                  scenarios: event.scenarios || [],
                  suggestedName: event.suggestedName || '',
                  readFrom: event.readFrom || null,
                  kind: event.targetKind || null,
                });
                break;
              // The run is holding at a step boundary. A failed step puts it
              // there on its own — carrying straight on through every step
              // after one that went wrong is what nobody wanted to watch.
              case 'waiting':
                setWaitingAt(event);
                setStepping(true);
                append({
                  type: 'waiting', index: event.index, total: event.total,
                  reason: event.reason, text: event.action,
                });
                break;
              case 'resumed':
                setWaitingAt(null);
                setStepping(Boolean(event.stepping));
                break;
              case 'finished':
                setStatus(event.status);
                append({ type: 'verdict', status: event.status, text: event.summary });
                break;
              case 'cancelled':
                setStatus('cancelled');
                append({ type: 'verdict', status: 'cancelled', text: event.message });
                break;
              case 'error':
                setStatus('failed');
                append({ type: 'error', text: event.message });
                break;
              case 'run_closed':
                finishedRef.current?.(event);
                break;
              default:
                break;
            }
          },
          controller.signal,
        );
      } catch (err) {
        if (err.name !== 'AbortError') {
          setStatus('failed');
          append({ type: 'error', text: err.message });
        }
      } finally {
        abortRef.current = null;
        setStatus((current) => (current === 'running' ? 'failed' : current));
      }
    },
    [sessionId, status, append, appendToken],
  );

  const stop = useCallback(async () => {
    if (!sessionId) return;
    try {
      await api.stopAgent(sessionId);
    } catch {
      // The stream abort below still ends the run from this client's side.
    }
    abortRef.current?.abort();
    abortRef.current = null;
    setStatus((current) => (current === 'running' ? 'cancelled' : current));
  }, [sessionId]);

  /** One more step, or the rest of the way. */
  const step = useCallback(async (one = true) => {
    if (!sessionId) return;
    try {
      await api.continueAgent(sessionId, one);
      setWaitingAt(null);
      setStepping(one);
    } catch {
      /* the run ended between the button and the request */
    }
  }, [sessionId]);

  /** Hold at every step from now on, or stop doing so. */
  const setStepMode = useCallback(async (on) => {
    setStepping(on);
    if (!sessionId) return;
    try {
      await api.setStepMode(sessionId, on);
    } catch {
      /* nothing running to tell */
    }
  }, [sessionId]);

  return {
    timeline, status, runId, currentStep, maxSteps, scenarioProgress, start, stop, reset,
    proposed, clearProposed, waitingAt, stepping, step, setStepMode,
  };
}
