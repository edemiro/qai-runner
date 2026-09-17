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
  const [currentStep, setCurrentStep] = useState(0);
  const [maxSteps, setMaxSteps] = useState(0);
  // Only set while running a scenario that was written as steps.
  const [scenarioProgress, setScenarioProgress] = useState(null);
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
    setRunId(null);
    setCurrentStep(0);
    setScenarioProgress(null);
  }, []);

  const start = useCallback(
    async (goal, { useVision = true, model = '', effort = '', steps = null } = {}) => {
      if (!sessionId || status === 'running') return;

      const controller = new AbortController();
      abortRef.current = controller;

      setTimeline([{ key: 'goal', type: 'goal', text: goal }]);
      setStatus('running');
      setCurrentStep(0);
      setScenarioProgress(null);
      // The real ceiling is the server's MAX_AGENT_STEPS and it comes back on
      // run_started; showing a guessed number until then would be a lie.
      setMaxSteps(0);

      try {
        await api.runAgent(
          sessionId,
          // Omitted rather than sent empty: the backend reads an absent field
          // as "use the Settings default", which is what a blank picker means.
          {
            goal,
            useVision,
            ...(steps?.length ? { steps } : {}),
            ...(model ? { model } : {}),
            ...(effort ? { effort } : {}),
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

  return {
    timeline, status, runId, currentStep, maxSteps, scenarioProgress, start, stop, reset,
  };
}
