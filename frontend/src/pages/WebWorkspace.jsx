import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle, ArrowLeft, ArrowRight, Eye, EyeOff, Globe, Loader2, Maximize2, Minimize2,
  PanelRightClose, PanelRightOpen, RefreshCw, RotateCcw, Wrench, X,
} from 'lucide-react';

import { api } from '../api';
import { AgentPanel } from '../components/AgentPanel';
import { ScenarioReviewModal } from '../components/ScenarioReviewModal';
import { Inspector } from '../components/Inspector';
import { ScanPanel } from '../components/ScanPanel';
import { WebTools } from '../components/WebTools';
import { useAgentRun } from '../hooks/useAgentRun';
import { useExplore } from '../hooks/useExplore';
import { useFullscreen } from '../hooks/useFullscreen';
import { useScreenStream } from '../hooks/useScreenStream';
import { useToast } from '../hooks/useToast';
import { parseBounds, roleColor } from '../lib/elements';

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

/** The address bar: open a page, navigate, or close the session. */
function AddressBar({ session, onOpen, onNavigate, onClose, busy }) {
  const [url, setUrl] = useState('');
  const [viewport, setViewport] = useState('desktop');
  // Headed by default: the sites people point QAi at tend to refuse a headless
  // browser, and the window is kept off-screen so headed costs nothing visible.
  const [visible, setVisible] = useState(true);

  const submit = () => {
    if (!url.trim() || busy) return;
    if (session) onNavigate(url.trim());
    else onOpen(url.trim(), viewport, !visible);
  };

  return (
    <div className="address-bar">
      <div className="address-input">
        <Globe size={15} />
        <input
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          onKeyDown={(event) => event.key === 'Enter' && submit()}
          placeholder={session ? session.device.name : 'turkishairlines.com'}
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

      {!session && (
        <>
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
            className={`chip-toggle ${visible ? 'on' : ''}`}
            onClick={() => setVisible((value) => !value)}
            title="Some sites refuse headless browsers. QAi retries visibly on its own."
          >
            {visible ? <Eye size={13} /> : <EyeOff size={13} />}
            {visible ? 'Visible' : 'Headless'}
          </button>
        </>
      )}

      <button className="btn btn-primary" onClick={submit} disabled={busy || !url.trim()}>
        {busy ? <Loader2 size={14} className="spin" /> : <Globe size={14} />}
        {busy ? 'Opening…' : session ? 'Go' : 'Open'}
      </button>

      {session && (
        <button className="btn btn-danger" onClick={onClose}>
          Close page
        </button>
      )}
    </div>
  );
}

export function WebWorkspace({ session, onOpen, onNavigate, onClose, llmConfigured, onNeedsKey, onOpenRun }) {
  const toast = useToast();
  const sessionId = session?.sessionId ?? null;

  const [busy, setBusy] = useState(false);
  const [fps, setFps] = useState(4);
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
  const { screenshot, connection, lostReason } = useScreenStream(
    sessionId, holding || wheeling ? Math.max(fps, 15) : fps, Boolean(sessionId),
  );
  const pageLost = connection === 'lost';

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

  const agent = useAgentRun(sessionId, { onFinished: () => refreshTree() });

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
      agent.reset();
    } finally {
      setBusy(false);
    }
  };

  const handleNavigate = async (url) => {
    setBusy(true);
    try {
      await onNavigate(url);
      setTree(null);
      setSelected(null);
    } finally {
      setBusy(false);
    }
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

  const highlight = useMemo(() => {
    const node = hovered || selected;
    if (!node) return null;
    const box = parseBounds(node.bounds, screen);
    return box ? { ...box, color: roleColor(node.role) } : null;
  }, [hovered, selected, screen]);

  // --- empty state ------------------------------------------------------ //

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

        <AddressBar session={null} onOpen={handleOpen} busy={busy} />

        <div className="empty-state">
          <Globe size={34} />
          <h3>Open a page to start</h3>
          <p>
            The page appears below at full width, the agent runs underneath it, and every step is
            recorded with a screenshot. You can click and scroll the page yourself at any time.
          </p>
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
          <span className={`stream-pill ${connection}`}>
            <span className="status-dot" />
            {connection === 'live' ? 'live' : connection === 'lost' ? 'page lost' : connection}
          </span>
          {session.device.headless === false && (
            <span
              className="badge virtual"
              title={
                'Bu site ekransız (arka planda çalışan) tarayıcıları kabul etmiyor, '
                + 'QAi pencereli tam bir tarayıcıya geçti. Pencere ekran dışında '
                + 'tutuluyor, masaüstünde görünmez. Yapmanız gereken bir şey yok.'
              }
            >
              tam tarayıcı
            </span>
          )}
        </div>
        <div className="web-header-actions">
          <button className="btn btn-ghost btn-sm" onClick={refreshTree} disabled={treeLoading}>
            <RefreshCw size={13} className={treeLoading ? 'spin' : ''} />
            Reload
          </button>
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
        </div>
      </header>

      {showTools && (
        <WebTools sessionId={session.sessionId} onClose={() => setShowTools(false)} />
      )}

      <AddressBar
        session={session}
        onOpen={handleOpen}
        onNavigate={handleNavigate}
        onClose={onClose}
        busy={busy}
      />

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
              status={agent.status}
              currentStep={agent.currentStep}
              maxSteps={agent.maxSteps}
              runId={agent.runId}
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
            <div className="browser-bar">
              <button className="browser-nav" onClick={() => send({ type: 'key', key: 'back' })} title="Back">
                <ArrowLeft size={13} />
              </button>
              <button className="browser-nav" onClick={() => send({ type: 'key', key: 'forward' })} title="Forward">
                <ArrowRight size={13} />
              </button>
              <button className="browser-nav" onClick={refreshTree} title="Re-read the page">
                <RotateCcw size={13} />
              </button>
              <span className="browser-address">{session.device.name}</span>
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
                <button className="btn btn-primary btn-sm" onClick={reopen} disabled={busy}>
                  {busy ? <Loader2 size={13} className="spin" /> : <RotateCcw size={13} />}
                  Reopen page
                </button>
                <button className="btn btn-ghost btn-sm" onClick={onClose}>
                  Close
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
                  max="15"
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
          onClose={agent.clearProposed}
        />
      )}
    </main>
  );
}
