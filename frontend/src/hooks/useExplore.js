import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';

const EMPTY = { rows: [], summary: null, error: null, runId: null, tally: null, progress: null };

/**
 * The deterministic checks: the exploratory crawl, the link scan and the
 * accessibility scan.
 *
 * They are grouped because they share a shape — start, produce findings, end
 * with a verdict — and because none of them calls a model, which is the thing
 * that makes them worth offering as one-click actions.
 */
export function useExplore(sessionId) {
  const [mode, setMode] = useState(null); // explore | links | a11y
  const [running, setRunning] = useState(false);
  const [state, setState] = useState(EMPTY);
  const abortRef = useRef(null);

  const clear = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setMode(null);
    setRunning(false);
    setState(EMPTY);
  }, []);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setRunning(false);
  }, []);

  // A session change invalidates everything on screen.
  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      abortRef.current?.abort();
      abortRef.current = null;
      setMode(null);
      setRunning(false);
      setState(EMPTY);
    });
    return () => {
      cancelled = true;
      abortRef.current?.abort();
    };
  }, [sessionId]);

  const explore = useCallback(
    async (options = {}) => {
      if (!sessionId) return;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setMode('explore');
      setRunning(true);
      setState(EMPTY);

      try {
        await api.explore(
          sessionId,
          {
            maxElements: options.maxElements ?? 40,
            maxSeconds: options.maxSeconds ?? 240,
            includeRisky: options.includeRisky ?? false,
          },
          (event) => {
            if (event.event === 'explore_started') {
              setState((s) => ({ ...s, runId: event.runId, progress: { done: 0, total: event.total } }));
            } else if (event.event === 'element_probed') {
              setState((s) => ({
                ...s,
                rows: [...s.rows, event],
                progress: { done: event.index, total: event.total },
              }));
            } else if (event.event === 'explore_finished') {
              setState((s) => ({
                ...s, summary: event.summary, tally: event.tally,
                runId: event.runId ?? s.runId, status: event.status,
              }));
            } else if (event.event === 'error') {
              setState((s) => ({ ...s, error: event.message }));
            } else if (event.event === 'warning') {
              setState((s) => ({ ...s, warning: event.message }));
            }
          },
          controller.signal,
        );
      } catch (err) {
        if (err.name !== 'AbortError') setState((s) => ({ ...s, error: err.message }));
      } finally {
        setRunning(false);
        abortRef.current = null;
      }
    },
    [sessionId],
  );

  const scanLinks = useCallback(async () => {
    if (!sessionId) return;
    setMode('links');
    setRunning(true);
    setState(EMPTY);
    try {
      const data = await api.scanLinks(sessionId);
      setState({
        ...EMPTY,
        rows: data.broken,
        summary: data.summary,
        tally: { checked: data.checked, ok: data.ok, broken: data.broken.length },
      });
    } catch (err) {
      setState({ ...EMPTY, error: err.message });
    } finally {
      setRunning(false);
    }
  }, [sessionId]);

  const scanAccessibility = useCallback(async () => {
    if (!sessionId) return;
    setMode('a11y');
    setRunning(true);
    setState(EMPTY);
    try {
      const data = await api.scanAccessibility(sessionId);
      setState({ ...EMPTY, rows: data.findings, summary: data.summary, tally: data.byRule });
    } catch (err) {
      setState({ ...EMPTY, error: err.message });
    } finally {
      setRunning(false);
    }
  }, [sessionId]);

  return { mode, running, ...state, explore, scanLinks, scanAccessibility, clear, stop };
}
