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
import { ExecutionsPage } from './pages/ExecutionsPage';
import { InsightsPage } from './pages/InsightsPage';
import { RunsPage } from './pages/RunsPage';
import { SettingsPage } from './pages/SettingsPage';
import { StudioPage } from './pages/StudioPage';
import { SuitesPage } from './pages/SuitesPage';
import { WebWorkspace } from './pages/WebWorkspace';
import './App.css';

export default function App() {
  const toast = useToast();
  const { theme, toggle: toggleTheme } = useTheme();

  const [activeTab, setActiveTab] = useState('web');
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
  // A brief from the composer's "write scenarios" mode, awaiting review in a
  // dialog. null = closed.
  const [writeBrief, setWriteBrief] = useState(null);

  const activeSession = useMemo(
    () => sessions.find((session) => session.sessionId === activeSessionId) || null,
    [sessions, activeSessionId],
  );

  // Each workspace owns its own kind of session, so switching tabs never shows
  // a phone bezel around a web page or vice versa.
  const mobileSessions = useMemo(
    () => sessions.filter((session) => session.device.kind !== 'web'),
    [sessions],
  );
  const webSession = useMemo(
    () => sessions.find((session) => session.device.kind === 'web') || null,
    [sessions],
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
          setActiveSessionId((current) => current || data.sessions[0].sessionId);
        }
      })
      .catch(() => {
        /* backend not up yet; the status strip shows it */
      });
  }, []);

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
  const connectDevice = useCallback(
    async (device, appId) => {
      try {
        const data = await api.createSession(device, appId);
        const session = { sessionId: data.sessionId, device: data.device };
        setSessions((current) => [...current.filter((s) => s.device.udid !== device.udid), session]);
        setActiveSessionId(data.sessionId);
        setTree(null);
        setSelectedElement(null);
        toast.success(`Connected to ${device.name}.`);
      } catch (err) {
        toast.error(err.message);
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
        // The backend switches to a windowed browser when a site refuses a
        // screenless one. Worth mentioning — it explains the badge and the
        // slower open — but it is not a warning: the page loaded, and there is
        // nothing for the user to do about it.
        if (data.note) toast.info(data.note);
      } catch (err) {
        toast.error(err.message);
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
  const agent = useAgentRun(activeSessionId, {
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
    (item) => {
      if (!activeSessionId) {
        toast.warning('Connect a browser or device first — this runs on the open session.');
        return;
      }
      // The workspace is where the run can actually be watched.
      setActiveTab(activeSession?.device?.kind === 'web' ? 'web' : 'mobile');
      setAgentSubTab('chat');
      startAgent(item.goal, { steps: item.steps || null });
    },
    [activeSessionId, activeSession, startAgent, toast],
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
      return <ExecutionsPage onOpenRun={openRunReport} />;
    }

    if (activeTab === 'suites') {
      return <SuitesPage onOpenRun={openRunReport} onRunHere={runCaseHere} />;
    }

    if (activeTab === 'insights') {
      return <InsightsPage />;
    }

    if (activeTab === 'settings') {
      return <SettingsPage appiumStatus={appiumStatus} onRefreshHealth={refreshHealth} />;
    }

    if (activeTab === 'web') {
      return (
        <WebWorkspace
          session={webSession}
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
          onClose={agent.clearProposed}
        />
      )}
    </div>
  );
}
