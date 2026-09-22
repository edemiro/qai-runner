import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle, ArrowLeft, ArrowRight, Eye, EyeOff, Globe, Loader2, Maximize2, Minimize2,
  PanelRightClose, PanelRightOpen, Radio, RotateCcw, Square, Wrench, X,
} from 'lucide-react';

import { api } from '../api';
import { AgentPanel } from '../components/AgentPanel';
import { ScenarioReviewModal } from '../components/ScenarioReviewModal';
import { Inspector } from '../components/Inspector';
import { ScanPanel } from '../components/ScanPanel';
import { WebTools } from '../components/WebTools';
import { useExplore } from '../hooks/useExplore';
import { useFullscreen } from '../hooks/useFullscreen';
import { useScreenStream } from '../hooks/useScreenStream';
import { useToast } from '../hooks/useToast';
import { parseBounds, roleColor } from '../lib/elements';
import { applyEnvironment, DEFAULT_ENV_URL, ENV_GROUPS, matchEnv } from '../lib/environments';
import './web-workspace.css';

const VIEWPORTS = [
  { id: 'desktop', label: 'Desktop', w: 1440, h: 900 },
  { id: 'tablet', label: 'Tablet', w: 834, h: 1112 },
  { id: 'mobile', label: 'Mobile', w: 390, h: 844 },
];

const SUGGESTIONS = [
  'Çerez uyarısını kapat, arama kutusuna "İstanbul" yaz ve sonuçların geldiğini doğrula',
  'Try a failed login with an invalid email and verify the error message appears',
  'Open the first result and verify the detail page shows a price',
];

const DRAG_PX = 6;

/**
 * The environment picker that sits left of an address.
 *
 * The environments differ by a single token, and picking the wrong one
 * produces a run against the wrong stack that still reads as plausible.
 * Choosing from the list swaps the host and keeps whatever path the address
 * already carries — the same swap the runner makes when a set is pointed at an
 * environment, so a page reached by hand and the same page reached by a run
 * land in the same place. Typing over it still works.
 */
