import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { api } from './api';
import { AgentPanel } from './components/AgentPanel';
import { DeviceMirror } from './components/DeviceMirror';
import { Inspector } from './components/Inspector';
import { ScenarioReviewModal } from './components/ScenarioReviewModal';
import { Sidebar } from './components/Sidebar';
import { useAgentRun } from './hooks/useAgentRun';
import { useScreenStream } from './hooks/useScreenStream';
import { useTheme } from './hooks/useTheme';
import { useToast } from './hooks/useToast';
import { parseBounds, roleColor } from './lib/elements';
import { applyEnvironment } from './lib/environments';
import { osOf } from './lib/platforms';
import { BugsPage } from './pages/BugsPage';
import { ExecutionsPage } from './pages/ExecutionsPage';
import { InsightsPage } from './pages/InsightsPage';
import { RunsPage } from './pages/RunsPage';
import { SettingsPage } from './pages/SettingsPage';
import { StudioPage } from './pages/StudioPage';
import { SuitesPage } from './pages/SuitesPage';
import { TestDataPage } from './pages/TestDataPage';
import { WebWorkspace } from './pages/WebWorkspace';
import './App.css';

export default function App() {
  const toast = useToast();
  const { theme, toggle: toggleTheme } = useTheme();

  const [activeTab, setActiveTab] = useState('web');
  // Which platform Bug Report should open on when a raised bug sends us there.
  const [bugsPlatform, setBugsPlatform] = useState(null);
  const [agentSubTab, setAgentSubTab] = useState('chat'); // chat | inspector

  const [sessions, setSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState(null);

  const [appiumStatus, setAppiumStatus] = useState('stopped');
  const [llmConfigured, setLlmConfigured] = useState(false);

  const [tree, setTree] = useState(null);
  const [snapshotId, setSnapshotId] = useState(null);
  // Read off the device the same way the web workspace reads off the page:
  // three fixed English e-commerce examples told a TK tester nothing.
  const [deviceSuggestions, setDeviceSuggestions] = useState([]);
  const [devicePage, setDevicePage] = useState(null);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  const [screen, setScreen] = useState({ width: 1080, height: 2400 });
  const [treeLoading, setTreeLoading] = useState(false);
  const [selectedElement, setSelectedElement] = useState(null);
  const [hoveredElement, setHoveredElement] = useState(null);

  const [fps, setFps] = useState(2);
  const [selectedRunId, setSelectedRunId] = useState(null);
  // An execution just started elsewhere, to open and follow on the Executions
  // page — starting one should land you where you can watch it.
  const [focusExecutionId, setFocusExecutionId] = useState(null);
  // A brief from the composer's "write scenarios" mode, awaiting review in a
  // dialog. null = closed.
  const [writeBrief, setWriteBrief] = useState(null);
  // The execution currently being watched in a workspace, and which of its
  // scenarios is on screen. Starting a run lands here: a suite drives browsers
  // of its own, and until they were registered there was nothing to watch.
  const [watchExecutionId, setWatchExecutionId] = useState(null);
  const [watchSessionId, setWatchSessionId] = useState(null);
  // Whether a browser of the watched run has been seen yet, so "still starting"
  // and "all finished" are not the same empty screen. Set from the poll's own
  // callback rather than an effect watching the list — the value comes from
  // outside React, which is where it should be read.
  const [watchSeen, setWatchSeen] = useState(false);
  // How the watched run is getting on, so the wait before the first browser
  // opens shows something moving rather than a fixed sentence.
  const [watchProgress, setWatchProgress] = useState(null);

  // Read by connectDevice, which must see the current sessions without taking
  // them as a dependency — that would rebuild the callback on every change.
  const sessionsRef = useRef(sessions);
  useEffect(() => {
    sessionsRef.current = sessions;
  }, [sessions]);

  const activeSession = useMemo(
    () => sessions.find((session) => session.sessionId === activeSessionId) || null,
    [sessions, activeSessionId],
  );

  // A running execution registers a browser per scenario so it can be watched.
  // Those are not the tester's own page and must never be picked up as one —
  // the workspace would offer to close a browser the run is driving, and the
  // page the tester opened would vanish from under them mid-run.
  const ownSessions = useMemo(() => sessions.filter((s) => !s.watching), [sessions]);
  const watchedSessions = useMemo(() => sessions.filter((s) => s.watching), [sessions]);

  // Every live browser of the execution being watched, in scenario order.
  const watchList = useMemo(
    () => watchedSessions
      .filter((s) => s.watching.suiteRunId === watchExecutionId)
      .sort((a, b) => (a.watching.idx || 0) - (b.watching.idx || 0)),
    [watchedSessions, watchExecutionId],
  );
  const watchSession = useMemo(
    () => watchList.find((s) => s.sessionId === watchSessionId) || watchList[0] || null,
    [watchList, watchSessionId],
  );
  // The execution's name, taken from whichever of its browsers is up. Not
  // fetched separately: the session list already carries it and a run with no
  // browser left has nothing to name anyway.
  const watchName = watchList[0]?.watching?.name || null;


  // Each workspace owns its own kind of session, so switching tabs never shows
  // a phone bezel around a web page or vice versa.
  const mobileSessions = useMemo(
    () => ownSessions.filter((session) => session.device.kind !== 'web'),
    [ownSessions],
  );
  const webSession = useMemo(
    () => ownSessions.find((session) => session.device.kind === 'web') || null,
    [ownSessions],
  );
  const mobileSession = useMemo(
    () => mobileSessions.find((session) => session.sessionId === activeSessionId) || mobileSessions[0] || null,
    [mobileSessions, activeSessionId],
  );

  // The web workspace renders its own full-width viewer and drives its own
  // stream; only the mobile workspace uses the phone mirror.
  const showMirror = activeTab === 'mobile' && Boolean(mobileSession);
  const { screenshot, connection } = useScreenStream(
    showMirror ? mobileSession?.sessionId : null,
    fps,
    showMirror,
  );

  // ---------------------------------------------------------------- health --
  const refreshHealth = useCallback(async () => {
    try {
      const data = await api.health();
      setLlmConfigured(Boolean(data.llm_connected));
    } catch {
      setLlmConfigured(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.health();
        if (!cancelled) setLlmConfigured(Boolean(data.llm_connected));
      } catch {
        if (!cancelled) setLlmConfigured(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Poll the Appium status on a single timer chain. The status is written into
  // a ref-free state that the effect does not depend on, so the loop is set up
  // exactly once instead of being torn down on every status change.
  useEffect(() => {
    let cancelled = false;
    let timer = null;

    const tick = async () => {
      try {
        const data = await api.appiumStatus();
        if (!cancelled) setAppiumStatus(data.status);
      } catch {
        if (!cancelled) setAppiumStatus('stopped');
      } finally {
        if (!cancelled) timer = setTimeout(tick, 3000);
      }
    };

    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  // Recover sessions the backend still holds (e.g. after a page reload).
  useEffect(() => {
    api
      .sessions()
      .then((data) => {
        if (data.sessions?.length) {
          setSessions(data.sessions);
          const own = data.sessions.find((s) => !s.watching);
          if (own) setActiveSessionId((current) => current || own.sessionId);
        }
      })
      .catch(() => {
        /* backend not up yet; the status strip shows it */
      });
  }, []);

  // While an execution is being watched the list has to be re-read: the run
  // opens a browser per scenario and closes it the moment that scenario ends,
  // so the set of watchable sessions turns over as the suite progresses. One
  // self-rescheduling chain, torn down when watching stops.
  useEffect(() => {
    if (!watchExecutionId) return undefined;
    let cancelled = false;
    let timer = null;
    const tick = async () => {
      try {
        const [data, run] = await Promise.all([
          api.sessions(),
          // Read alongside the sessions so the wait has something true to say.
          // Opening the first browser takes the better part of ten seconds, and
          // a motionless screen for that long reads as broken.
          api.suiteRun(watchExecutionId).catch(() => null),
        ]);
        if (cancelled) return;
        const list = data.sessions || [];
        setSessions(list);
        if (list.some((s) => s.watching?.suiteRunId === watchExecutionId)) setWatchSeen(true);
        if (run) {
          setWatchProgress({
            status: run.status,
            done: (run.runs || []).length,
            passed: run.passed || 0,
            failed: run.failed || 0,
            startedAt: run.started_at || null,
          });
        }
      } catch {
        /* the status strip already reports a backend that is not answering */
      } finally {
        if (!cancelled) timer = setTimeout(tick, 2000);
      }
    };
    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [watchExecutionId]);

  // ------------------------------------------------------------------ DOM ---
  const refreshTree = useCallback(async () => {
    if (!activeSessionId) return;
    setTreeLoading(true);
    try {
      const data = await api.source(activeSessionId);
      setTree(data.source);
      setSnapshotId(data.snapshotId);
      if (data.screen) setScreen(data.screen);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setTreeLoading(false);
    }
  }, [activeSessionId, toast]);

  // Load the tree the first time the inspector is opened for a session. The
  // fetch is inlined rather than calling refreshTree() so nothing sets state
  // synchronously inside the effect body.
  useEffect(() => {
    if (!(activeTab === 'agent' && agentSubTab === 'inspector' && activeSessionId && !tree)) return undefined;

    let cancelled = false;
    (async () => {
      try {
        const data = await api.source(activeSessionId);
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
  }, [activeTab, agentSubTab, activeSessionId, tree, toast]);

  // --------------------------------------------------------------- sessions -
  /* Returns the new session, or null. Callers that need to run something on the
     phone they just connected — starting an execution against a chosen build —
     have no other way to learn its id. */
  const connectDevice = useCallback(
    async (device, appId) => {
      // A session already open on this phone has to be closed, not just
      // dropped from the list: two Appium sessions against one device collide
      // on WebDriverAgent's port, and the orphan holds it.
      const previous = sessionsRef.current.find((s) => s.device.udid === device.udid);
      if (previous) {
        try {
          await api.deleteSession(previous.sessionId);
        } catch {
          /* already gone on the backend, which is the outcome we wanted */
        }
      }
      try {
        const data = await api.createSession(device, appId);
        const session = { sessionId: data.sessionId, device: data.device };
        setSessions((current) => [...current.filter((s) => s.device.udid !== device.udid), session]);
        setActiveSessionId(data.sessionId);
        setTree(null);
        setSelectedElement(null);
        toast.success(`Connected to ${device.name}.`);
        return session;
      } catch (err) {
        toast.error(err.message);
        return null;
      }
    },
    [toast],
  );

  const openWebPage = useCallback(
    async (url, viewport, headless = true) => {
      try {
        const data = await api.createWebSession(url, viewport, 'chromium', headless);
        setSessions((current) => [...current, { sessionId: data.sessionId, device: data.device }]);
        setActiveSessionId(data.sessionId);
        setTree(null);
        setSelectedElement(null);
        toast.success(`Opened ${data.device.title || data.device.name}.`);
        // Returned so a caller that needs to act on the new session can, without
        // waiting for the render that puts it in state.
        return data;
      } catch (err) {
        toast.error(err.message);
        return null;
      }
    },
    [toast],
  );

  const navigateWeb = useCallback(
    async (url) => {
      if (!webSession) return;
      try {
        const data = await api.navigate(webSession.sessionId, url);
        setSessions((current) =>
          current.map((session) =>
            session.sessionId === webSession.sessionId
              ? { ...session, device: { ...session.device, name: data.url, title: data.title } }
              : session,
          ),
        );
        toast.success(`Navigated to ${data.title || data.url}.`);
      } catch (err) {
        toast.error(err.message);
      }
    },
    [webSession, toast],
  );

  const disconnectDevice = useCallback(
    async (sessionId) => {
      try {
        await api.deleteSession(sessionId);
      } catch {
        /* the session is going away locally regardless */
      }
      setSessions((current) => {
        const remaining = current.filter((session) => session.sessionId !== sessionId);
        setActiveSessionId((active) => (active === sessionId ? remaining[0]?.sessionId ?? null : active));
        return remaining;
      });
      setTree(null);
      setSelectedElement(null);
      toast.info('Session closed.');
    },
    [toast],
  );

  // ------------------------------------------------------------------ agent -
  // The visible workspace owns the session the agent runs against. Connecting a
  // phone makes it the active session, so binding the one agent hook to
  // activeSessionId alone would let a run started from the Web tab drive the
  // phone while the browser sat on screen. Derived rather than synced into
  // state: on any other tab the active session is still the right answer.
  const agentSessionId = useMemo(() => {
    if (activeTab === 'web') return webSession?.sessionId ?? null;
    if (activeTab === 'mobile') return mobileSession?.sessionId ?? null;
    return activeSessionId;
  }, [activeTab, webSession, mobileSession, activeSessionId]);

  const agent = useAgentRun(agentSessionId, {
    onFinished: () => refreshTree(),
  });

  const startAgent = useCallback(
    (goal, options) => {
      if (!llmConfigured) {
        toast.warning('Choose a model provider and save its API key in Settings first.');
        setActiveTab('settings');
        return;
      }
      agent.start(goal, options);
    },
    [agent, llmConfigured, toast],
  );

  /* Run one saved scenario against the session that is already open, instead
     of through a whole Test Set run. A scenario written as steps is run and
     judged step by step here too, so this is also how a step's expected result
     gets checked while the scenario is still being written. */
  const runCaseHere = useCallback(
    async (item, { envUrl = null, sessionId: given = null } = {}) => {
      const mobile = (item.kind || 'web') === 'mobile';
      // A phone the caller has already booked for this — Test Sets connects
      // the device picked in its Run panel before it gets here, the same way
      // pressing Run on the whole set does.
      let sessionId = given || activeSessionId;
      let kind = given ? 'mobile' : activeSession?.device?.kind;

      if (!sessionId) {
        /* Pressing run *is* the instruction to connect. Refusing with
           "connect a browser or device first" asked for a step the tester had
           already taken: the scenario carries the address it was written
           against and the environment is picked beside it. A phone cannot be
           conjured, but it can be booked, and the caller does that — so the
           only case left here is a mobile scenario run from somewhere with no
           picker at all. */
        if (mobile) {
          toast.warning('Pick the phone to run on, or open one in Mobile.');
          return;
        }
        const target = applyEnvironment(item.url, envUrl);
        if (!target) {
          toast.warning('This scenario has no address. Pick an environment beside '
            + 'it, or give the scenario a URL.');
          return;
        }
        // Headed, because the sites these scenarios run against refuse a
        // headless browser at the network layer.
        const opened = await openWebPage(target, 'desktop', false);
        if (!opened) return;
        sessionId = opened.sessionId;
        kind = 'web';
      }

      // The workspace is where the run can actually be watched.
      setActiveTab(kind === 'web' ? 'web' : 'mobile');
      setAgentSubTab('chat');
      startAgent(item.goal, { steps: item.steps || null, on: sessionId });
    },
    [activeSessionId, activeSession, openWebPage, startAgent, toast],
  );

  // ----------------------------------------------------------------- replay -
  const replayAbort = useRef(null);
  const replayRun = useCallback(
    async (run) => {
      if (!activeSessionId) {
        toast.warning('Connect a device before replaying a run.');
        return;
      }
      replayAbort.current?.abort();
      const controller = new AbortController();
      replayAbort.current = controller;

      toast.info(`Replaying “${run.title}”…`);
      let failed = false;
      try {
        await api.replayRun(
          run.id,
          activeSessionId,
          (event) => {
            if (event.event === 'step_finished' && event.status === 'failed') {
              failed = true;
              toast.error(`Step ${event.step} failed: ${event.message}`);
            }
            if (event.event === 'finished') {
              if (!failed) toast.success('Replay passed.');
            }
          },
          controller.signal,
        );
      } catch (err) {
        if (err.name !== 'AbortError') toast.error(err.message);
      } finally {
        replayAbort.current = null;
      }
    },
    [activeSessionId, toast],
  );

  useEffect(() => () => replayAbort.current?.abort(), []);

  // ---------------------------------------------------------------- overlay -
  const highlight = useMemo(() => {
    const node = hoveredElement || selectedElement;
    if (!node) return null;
    const box = parseBounds(node.bounds, screen);
    return box ? { ...box, color: roleColor(node.role) } : null;
  }, [hoveredElement, selectedElement, screen]);

  const handleElementPicked = useCallback(
    (element) => {
      setSelectedElement(element);
      setAgentSubTab('inspector');
      if (!tree) refreshTree();
    },
    [tree, refreshTree],
  );

  const loadDeviceSuggestions = useCallback(async () => {
    if (!activeSessionId) {
      setDeviceSuggestions([]);
      setDevicePage(null);
      return;
    }
    setSuggestionsLoading(true);
    try {
      const data = await api.suggestions(activeSessionId);
      setDeviceSuggestions(data.suggestions || []);
      setDevicePage(data.page || null);
    } catch {
      // The composer works without them; a failure here is not worth a toast.
      setDeviceSuggestions([]);
      setDevicePage(null);
    } finally {
      setSuggestionsLoading(false);
    }
  }, [activeSessionId]);

  useEffect(() => {
    if (agentSubTab !== 'chat') return undefined;
    let cancelled = false;
    (async () => {
      if (!cancelled) await loadDeviceSuggestions();
    })();
    return () => { cancelled = true; };
  }, [agentSubTab, agent.status, loadDeviceSuggestions]);

  const openRunReport = useCallback((runId) => {
    setSelectedRunId(runId);
    setActiveTab('runs');
  }, []);

  const openExecution = useCallback((suiteRunId) => {
    setFocusExecutionId(suiteRunId);
    setActiveTab('executions');
  }, []);

  /* Starting a run lands on the workspace, watching it. A suite drives browsers
     of its own, so before this the tester was sent to a list of status chips
     and had no way to see the thing they had just started. A mobile suite runs
     on the phone already connected, which the Mobile workspace is already
     mirroring — there is nothing extra to attach to, just somewhere to be. */
  const watchExecution = useCallback((suiteRunId, kind = 'web') => {
    if (kind === 'mobile') {
      setActiveTab('mobile');
      return;
    }
    setWatchExecutionId(suiteRunId);
    setWatchSessionId(null);
    setWatchSeen(false);
    setWatchProgress(null);
    setActiveTab('web');
  }, []);

  const stopWatching = useCallback(() => {
    setWatchExecutionId(null);
    setWatchSessionId(null);
  }, []);

  /* Stops the run itself, as opposed to stopping watching it. Both belong in
     the watch header: someone sitting in front of a run going wrong wants to
     end it there, not go and find the execution first. */
  const stopExecution = useCallback(async (suiteRunId) => {
    try {
      const data = await api.cancelSuiteRun(suiteRunId);
      toast.info(data.live
        ? 'Stopping — the scenarios still running are finishing their current step.'
        : 'Marked stopped. The run was no longer in flight on the server.');
    } catch (err) {
      toast.error(err.message);
    }
  }, [toast]);

  // Stable, so ExecutionsPage's load callback keeps its identity and the list
  // is not refetched on every unrelated re-render of App.
  const clearExecutionFocus = useCallback(() => setFocusExecutionId(null), []);

  const needsKey = useCallback(() => {
    toast.warning('Choose a model provider and save its API key in Settings first.');
    setActiveTab('settings');
  }, [toast]);

  // ------------------------------------------------------------------ views -
  const renderMain = () => {
    if (activeTab === 'runs') {
      return (
        <RunsPage
          activeSessionId={activeSessionId}
          onReplay={replayRun}
          selectedRunId={selectedRunId}
          onSelectRun={setSelectedRunId}
        />
      );
    }

    if (activeTab === 'executions') {
      return (
        <ExecutionsPage
          onOpenRun={openRunReport}
          onWatch={watchExecution}
          // A bug raised from a mobile scenario has to land on the Mobile tab.
          // Bug Report opens on Web like every other page, so without this the
          // tester is sent to a list the bug they just raised is not in.
          onOpenBugs={(kind) => { setBugsPlatform(kind || null); setActiveTab('bugs'); }}
          focusId={focusExecutionId}
          onFocused={clearExecutionFocus}
        />
      );
    }

    if (activeTab === 'suites') {
      return (
        <SuitesPage
          onRunHere={runCaseHere}
          onOpenExecution={openExecution}
          onWatchExecution={watchExecution}
          sessions={mobileSessions}
          onConnectDevice={connectDevice}
        />
      );
    }

    if (activeTab === 'test-data') {
      return <TestDataPage />;
    }

    if (activeTab === 'insights') {
      return <InsightsPage />;
    }

    if (activeTab === 'bugs') {
      return <BugsPage onOpenRun={openRunReport} initialPlatform={bugsPlatform} />;
    }

    if (activeTab === 'settings') {
      return <SettingsPage appiumStatus={appiumStatus} onRefreshHealth={refreshHealth} />;
    }

    if (activeTab === 'web') {
      return (
        <WebWorkspace
          session={watchExecutionId ? watchSession : webSession}
          agent={agent}
          watch={watchExecutionId ? {
            name: watchName,
            sessions: watchList,
            // Empty only after at least one browser has been seen: before that
            // the run is still opening its first one, which reads very
            // differently to the tester.
            finished: watchList.length === 0 && watchSeen,
            progress: watchProgress,
            onPick: setWatchSessionId,
            onOpenExecution: () => openExecution(watchExecutionId),
            onStop: () => stopExecution(watchExecutionId),
            onExit: stopWatching,
          } : null}
          onOpenExecution={openExecution}
          onOpen={openWebPage}
          onNavigate={navigateWeb}
          onClose={() => webSession && disconnectDevice(webSession.sessionId)}
          llmConfigured={llmConfigured}
          onNeedsKey={needsKey}
          onOpenRun={openRunReport}
        />
      );
    }

    // Mobile workspace: devices and the agent in one place.
    if (!mobileSession) {
      return (
        <StudioPage
          sessions={mobileSessions}
          activeSessionId={activeSessionId}
          onConnect={connectDevice}
          onDisconnect={disconnectDevice}
          onSelectSession={setActiveSessionId}
          appiumRunning={appiumStatus === 'running'}
          onOpenSettings={() => setActiveTab('settings')}
        />
      );
    }

    return (
      <main className="page page-flush">
        <header className="page-header">
          <div>
            <h1 className="page-title">Mobile</h1>
            <p className="page-subtitle">
              {mobileSession.device.name} · {mobileSession.device.platform}
            </p>
          </div>
          <div className="web-header-actions">
            <div className="segmented">
              <button className={agentSubTab === 'chat' ? 'active' : ''} onClick={() => setAgentSubTab('chat')}>
                Run
              </button>
              <button
                className={agentSubTab === 'inspector' ? 'active' : ''}
                onClick={() => {
                  setAgentSubTab('inspector');
                  refreshTree();
                }}
              >
                Inspector
              </button>
            </div>
            <button className="btn btn-danger btn-sm" onClick={() => disconnectDevice(mobileSession.sessionId)}>
              Disconnect
            </button>
          </div>
        </header>

        {agentSubTab === 'chat' ? (
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
            onStart={startAgent}
            onWriteScenarios={(b) => setWriteBrief(b || '')}
            onStop={agent.stop}
            onReset={agent.reset}
            onOpenRun={openRunReport}
            examples={deviceSuggestions}
            pageSummary={devicePage}
            suggestionsLoading={suggestionsLoading}
          />
        ) : (
          <Inspector
            sessionId={activeSessionId}
            tree={tree}
            snapshotId={snapshotId}
            loading={treeLoading}
            selected={selectedElement}
            onSelect={setSelectedElement}
            onHover={setHoveredElement}
            onLeave={() => setHoveredElement(null)}
            onRefresh={refreshTree}
          />
        )}
      </main>
    );
  };

  return (
    <div className={`app-shell ${showMirror ? '' : 'no-mirror'}`}>
      <Sidebar
        activeTab={activeTab}
        onTabChange={(tab) => {
          setActiveTab(tab);
          if (tab === 'runs') setSelectedRunId(null);
          // Reaching Bug Report from the nav is not following a bug there, so
          // it opens on the default platform rather than on wherever the last
          // raised bug happened to be.
          if (tab === 'bugs') setBugsPlatform(null);
        }}
        theme={theme}
        onToggleTheme={toggleTheme}
        connectedDevice={activeSession?.device}
        appiumStatus={appiumStatus}
      />

      {renderMain()}

      {/* The phone bezel belongs to the mobile workspace only; the web page
          renders full width inside its own workspace instead. */}
      {showMirror && (
        <DeviceMirror
          sessionId={activeSessionId}
          device={mobileSession?.device}
          screenshot={screenshot}
          connection={connection}
          screen={screen}
          fps={fps}
          onFpsChange={setFps}
          highlight={agentSubTab === 'inspector' ? highlight : null}
          onElementPicked={handleElementPicked}
          onScreenChanged={refreshTree}
          interactive={agent.status !== 'running'}
        />
      )}

      {writeBrief !== null && (
        <ScenarioReviewModal
          sessionId={activeSessionId}
          brief={writeBrief}
          kind="mobile"
          os={activeSession ? osOf(activeSession.device) : null}
          onOpenExecution={openExecution}
          onClose={() => setWriteBrief(null)}
        />
      )}

      {/* Scenarios the agent wrote from a plain chat request arrive here for
          review instead of being filed on their own. */}
      {agent.proposed && (
        <ScenarioReviewModal
          sessionId={activeSessionId}
          scenarios={agent.proposed.scenarios}
          readFrom={agent.proposed.readFrom}
          suggestedName={agent.proposed.suggestedName}
          kind={agent.proposed.kind || 'mobile'}
          os={activeSession ? osOf(activeSession.device) : null}
          onOpenExecution={openExecution}
          onClose={agent.clearProposed}
        />
      )}
    </div>
  );
}