function EnvSelect({ url, onPick, disabled }) {
  const current = matchEnv(url);
  return (
    <select
      className="env-select"
      value={current?.url || ''}
      onChange={(event) => event.target.value && onPick(applyEnvironment(url, event.target.value))}
      disabled={disabled}
      aria-label="Environment"
      title="TK web environments"
    >
      {!current && <option value="">Custom</option>}
      {ENV_GROUPS.map((group) => (
        <optgroup key={group.label} label={group.label}>
          {group.items.map((item) => (
            <option key={item.name} value={item.url} title={item.url}>{item.name}</option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}

/**
 * The bar of the empty screen: pick an environment, name a page, open it.
 *
 * Only the empty screen has this. Once a page is open the address lives in the
 * browser chrome, next to Back and Forward, rather than in a second bar above
 * the whole workspace saying a different thing from the chrome's.
 */
function OpenPageBar({ onOpen, busy }) {
  // Opens on NUAT, the environment most work starts from, so the common case
  // is one click. The box stays editable for a path or a one-off host.
  const [url, setUrl] = useState(DEFAULT_ENV_URL);
  const [viewport, setViewport] = useState('desktop');
  // Headed by default: the sites people point QAi at tend to refuse a headless
  // browser, and the window is kept off-screen so headed costs nothing visible.
  const [visible, setVisible] = useState(true);

  // `busy` stays true for the several seconds a browser takes to start, and a
  // second press in that window opens a second browser.
  const submit = () => {
    if (!url.trim() || busy) return;
    onOpen(url.trim(), viewport, !visible);
  };

  return (
    <div className="address-bar">
      <EnvSelect url={url} onPick={setUrl} disabled={busy} />

      <div className="address-input">
        <Globe size={15} />
        <input
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          onKeyDown={(event) => event.key === 'Enter' && submit()}
          placeholder="turkishairlines.com"
          autoComplete="off"
          spellCheck="false"
          aria-label="Page address"
        />
        {url && (
          <button className="icon-btn-tiny" onClick={() => setUrl('')} aria-label="Clear">
            <X size={13} />
          </button>
        )}
      </div>

      <div className="segmented compact">
        {VIEWPORTS.map((item) => (
          <button
            key={item.id}
            className={viewport === item.id ? 'active' : ''}
            onClick={() => setViewport(item.id)}
            title={`${item.w} × ${item.h}`}
          >
            {item.label}
          </button>
        ))}
      </div>

      <button
        className={`toggle-chip ${visible ? 'on' : ''}`}
        onClick={() => setVisible((value) => !value)}
        title={visible
          ? 'The browser has a window (kept off-screen). Most sites refuse a headless one, so this is the default.'
          : 'No browser window. Faster, but many sites — including this one — drop a headless browser at the network layer.'}
      >
        {visible ? <Eye size={13} /> : <EyeOff size={13} />}
        {visible ? 'Visible' : 'Headless'}
      </button>

      <button className="btn btn-primary" onClick={submit} disabled={busy || !url.trim()}>
        {busy ? <Loader2 size={14} className="spin" /> : <Globe size={14} />}
        {busy ? 'Opening…' : 'Open'}
      </button>
    </div>
  );
}

// `agent` is owned by App and handed down rather than started here: a run can
// also be launched from outside this component — "Run here" on a Test Set
// scenario — and when the workspace kept its own useAgentRun those were two
// different runs. The one driving the browser was App's; the one on screen was
// this component's, permanently idle. That showed no timeline, no step count
// and no Stop, and left the page clickable while the agent was driving it.
export function WebWorkspace({
  session, agent, onOpen, onNavigate, onClose, llmConfigured, onNeedsKey, onOpenRun, onOpenExecution,
  // Present while an execution is being watched. `session` is then one of the
  // run's own browsers, not a page the tester opened, so the workspace renders
  // a read-only view: no address bar, no agent composer, and no pointer or
  // wheel reaching a browser something else is driving.
  watch = null,
}) {
  const toast = useToast();
  const sessionId = session?.sessionId ?? null;

  const [busy, setBusy] = useState(false);
  // Chromium pushes a frame when the page paints, not on a timer, so a higher
  // ceiling costs nothing on a still page and buys real smoothness on a moving
  // one. Four was the old polling rate, chosen when every frame meant a full
  // screenshot capture.
  const [fps, setFps] = useState(12);
  const [tree, setTree] = useState(null);
  const [snapshotId, setSnapshotId] = useState(null);
  const [screen, setScreen] = useState({ width: 1440, height: 900 });
  // How much the rendered frame is scaled to fit the panel. The page renders at
  // its real size and is scaled down to fit, so the whole page is visible
  // instead of a wide layout being cut off at the panel edge.
  const [fit, setFit] = useState(1);
  const [treeLoading, setTreeLoading] = useState(false);
  const [selected, setSelected] = useState(null);
  const [hovered, setHovered] = useState(null);
  const [showInspector, setShowInspector] = useState(false);
  const [showTools, setShowTools] = useState(false);
  const [suggestions, setSuggestions] = useState([]);
  const [pageSummary, setPageSummary] = useState(null);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  // The mouse is currently held down on the page. Shown, because a hold has no
  // other feedback until the page itself reacts.
  const [holding, setHolding] = useState(false);
  // True briefly after wheel activity, to speed the stream up while scrolling.
  const [wheeling, setWheeling] = useState(false);
  // A brief typed into the composer's "write scenarios" mode, awaiting review in
  // a dialog. null = closed; '' or text = open.
  const [writeBrief, setWriteBrief] = useState(null);
  // Seconds spent waiting for a watched run to open its first browser. A number
  // that moves is the whole difference between "starting" and "stuck".
  const [waited, setWaited] = useState(0);
  // What has been typed over the address, or null while nobody has touched it.
  // The field below is derived from this and the page's real address rather
  // than seeded from it: a field holding its own copy drifts the moment the
  // page moves, and the old bar's — seeded once, never refreshed — spent every
  // session claiming the address it had been opened with.
  const [typedUrl, setTypedUrl] = useState(null);

  const imgRef = useRef(null);
  const pressRef = useRef(null);
  const stageRef = useRef(null);
  const shellRef = useRef(null);
  const wheelRef = useRef({ dx: 0, dy: 0, raf: 0 });

  // While the mouse is held the page is usually animating something in
  // response, so the stream is temporarily sped up — at 4 fps a filling
  // progress ring is invisible and the hold feels like it did nothing.
  const { isFullscreen, toggle: toggleFullscreen, supported: canFullscreen } =
    useFullscreen(shellRef);

  // Holding or scrolling means the page is changing under the user's hand, so
  // the stream is temporarily sped up — otherwise the mirror lags a step behind
  // and the interaction feels unresponsive.
  const { screenshot, connection, lostReason, retry: retryStream } = useScreenStream(
    sessionId, holding || wheeling ? Math.max(fps, 24) : fps, Boolean(sessionId),
  );
  const pageLost = connection === 'lost';
  const address = typedUrl ?? session?.device?.name ?? DEFAULT_ENV_URL;

  const scan = useExplore(sessionId);

  /** Suggestions are read off the page, so they are refreshed whenever the
   *  page changes — a blank prompt box on an unfamiliar site is the point at
   *  which people give up on an agent tool. */
  const loadSuggestions = useCallback(async () => {
    if (!sessionId) {
      setSuggestions([]);
      setPageSummary(null);
      return;
    }
    setSuggestionsLoading(true);
    try {
      const data = await api.suggestions(sessionId);
      setSuggestions(data.suggestions);
      setPageSummary(data.page);
    } catch {
      // Falling back to no suggestions is fine; the composer still works.
      setSuggestions([]);
      setPageSummary(null);
    } finally {
      setSuggestionsLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!cancelled) await loadSuggestions();
    })();
    return () => {
      cancelled = true;
    };
  }, [loadSuggestions, session?.device?.name]);

  const runSuggestedAction = useCallback(
    (suggestion) => {
      if (suggestion.id === 'explore') scan.explore();
      else if (suggestion.id === 'links') scan.scanLinks();
      else if (suggestion.id === 'a11y') scan.scanAccessibility();
    },
    [scan],
  );

  /** A crashed page used to leave the last frame frozen on screen with no way
   *  forward. Reopening keeps the session id, so the run history survives. */
  const reopen = async () => {
    if (!sessionId || busy) return;
    setBusy(true);
    try {
      const data = await api.reopenWebSession(sessionId);
      setTree(null);
      setSelected(null);
      // The stream stopped for good when the page was declared lost; without
      // this the viewer keeps showing the banner over a session that is live.
      retryStream();
      toast.success(`Reopened ${data.title || data.url}.`);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };

  // Scale the rendered frame down so the whole page fits the panel, instead of
  // resizing the page to the panel — a real desktop layout is wider than this
  // panel and would otherwise have its right edge cut off. Ratio-based click
  // mapping is unaffected: the image rect scales, the proportions do not.
  useEffect(() => {
    const node = stageRef.current;
    if (!node) return undefined;

    const recompute = () => {
      const box = node.getBoundingClientRect();
      if (box.width < 40 || box.height < 40) return;
      const scale = Math.min(box.width / screen.width, box.height / screen.height);
      // Never blow the frame up past its captured resolution.
      setFit(Math.min(scale, 1));
    };

    recompute();
    const observer = new ResizeObserver(recompute);
    observer.observe(node);
    return () => observer.disconnect();
  }, [screen.width, screen.height]);

  const refreshTree = useCallback(async () => {
    if (!sessionId) return;
    setTreeLoading(true);
    try {
      const data = await api.source(sessionId);
      setTree(data.source);
      setSnapshotId(data.snapshotId);
      if (data.screen) setScreen(data.screen);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setTreeLoading(false);
    }
  }, [sessionId, toast]);

  // Load the tree once the inspector is opened, and after each run.
  useEffect(() => {
    if (!showInspector || !sessionId || tree) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const data = await api.source(sessionId);
        if (cancelled) return;
        setTree(data.source);
        setSnapshotId(data.snapshotId);
        if (data.screen) setScreen(data.screen);
      } catch (err) {
        if (!cancelled) toast.error(err.message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [showInspector, sessionId, tree, toast]);

  // The agent leaves the page somewhere else than it found it, so the tree on
  // screen is stale the moment a run ends. App owns the hook and cannot reach
  // this component's tree state, so the refresh hangs off the status instead of
  // the hook's onFinished.
  const agentStatus = agent?.status;
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && agentStatus !== 'running') refreshTree();
    wasRunning.current = agentStatus === 'running';
  }, [agentStatus, refreshTree]);

  const startAgent = useCallback(
    (goal, options) => {
      if (!llmConfigured) {
        onNeedsKey();
        return;
      }
      agent.start(goal, options);
    },
    [agent, llmConfigured, onNeedsKey],
  );

  /* A dead page still had a live Run button, and pressing it started a
     scenario that failed every step on "the page is closed" — a model call
     each, for a browser that was not there. The server refuses these now; this
     is so nobody is invited to ask. */
  const cannotRun = pageLost
    ? 'The page is no longer open. Reopen it before starting a run.'
    : null;

  const handleOpen = async (url, viewport, headless) => {
    setBusy(true);
    try {
      // Every preset renders at its real dimensions — desktop at a true desktop
      // width, not the panel's — and the frame is then scaled to fit the panel.
      await onOpen(url, viewport, headless, null);
      // Seed the frame size from the preset so the fit scale is right from the
      // first frame; source() later replaces it with the exact reported size.
      const preset = VIEWPORTS.find((v) => v.id === viewport);
      if (preset) setScreen({ width: preset.w, height: preset.h });
      setTree(null);
      setSelected(null);
      setTypedUrl(null);
      agent.reset();
    } finally {
      setBusy(false);
    }
  };

  const handleNavigate = async (url) => {
    setBusy(true);
    try {
      await onNavigate(url);
      // Back to reporting where the page is, rather than holding what was typed
      // to get it there. If the navigation failed this shows the address that
      // is still open, which is the honest answer either way.
      setTypedUrl(null);
      setTree(null);
      setSelected(null);
    } finally {
      setBusy(false);
    }
  };

  // The chrome's Go, and Enter in its address field. Locked while a navigation
  // is in flight: the second press is a second page load over the first.
  const goToAddress = () => {
    const next = address.trim();
    if (!next || busy) return;
    handleNavigate(next);
  };

  // --- direct interaction with the page image -------------------------- //

  const toPageCoords = (event) => {
    const node = imgRef.current;
    if (!node) return null;
    const rect = node.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    return {
      x: Math.round(((event.clientX - rect.left) / rect.width) * screen.width),
      y: Math.round(((event.clientY - rect.top) / rect.height) * screen.height),
    };
  };

  /**
   * `reread` is opt-out because the intermediate events of a press — the down,
   * and every move while dragging — change nothing worth re-reading, and
   * re-reading the tree on each one would fire dozens of snapshots during a
   * single hold.
   */
  const send = useCallback(
    async (payload, { reread = true } = {}) => {
      if (!sessionId) return;
      try {
        await api.gesture(sessionId, payload);
        if (reread) setTimeout(refreshTree, 600);
      } catch (err) {
        toast.error(err.message);
      }
    },
    [sessionId, refreshTree, toast],
  );

  /**
   * Press and release are forwarded separately, so the page receives the press
   * while the mouse is still down rather than a finished click on release.
   * Anything that reacts to being held — a hold-to-confirm button, a drag
   * handle, a long-press menu — can only be worked by hand this way.
   */
  const onPointerDown = async (event) => {
    if (!sessionId || event.button !== 0 || agent.status === 'running' || pageLost) return;
    const coords = toPageCoords(event);
    if (!coords) return;

    pressRef.current = { ...coords, cx: event.clientX, cy: event.clientY, at: Date.now() };
    setHolding(true);
    // Capture the pointer so a release outside the image still ends the press;
    // otherwise the page would be left with the button stuck down.
    event.currentTarget.setPointerCapture?.(event.pointerId);
    await send({ type: 'pointer_down', x: coords.x, y: coords.y }, { reread: false });
  };

  const onPointerMove = async (event) => {
    const start = pressRef.current;
    if (!start || !sessionId) return;
    const moved = Math.abs(event.clientX - start.cx) > DRAG_PX
      || Math.abs(event.clientY - start.cy) > DRAG_PX;
    if (!moved) return;
    const coords = toPageCoords(event);
    if (coords) await send({ type: 'pointer_move', x: coords.x, y: coords.y }, { reread: false });
  };

  const onPointerUp = async (event) => {
    if (!sessionId || (event.button !== undefined && event.button !== 0)) return;
    const start = pressRef.current;
    pressRef.current = null;
    setHolding(false);
    if (!start) return;

    const end = toPageCoords(event) || start;
    await send({ type: 'pointer_up', x: end.x, y: end.y });
    // A long press or a drag changes what is on screen without necessarily
    // navigating, so re-read rather than waiting for the next poll.
    if (Date.now() - start.at > 400) setTimeout(refreshTree, 500);
  };

  // A press that ends outside the window would otherwise leave the page with
  // the mouse button held down forever.
  useEffect(() => {
    if (!holding) return undefined;
    const release = () => {
      const start = pressRef.current;
      pressRef.current = null;
      setHolding(false);
      if (start && sessionId) send({ type: 'pointer_up', x: start.x, y: start.y });
    };
    window.addEventListener('blur', release);
    window.addEventListener('pointerup', release);
    return () => {
      window.removeEventListener('blur', release);
      window.removeEventListener('pointerup', release);
    };
  }, [holding, sessionId, send]);

  // The wheel is forwarded as raw pixel deltas, coalesced to one request per
  // animation frame. Sending each tick's `send` (which also re-reads the tree)
  // is what made scrolling lag and overshoot to the bottom.
  const flushWheel = useCallback(() => {
    const w = wheelRef.current;
    w.raf = 0;
    const { dx, dy } = w;
    w.dx = 0;
    w.dy = 0;
    if (!sessionId || (!dx && !dy)) return;
    api.gesture(sessionId, { type: 'wheel', dx, dy }).catch(() => {});
  }, [sessionId]);

  const onWheel = (event) => {
    if (!sessionId || agent.status === 'running' || pageLost) return;
    event.preventDefault();
    // Display pixels → page pixels: the page is rendered larger and scaled down
    // by `fit`, so a wheel move on screen covers 1/fit as much of the page.
    const scale = fit > 0 ? 1 / fit : 1;
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? screen.height : 1;
    const w = wheelRef.current;
    w.dx += event.deltaX * unit * scale;
    w.dy += event.deltaY * unit * scale;
    if (!w.raf) w.raf = requestAnimationFrame(flushWheel);
    if (!wheeling) setWheeling(true);
    clearTimeout(w.stop);
    w.stop = setTimeout(() => setWheeling(false), 500);
  };

  // The coalescing frame and the "still scrolling" timer outlive the component
  // otherwise: closing the page mid-scroll fired a gesture at a session being
  // torn down and set state after unmount.
  useEffect(() => {
    const w = wheelRef.current;
    return () => {
      if (w.raf) cancelAnimationFrame(w.raf);
      clearTimeout(w.stop);
      w.raf = 0;
      w.dx = 0;
      w.dy = 0;
    };
  }, []);

  const awaitingFirstFrame = Boolean(watch) && !session && !watch?.finished;
  useEffect(() => {
    if (!awaitingFirstFrame) return undefined;
    const started = Date.now();
    const id = setInterval(
      () => setWaited(Math.round((Date.now() - started) / 1000)),
      1000,
    );
    return () => {
      clearInterval(id);
      setWaited(0);
    };
  }, [awaitingFirstFrame]);

  const highlight = useMemo(() => {
    const node = hovered || selected;
    if (!node) return null;
    const box = parseBounds(node.bounds, screen);
    return box ? { ...box, color: roleColor(node.role) } : null;
  }, [hovered, selected, screen]);

  // Live means a frame has actually arrived, not merely that a session exists —
  // there is a second or two between the two, and claiming the first during the
  // second is how a pill stops being worth reading. A stream that has died goes
  // on showing its last frame, so "live" has to give way to the truth as well.
  const watchState = connection === 'lost' ? 'lost' : screenshot ? 'live' : 'pending';

  // --- empty state ------------------------------------------------------ //

  if (watch) {
    return (
      <main className="page web-page">
        <header className="page-header compact">
          <div className="web-title-row">
            <h1 className="page-title">Web</h1>
            {/* Connecting and connected are different things, and the wait
                between them is long enough that saying so matters. This is the
                only place the stream's state is given: it used to be said again
                over the frame, from the same two values, so the two could only
                ever agree. */}
            <span className={`watch-pill ${watchState}`}>
              {watchState === 'live' && <><Radio size={12} /> Live</>}
              {watchState === 'pending' && <><Loader2 size={12} className="spin" /> Connecting</>}
              {watchState === 'lost' && <><AlertTriangle size={12} /> Page lost</>}
            </span>
            <span className="watch-exec" title="The execution being watched">
              {watch.name || 'Execution'}
            </span>
          </div>
          <div className="web-header-actions">
            {watch.onOpenExecution && (
              <button className="btn btn-ghost btn-sm" onClick={watch.onOpenExecution}>
                Open execution
              </button>
            )}
            {/* Two different stops, so both say which one they are: one ends
                the run, the other only leaves this view. */}
            {watch.onStop && !watch.finished && (
              <button className="btn btn-danger btn-sm" onClick={watch.onStop}>
                <Square size={13} /> Stop run
              </button>
            )}
            <button className="btn btn-ghost btn-sm" onClick={watch.onExit}>
              <X size={13} /> Stop watching
            </button>
          </div>
        </header>

        {/* One browser per scenario runs at a time, up to the worker count, so
            the strip is how the tester moves between them. Hidden for a single
            worker, where there is nothing to choose. */}
        {watch.sessions.length > 1 && (
          <div className="watch-strip" role="tablist" aria-label="Running scenarios">
            {watch.sessions.map((item) => (
              <button
                key={item.sessionId}
                role="tab"
                aria-selected={item.sessionId === session?.sessionId}
                className={`watch-tab ${item.sessionId === session?.sessionId ? 'active' : ''}`}
                onClick={() => watch.onPick(item.sessionId)}
                title={item.watching.label}
              >
                <span className="watch-tab-idx">#{item.watching.idx}</span>
                <span className="watch-tab-name">{item.watching.label}</span>
              </button>
            ))}
          </div>
        )}

        {session ? (
          <>
            <div className="watch-scenario">
              <span className="watch-scenario-idx">#{session.watching?.idx}</span>
              <span className="watch-scenario-name">{session.watching?.label}</span>
            </div>
            <div className="browser-viewport watching" ref={stageRef}>
              {screenshot ? (
                <div
                  className="browser-canvas"
                  style={{ width: screen.width, height: screen.height, transform: `scale(${fit})` }}
                >
                  {/* No pointer or wheel handlers: this browser belongs to the
                      run, and a stray click is a corrupted result. */}
                  <img
                    ref={imgRef}
                    src={`data:image/jpeg;base64,${screenshot}`}
                    alt={`Live view of ${session.watching?.label || 'the running scenario'}`}
                    className="browser-image"
                    draggable={false}
                    onContextMenu={(event) => event.preventDefault()}
                  />
                </div>
              ) : (
                <div className="browser-placeholder">
                  <Loader2 size={22} className="spin" />
                  <span>Waiting for the first frame…</span>
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="empty-state">
            {watch.finished ? <Radio size={34} /> : <Loader2 size={34} className="spin" />}
            <h3>{watch.finished ? 'Every scenario has finished' : 'Opening the browser'}</h3>
            <p>
              {watch.finished
                ? 'The run has no browser open any more. Its report is on the execution.'
                : 'A browser takes a few seconds to start and reach the page. '
                  + 'The scenario appears here the moment it does.'}
            </p>
            {/* Something true and moving, so a ten-second wait does not read as
                a page that has stopped. */}
            {!watch.finished && (
              <p className="watch-wait-progress">
                <span className="watch-wait-dot" />
                {watch.progress?.status === 'running' || !watch.progress
                  ? 'Run started'
                  : `Run ${watch.progress.status}`}
                {watch.progress?.done > 0 && ` · ${watch.progress.done} scenario${
                  watch.progress.done === 1 ? '' : 's'} finished`}
                {waited > 2 && ` · ${waited}s`}
              </p>
            )}
            {watch.onOpenExecution && (
              <button className="btn btn-primary" onClick={watch.onOpenExecution}>
                Open execution
              </button>
            )}
          </div>
        )}
      </main>
    );
  }

  if (!session) {
    return (
      <main className="page web-page">
        <header className="page-header">
          <div>
            <h1 className="page-title">Web</h1>
            <p className="page-subtitle">
              Give QAi a page and describe what to test. No Appium, no device — just a URL.
            </p>
          </div>
        </header>

        <OpenPageBar onOpen={handleOpen} busy={busy} />

        <div className="empty-state">
          <Globe size={34} />
          <h3>No page open yet</h3>
          {/* True of every page opened here and read once, so it waits behind a
              line rather than taking a paragraph under the bar that opens one. */}
          <details className="web-fold">
            <summary>What the screen becomes once a page is open</summary>
            <p className="muted small web-fold-body">
              The agent runs down the left and the live page fills the right, so a run and the
              thing it is doing stay visible together. Every step is recorded with a screenshot,
              and you can click, hold and scroll the page yourself at any time.
            </p>
          </details>
        </div>
      </main>
    );
  }

  // --- workspace -------------------------------------------------------- //

  return (
    <main className="page web-page">
      <header className="page-header compact">
        <div className="web-title-row">
          <h1 className="page-title">Web</h1>
          {/* When the page is gone the banner over the frame says so, says why,
              and carries the way back. A pill repeating it up here is the same
              sentence twice on one screen, and the shorter of the two. */}
          {!pageLost && (
            <span className={`stream-pill ${connection}`}>
              <span className="status-dot" />
              {connection === 'live' ? 'live' : connection}
            </span>
          )}
          {/* States what this session is, not a switch that happened: the
              headless→headed fallback was removed from the backend, but the
              badge kept claiming the site had refused a background browser —
              on every page opened with the default toggle. */}
          {session.device.headless === false && (
            <span
              className="badge virtual"
              title={
                'Bu oturum pencereli (headed) bir tarayıcı kullanıyor — çoğu site '
                + 'ekransız tarayıcıyı reddettiği için varsayılan bu. Pencere ekran '
                + 'dışında tutuluyor, masaüstünde görünmez.'
              }
            >
              tam tarayıcı
            </span>
          )}
        </div>
        {/* What is here is about the workspace; what is about the page is in
            the chrome under it. Re-reading the page was in both, as Reload here
            and as the circular arrow there, running the same function — two
            buttons for one thing, three inches apart. */}
        <div className="web-header-actions">
          <button
            className={`btn btn-ghost btn-sm ${showTools ? 'active' : ''}`}
            onClick={() => setShowTools((value) => !value)}
            title="Saved sign-ins, network mocking, visual baselines, page errors"
          >
            <Wrench size={13} />
            Tools
          </button>
          <button
            className={`btn btn-ghost btn-sm ${showInspector ? 'active' : ''}`}
            onClick={() => setShowInspector((value) => !value)}
          >
            {showInspector ? <PanelRightClose size={13} /> : <PanelRightOpen size={13} />}
            Inspector
          </button>
          <button className="btn btn-ghost btn-sm web-session-close" onClick={onClose}>
            <X size={13} />
            Close page
          </button>
        </div>
      </header>

      {showTools && (
        <WebTools sessionId={session.sessionId} onClose={() => setShowTools(false)} />
      )}

      {/* Agent on the left, page on the right: the run and the thing it is
          doing stay visible at the same time, and the page gets the full
          column height instead of sharing it with the transcript. */}
      <div className={`web-body ${showInspector ? 'with-inspector' : ''}`}>
        <div className="web-agent">
          {/* A scan takes over the column while it runs: its findings are the
              thing to read, and interleaving them with the agent transcript
              made both unreadable. */}
          {scan.mode ? (
            <ScanPanel
              mode={scan.mode}
              running={scan.running}
              rows={scan.rows}
              summary={scan.summary}
              tally={scan.tally}
              error={scan.error}
              warning={scan.warning}
              progress={scan.progress}
              runId={scan.runId}
              onBack={scan.clear}
              onStop={scan.stop}
              onOpenRun={onOpenRun}
            />
          ) : (
            <AgentPanel
              timeline={agent.timeline}
              waitingAt={agent.waitingAt}
              stepping={agent.stepping}
              onStep={agent.step}
              onStepMode={agent.setStepMode}
              status={agent.status}
              currentStep={agent.currentStep}
              maxSteps={agent.maxSteps}
              runId={agent.runId}
              cannotRun={cannotRun}
              onStart={startAgent}
              onWriteScenarios={(b) => setWriteBrief(b || '')}
              onStop={agent.stop}
              onReset={agent.reset}
              onOpenRun={onOpenRun}
              placeholder='Ne test edelim? Örn. "Çerez uyarısını kapat, İstanbul ara ve sonuçları doğrula"'
              heading="Ne test edelim?"
              intro="Aşağıdaki öneriler bu sayfadan okundu. Mavi olanlar model kullanmadan hemen çalışır."
              examples={suggestions.length ? suggestions : SUGGESTIONS}
              pageSummary={pageSummary}
              onAction={runSuggestedAction}
              suggestionsLoading={suggestionsLoading}
            />
          )}
        </div>

        <div className="web-main">
          <div
            className={`browser-shell ${isFullscreen ? 'fullscreen' : ''}`}
            ref={shellRef}
          >
            {/* One address, in the chrome, editable. The workspace used to
                carry a second bar above the body with a field of its own, and
                the two disagreed by design: this one reported where the page
                was, that one held whatever had last been typed into it. */}
            <div className="browser-bar web-chrome">
              {/* Nothing here reaches a page that is gone, and the banner below
                  already says what to do instead. Same reason the agent's Run
                  is refused while the page is lost: a control that can only
                  fail should not be offered. */}
              <button
                className="browser-nav"
                onClick={() => send({ type: 'key', key: 'back' })}
                disabled={pageLost}
                title="Back"
                aria-label="Back"
              >
                <ArrowLeft size={13} />
              </button>
              <button
                className="browser-nav"
                onClick={() => send({ type: 'key', key: 'forward' })}
                disabled={pageLost}
                title="Forward"
                aria-label="Forward"
              >
                <ArrowRight size={13} />
              </button>
              <button
                className="browser-nav"
                onClick={refreshTree}
                disabled={treeLoading || pageLost}
                title="Re-read the page"
                aria-label="Re-read the page"
              >
                <RotateCcw size={13} className={treeLoading ? 'spin' : ''} />
              </button>

              <EnvSelect url={address} onPick={setTypedUrl} disabled={busy || pageLost} />

              <div className="address-input">
                <Globe size={14} />
                <input
                  value={address}
                  onChange={(event) => setTypedUrl(event.target.value)}
                  onKeyDown={(event) => event.key === 'Enter' && goToAddress()}
                  placeholder="turkishairlines.com"
                  autoComplete="off"
                  spellCheck="false"
                  aria-label="Page address"
                />
                {address && (
                  <button
                    className="icon-btn-tiny"
                    onClick={() => setTypedUrl('')}
                    aria-label="Clear the address"
                  >
                    <X size={13} />
                  </button>
                )}
              </div>

              <button
                className="btn btn-ghost btn-sm"
                onClick={goToAddress}
                disabled={busy || pageLost || !address.trim()}
              >
                {busy && <Loader2 size={13} className="spin" />}
                {busy ? 'Going…' : 'Go'}
              </button>

              <span className="browser-size">
                {screen.width} × {screen.height}
              </span>
              {canFullscreen && (
                <button
                  className="browser-nav"
                  onClick={toggleFullscreen}
                  title={isFullscreen ? 'Leave fullscreen (Esc)' : 'Fullscreen'}
                  aria-label={isFullscreen ? 'Leave fullscreen' : 'Show the page fullscreen'}
                >
                  {isFullscreen ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
                </button>
              )}
            </div>

            {pageLost && (
              <div className="banner danger page-lost">
                <AlertTriangle size={15} />
                <span>
                  The page is no longer responding — {lostReason}. The view below is the last
                  frame it sent.
                </span>
                {/* Reopening takes seconds and looks like nothing happening, so
                    the button locks — a second press opens a second browser. */}
                <button className="btn btn-primary btn-sm" onClick={reopen} disabled={busy}>
                  {busy ? <Loader2 size={13} className="spin" /> : <RotateCcw size={13} />}
                  Reopen page
                </button>
              </div>
            )}

            <div className={`browser-viewport ${pageLost ? 'stale' : ''}`} ref={stageRef}>
              {screenshot ? (
                // The page renders at its real size and the whole frame is
                // scaled to fit the panel, so nothing is cropped. The image and
                // its overlay live in one scaled box, keeping them aligned and
                // preserving the ratios the click mapping relies on.
                <div
                  className="browser-canvas"
                  style={{
                    width: screen.width,
                    height: screen.height,
                    transform: `scale(${fit})`,
                  }}
                >
                  <img
                    ref={imgRef}
                    src={`data:image/jpeg;base64,${screenshot}`}
                    alt="Live page"
                    className="browser-image"
                    draggable={false}
                    onPointerDown={onPointerDown}
                    onPointerMove={onPointerMove}
                    onPointerUp={onPointerUp}
                    onWheel={onWheel}
                    onContextMenu={(event) => event.preventDefault()}
                  />
                  {highlight && (
                    <svg className="browser-overlay" aria-hidden="true">
                      <rect
                        x={`${highlight.left}%`}
                        y={`${highlight.top}%`}
                        width={`${highlight.width}%`}
                        height={`${highlight.height}%`}
                        fill={highlight.color}
                        fillOpacity="0.16"
                        stroke={highlight.color}
                        strokeWidth="2"
                        rx="2"
                      />
                    </svg>
                  )}
                </div>
              ) : (
                <div className="browser-placeholder">
                  <Loader2 size={22} className="spin" />
                  <p>Waiting for the first frame…</p>
                </div>
              )}
            </div>

            <div className="browser-foot">
              <span className="muted-tiny">
                {holding
                  ? 'Basılı tutuluyor — bırakana kadar sayfa basılı görüyor.'
                  : 'Sayfaya doğrudan tıklayabilir, basılı tutabilir ve kaydırabilirsiniz.'}
              </span>
              <span className="fps-inline">
                <input
                  type="range"
                  min="1"
                  max="30"
                  value={fps}
                  onChange={(event) => setFps(Number(event.target.value))}
                  aria-label="Stream rate"
                />
                {fps} fps
              </span>
            </div>
          </div>
        </div>

        {showInspector && (
          <aside className="web-inspector">
            <Inspector
              sessionId={sessionId}
              tree={tree}
              snapshotId={snapshotId}
              loading={treeLoading}
              selected={selected}
              onSelect={setSelected}
              onHover={setHovered}
              onLeave={() => setHovered(null)}
              onRefresh={refreshTree}
            />
          </aside>
        )}
      </div>

      {writeBrief !== null && (
        <ScenarioReviewModal
          sessionId={sessionId}
          brief={writeBrief}
          onOpenExecution={onOpenExecution}
          onClose={() => setWriteBrief(null)}
        />
      )}

      {/* Scenarios the agent wrote from a plain chat request ("…senaryolarını
          yaz") arrive here for review instead of being filed on their own. */}
      {agent.proposed && (
        <ScenarioReviewModal
          sessionId={sessionId}
          scenarios={agent.proposed.scenarios}
          readFrom={agent.proposed.readFrom}
          suggestedName={agent.proposed.suggestedName}
          onOpenExecution={onOpenExecution}
          onClose={agent.clearProposed}
        />
      )}
    </main>
  );
}
