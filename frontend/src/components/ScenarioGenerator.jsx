import { useEffect, useMemo, useState } from 'react';
import {
  Check, ChevronDown, ChevronRight, FileText, FileUp, Globe, HelpCircle,
  Link as LinkIcon,
  Loader2, Play, Save, Sparkles, Smartphone, X,
} from 'lucide-react';

import { api } from '../api';
import { StepEditor } from './StepEditor';
import { DEFAULT_ENV_URL, ENV_GROUPS } from '../lib/environments';
import { forPicking, sessionsFor } from '../lib/platforms';
import { useToast } from '../hooks/useToast';

/**
 * Scenarios written to the Digital Channels standard, reviewed and edited before
 * they land.
 *
 * Nothing saves automatically — generated scenarios are proposals. The reviewer
 * (who knows the app better than the model) can rewrite the title, the goal and
 * every step and its expected result, tick the ones to keep, name the set they
 * go into, and — if they want it run now — name the execution and start it.
 */

const PRIORITIES = ['Critical', 'High', 'Medium', 'Low'];
const NEW_SET = '__new__';

export function ScenarioGenerator({
  suiteId = null, sessionId = null, defaultBrief = '', autoGenerate = false, onAdded,
  // Scenarios already written elsewhere (the agent, from a chat request) and
  // handed here for review — shown at once, all ticked, no generation needed.
  initialScenarios = null, initialReadFrom = null, defaultSetName = '',
  // Platform of the session the scenarios were read from; a new set created
  // here is filed under it, so a mobile session never produces a "web" set.
  // `os` narrows that for mobile — a set that does not say which phone lands
  // on neither sub-tab, and the scenarios just written look lost.
  kind = 'web', os = null,
  // How to book a phone from here. Without it the mobile side is a dead end:
  // scenarios are written by reading a screen, and the tester was sent to
  // another page to arrange one.
  onConnectDevice = null,
  // Open the execution "Save & run" just started. Absent where the caller has
  // no way to switch tabs, in which case the toast says where to find it.
  onOpenExecution = null,
}) {
  const toast = useToast();
  const seeded = Array.isArray(initialScenarios) && initialScenarios.length > 0;
  const [brief, setBrief] = useState(defaultBrief);
  /* One of the environments the Web workspace lists, not free text. These hosts
     differ by a single token and a mistyped one reads a page that still looks
     plausible, so scenarios come back written against the wrong stack. */
  const [url, setUrl] = useState(DEFAULT_ENV_URL);
  const [busy, setBusy] = useState(false);
  const [scenarios, setScenarios] = useState(() => (seeded ? initialScenarios : []));
  const [rejected, setRejected] = useState([]);
  const [questions, setQuestions] = useState([]);
  const [answers, setAnswers] = useState('');
  const [readFrom, setReadFrom] = useState(seeded ? initialReadFrom : null);
  const [chosen, setChosen] = useState(
    () => new Set(seeded ? initialScenarios.map((_, i) => i) : []),
  );
  const [expanded, setExpanded] = useState(() => new Set());

  const [sessions, setSessions] = useState([]);
  const isMobile = kind === 'mobile';
  /* A page is something you open at an address; an app is something already
     running on a phone. Offering "Open a URL…" under the Mobile tab asked for
     an address there is nothing to type into — and the sessions beside it were
     every open session, so the Android tab offered an iPhone. Both lists are
     cut to the tab now: web pages on Web, and on Mobile only the phones on the
     OS being looked at. */
  const [source, setSource] = useState(
    sessionId ? 'session' : (isMobile ? 'brief' : 'url'),
  );

  /* An analysis document, read into text and shown as what it is rather than
     dropped into the brief field — a page of acceptance criteria would bury
     the module name the tester typed beside it. Both are sent: the name says
     which part of the product, the document says what it has to do. */
  const [analysis, setAnalysis] = useState(null);
  const [reading, setReading] = useState(false);

  // A Jira story or a Confluence page, read into the same place a document
  // goes: they are the same thing to the generator — the requirements, already
  // written down somewhere that is not here.
  const [link, setLink] = useState('');

  const readLink = async () => {
    if (!link.trim()) return;
    setReading(true);
    try {
      const data = await api.readTrackerLink(link.trim());
      setAnalysis({ ...data, name: `${data.title} (${data.service})` });
      if (data.note) toast.info(data.note);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setReading(false);
    }
  };

  const readDocument = async (file) => {
    if (!file) return;
    setReading(true);
    try {
      const data = await api.readScenarioDocument(file);
      setAnalysis(data);
      if (data.note) toast.info(data.note);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setReading(false);
    }
  };

  // What actually reaches the model: the module name and the document, in that
  // order, so the first line still says what this is about.
  const fullBrief = [brief.trim(), analysis?.text].filter(Boolean).join('\n\n');

  // Generating from a workspace means no Test Set is in scope yet, so one has
  // to be chosen — or named and created — before the scenarios have anywhere to go.
  const [suites, setSuites] = useState([]);
  const [targetSuite, setTargetSuite] = useState(suiteId || NEW_SET);
  const [newSetName, setNewSetName] = useState(defaultSetName || '');
  // The module a new set files under ("Uçuş Arama"), so sets for one-way,
  // round-trip and so on group together instead of piling up in one list.
  const [newSetModule, setNewSetModule] = useState('');
  const [execName, setExecName] = useState('');
  const needsTarget = !suiteId;

  useEffect(() => {
    if (sessionId) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const data = await api.sessions();
        if (!cancelled) setSessions(data.sessions || []);
      } catch {
        if (!cancelled) setSessions([]);
      }
    })();
    return () => { cancelled = true; };
  }, [sessionId]);

  const onThisTab = useMemo(
    () => sessionsFor(sessions, kind, isMobile ? os : null),
    [sessions, kind, isMobile, os],
  );

  /* The phones that could be opened, for the case where none is.
     Listed rather than counted: saying "63 can be booked — open one in
     Mobile" is a dead end in the middle of the one screen where scenarios get
     written. On Web the tester picks an environment here and it opens; this
     is the same gesture, one platform over. */
  const [devices, setDevices] = useState([]);
  const [pickedUdid, setPickedUdid] = useState('');
  const [pickedApp, setPickedApp] = useState('');
  const [builds, setBuilds] = useState([]);
  const [opening, setOpening] = useState(false);

  // Phones first, newest first. The catalogue comes back in no order at
  // all, so the first thing the list offered was an iPad from 2022.
  const bookable = useMemo(() => forPicking(devices, os), [devices, os]);

  useEffect(() => {
    if (sessionId || !isMobile) return undefined;
    let cancelled = false;
    (async () => {
      const found = [];
      for (const load of [api.devices, api.browserstackDevices]) {
        try {
          const data = await load();
          found.push(...(data.devices || []));
        } catch {
          /* one source being unavailable must not hide the other */
        }
      }
      if (!cancelled) setDevices(found);
    })();
    return () => { cancelled = true; };
  }, [sessionId, isMobile]);

  // What is installed on the phone about to be opened. A cloud device has
  // nothing on it until a build is named, and will not start a session
  // without one.
  useEffect(() => {
    if (!pickedUdid) return undefined;
    const device = devices.find((item) => item.udid === pickedUdid);
    if (!device) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const data = await api.deviceApps(device.udid, device.platform);
        if (!cancelled) setBuilds(data.environments || []);
      } catch {
        if (!cancelled) setBuilds([]);
      }
    })();
    return () => { cancelled = true; };
  }, [pickedUdid, devices]);

  /** Book the chosen phone and write the scenarios from its own screen. */
  const openDevice = async () => {
    const device = bookable.find((item) => item.udid === pickedUdid);
    if (!device || !onConnectDevice) return;
    setOpening(true);
    try {
      const session = await onConnectDevice(device, pickedApp || null);
      if (!session) return;
      // Added here rather than waited for: the list this component holds is
      // its own, and the tester pressed open to use it now.
      setSessions((current) => [...current, session]);
      setSource(session.sessionId);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setOpening(false);
    }
  };

  /* Derived rather than synced: switching from iOS to Android would otherwise
     leave the iPhone selected — gone from the list, still in the state, and
     still the phone the next Generate would read. Falling through to what is
     on offer keeps the control and the choice saying the same thing. */
  const chosenSource = (() => {
    if (sessionId) return 'session';
    if (source === 'brief') return 'brief';
    if (onThisTab.some((item) => item.sessionId === source)) return source;
    if (onThisTab.length) return onThisTab[0].sessionId;
    return isMobile ? 'brief' : 'url';
  })();

  useEffect(() => {
    if (!needsTarget) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const data = await api.suites();
        if (cancelled) return;
        setSuites(data.suites);
        // A fresh request means a fresh set: stay on "new" so each batch gets
        // its own set (named from the brief) instead of quietly landing in
        // whichever existing set is listed first. The tester can still pick an
        // existing set from the dropdown.
        setTargetSuite((current) => current || NEW_SET);
      } catch {
        if (!cancelled) setSuites([]);
      }
    })();
    return () => { cancelled = true; };
  }, [needsTarget]);

  const generate = async (withAnswers = '') => {
    setBusy(true);
    try {
      const liveSession = sessionId
        || (chosenSource !== 'url' && chosenSource !== 'brief' ? chosenSource : null);
      const body = {
        brief: fullBrief || null,
        url: liveSession ? null : (chosenSource === 'url' ? url.trim() || null : null),
        answers: withAnswers.trim() || null,
      };
      const data = liveSession
        ? await api.generateScenariosFromScreen(liveSession, body)
        : await api.generateScenarios(body);

      setQuestions(data.questions || []);
      setScenarios(data.scenarios);
      setRejected(data.rejected || []);
      setReadFrom(data.readFrom || null);
      // Name the new set from the brief unless the tester already typed one.
      if (data.suggestedName) {
        setNewSetName((current) => current.trim() || data.suggestedName);
      }
      setChosen(new Set(data.scenarios.map((_, index) => index)));
      setExpanded(new Set());
      if (!data.scenarios.length && !(data.questions || []).length) {
        toast.error('No scenario came back in the required format.');
      }
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };

  const answerAndRetry = () => {
    if (!answers.trim()) return;
    setQuestions([]);
    generate(answers);
  };

  // Opened from the chat with a brief already typed: generate straight away so
  // the reviewer lands on scenarios to check, not an empty form. Deferred out of
  // the commit so the first setState does not run inside the effect.
  useEffect(() => {
    if (!autoGenerate || seeded) return undefined;
    const t = setTimeout(() => generate(), 0);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const patchScenario = (index, patch) => {
    setScenarios((list) => list.map((s, i) => (i === index ? { ...s, ...patch } : s)));
  };

  const toggle = (index) => {
    setChosen((set) => {
      const next = new Set(set);
      if (next.has(index)) next.delete(index); else next.add(index);
      return next;
    });
  };

  const toggleExpanded = (index) => {
    setExpanded((set) => {
      const next = new Set(set);
      if (next.has(index)) next.delete(index); else next.add(index);
      return next;
    });
  };

  /** Persist the ticked scenarios; returns the created cases (with ids) so the
   *  caller can run them, or null if nothing was saved. */
  const persist = async () => {
    const picked = scenarios.filter((_, index) => chosen.has(index));
    if (!picked.length) {
      toast.warning('Tick at least one scenario to save.');
      return null;
    }

    let destId = suiteId || targetSuite;
    if (!suiteId && targetSuite === NEW_SET) {
      if (!newSetName.trim()) {
        toast.warning('A Test Set name is required — name the set these go into.');
        return null;
      }
      const created = await api.createSuite({
        name: newSetName.trim(),
        kind,
        // A web set has no OS; carrying one over from a phone that happened to
        // be open would file it under a sub-tab it does not belong on.
        os: isMobile ? os : null,
        module: newSetModule.trim() || null,
      });
      destId = created.id;
    }
    if (!destId) {
      toast.warning('Pick or name a Test Set first.');
      return null;
    }

    const result = await api.addCases(destId, picked.map((s) => ({
      name: s.title,
      goal: s.goal,
      url: readFrom || null,
      priority: s.priority,
      layer: s.layer,
      scenarioType: s.type,
      precondition: s.precondition || null,
      steps: s.steps || [],
      tags: [],
    })));
    return { suiteId: destId, cases: result.cases || [], count: picked.length };
  };

  const clearAfterSave = () => {
    setScenarios([]);
    setChosen(new Set());
    setExpanded(new Set());
    setNewSetName('');
    setNewSetModule('');
    setExecName('');
    onAdded?.();
  };

  const save = async () => {
    setBusy(true);
    try {
      const saved = await persist();
      if (!saved) return;
      toast.success(`${saved.count} scenario${saved.count > 1 ? 's' : ''} saved.`);
      clearAfterSave();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };

  const saveAndRun = async () => {
    // Named before anything is saved: an execution with no name is one more
    // "Test Set" in the list that nobody can tell from the others.
    if (!execName.trim()) {
      toast.warning('An execution name is required to run these.');
      return;
    }
    setBusy(true);
    try {
      const saved = await persist();
      if (!saved) return;
      const caseIds = saved.cases.map((c) => c.id).filter(Boolean);
      if (!caseIds.length) {
        toast.error('Scenarios saved, but no ids came back to run them.');
        clearAfterSave();
        return;
      }
      // Started on the server, not streamed: this dialog closes straight after,
      // and a streamed run dies with the connection — which is what left
      // executions sitting at "running" with nothing in them. Headed, because
      // the sites these scenarios are written against refuse a headless browser.
      const { suiteRunId } = await api.startExecution({
        caseIds, name: execName.trim(), workers: 1, headless: false,
      });
      toast.success(
        suiteRunId && onOpenExecution
          ? `“${execName.trim()}” started — opening it.`
          : `“${execName.trim()}” started — follow it in Test Executions.`,
      );
      clearAfterSave();
      if (suiteRunId && onOpenExecution) onOpenExecution(suiteRunId);
    } catch (err) {
      toast.error(`Execution could not start: ${err.message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="scenario-generator">
      <div className="generator-fields">
        <input
          className="generator-brief"
          type="text"
          value={brief}
          onChange={(event) => setBrief(event.target.value)}
          placeholder={sessionId
            ? 'Module or what to cover — e.g. “Uçuş Arama”'
            : 'Module or what to cover — e.g. “Uçuş Arama”, “Booking - Payment”'}
          disabled={busy}
        />

        {/* The requirements usually exist already. Reading the file is worth a
            button of its own beside the module name, not a line of help text
            under it. */}
        {/* The same requirements, wherever the team keeps them. Enter submits
            so a pasted link needs no second gesture. */}
        <div className="generator-link">
          <LinkIcon size={13} />
          <input
            type="text"
            value={link}
            onChange={(event) => setLink(event.target.value)}
            onKeyDown={(event) => { if (event.key === 'Enter') readLink(); }}
            placeholder="Jira or Confluence link"
            disabled={busy || reading}
          />
          <button
            className="btn btn-ghost btn-sm"
            onClick={readLink}
            disabled={busy || reading || !link.trim()}
          >
            Read
          </button>
        </div>

        <label className={`generator-doc ${reading ? 'busy' : ''}`}>
          {reading ? <Loader2 size={14} className="spin" /> : <FileUp size={14} />}
          {reading ? 'Reading…' : 'Analysis document'}
          <input
            type="file"
            accept=".txt,.md,.markdown,.csv,.json,.docx,.pdf"
            disabled={busy || reading}
            onChange={(event) => {
              readDocument(event.target.files?.[0]);
              // Cleared so choosing the same file again still fires a change.
              event.target.value = '';
            }}
          />
        </label>
        {!sessionId && (
          <select
            className="generator-source"
            value={chosenSource}
            onChange={(event) => setSource(event.target.value)}
            disabled={busy}
            aria-label="What to read the scenarios from"
          >
            {onThisTab.length > 0 && (
              <optgroup label={isMobile ? 'Connected device' : 'Open page'}>
                {onThisTab.map((item) => (
                  <option key={item.sessionId} value={item.sessionId}>
                    {isMobile ? '📱' : '🌐'} {item.device.name}
                  </option>
                ))}
              </optgroup>
            )}
            {/* There is no address to open on a phone: an app is read off a
                device that is already running it. */}
            {!isMobile && <option value="url">🌐 Open an environment…</option>}
            <option value="brief">✎ Brief only</option>
          </select>
        )}
        {!sessionId && !isMobile && chosenSource === 'url' && (
          <div className="generator-url">
            <Globe size={13} />
            <select
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              disabled={busy}
              aria-label="Environment to read"
            >
              {ENV_GROUPS.map((group) => (
                <optgroup key={group.label} label={group.label}>
                  {group.items.map((item) => (
                    <option key={item.name} value={item.url} title={item.url}>
                      {item.name} · {new URL(item.url).host}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>
        )}
        {/* Pick a phone and open it, here. Scenarios are written by reading a
            screen, so one has to be running — and sending the tester to
            another page to arrange that, in the middle of the page where
            scenarios get written, is the dead end this replaces. */}
        {!sessionId && isMobile && !onThisTab.length && (
          bookable.length ? (
            <div className="generator-device">
              <Smartphone size={13} />
              <select
                value={pickedUdid}
                onChange={(event) => {
                  setPickedUdid(event.target.value);
                  setPickedApp('');
                  setBuilds([]);
                }}
                disabled={busy || opening}
                aria-label="Device to open"
              >
                <option value="">
                  {`Pick an ${os === 'android' ? 'Android' : 'iOS'} device…`}
                </option>
                {bookable.map((device) => (
                  <option key={device.udid} value={device.udid}>
                    {device.name} · {device.platform}
                    {device.source === 'browserstack' ? ' · cloud' : ''}
                  </option>
                ))}
              </select>
              <select
                value={pickedApp}
                onChange={(event) => setPickedApp(event.target.value)}
                disabled={busy || opening || !pickedUdid}
                aria-label="Build to open on it"
              >
                <option value="">
                  {!pickedUdid ? 'Build' : builds.length ? 'Whatever is open' : 'No TK build'}
                </option>
                {builds.map((build) => (
                  <option key={build.id || build.name} value={build.id}>
                    {build.label || build.name}
                  </option>
                ))}
              </select>
              <button
                className="btn btn-sm"
                onClick={openDevice}
                disabled={busy || opening || !pickedUdid || !onConnectDevice}
              >
                {opening ? <Loader2 size={13} className="spin" /> : null}
                {opening ? 'Opening…' : 'Open'}
              </button>
            </div>
          ) : (
            <span className="generator-nodevice">
              <Smartphone size={13} />
              {`No ${os === 'android' ? 'Android' : 'iOS'} device found. `}
              Connect one, or add a BrowserStack account in Settings.
            </span>
          )
        )}
        <button className="btn btn-primary" onClick={() => generate()} disabled={busy}>
          {busy ? <Loader2 size={15} className="spin" /> : <Sparkles size={15} />}
          {busy ? 'Writing…' : 'Generate'}
        </button>
      </div>

      {/* What was read, in the tester's hands rather than the model's alone:
          the size says whether the whole document came through, and the first
          lines say whether it was the right one. */}
      {analysis && (
        <div className="generator-doc-read">
          <FileText size={14} />
          <span className="doc-name">{analysis.name}</span>
          <span className="muted small">
            {analysis.characters.toLocaleString()} characters
            {analysis.note ? ` · ` : ''}
          </span>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setAnalysis(null)}
            disabled={busy}
          >
            <X size={13} /> Remove
          </button>
          <pre className="doc-preview">{analysis.text.slice(0, 700)}</pre>
        </div>
      )}

      <p className="generator-hint">
        {sessionId || (chosenSource !== 'url' && chosenSource !== 'brief')
          ? 'Read from the device or page you have open, in the state it is in now.'
          : chosenSource === 'url'
            ? 'The page is opened and read, so scenarios name real fields and buttons.'
            : 'Written from the brief alone. Point it at a device or page for real fields.'}
        {' '}Every scenario comes back with steps to review and edit before it is saved.
      </p>

      {questions.length > 0 && (
        <div className="generator-questions">
          <p className="questions-lead">
            <HelpCircle size={14} /> Bunlar netleşmeden senaryo yazmak, sonradan
            baştan yazılacak senaryolar üretmek olurdu:
          </p>
          <ul>
            {questions.map((question, index) => <li key={index}>{question}</li>)}
          </ul>
          <div className="questions-answer">
            <input
              type="text"
              value={answers}
              onChange={(event) => setAnswers(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && answerAndRetry()}
              placeholder="Cevabınız — örn. “INT OW, 1 ADT, Klarna [USD]”"
              disabled={busy}
            />
            <button className="btn btn-primary btn-sm" onClick={answerAndRetry} disabled={busy || !answers.trim()}>
              Continue
            </button>
          </div>
        </div>
      )}

      {readFrom && scenarios.length > 0 && (
        <p className="generator-source">
          <Globe size={12} /> Read from <code>{readFrom}</code>
        </p>
      )}

      {rejected.length > 0 && (
        <p className="generator-rejected">
          {rejected.length} dropped for breaking the format: {rejected.map((r) => `“${r}”`).join(', ')}
        </p>
      )}

      {scenarios.length > 0 && (
        <>
          <div className="generator-summary">
            <strong>{scenarios.length}</strong> scenarios · {chosen.size} selected · review and edit before saving
          </div>
          <ul className="generated-list">
            {scenarios.map((scenario, index) => {
              const isOpen = expanded.has(index);
              const stepCount = scenario.steps?.length || 0;
              return (
                <li key={index} className={chosen.has(index) ? 'picked' : ''}>
                  <div className="generated-row">
                    <button
                      className="pick-box"
                      onClick={() => toggle(index)}
                      aria-label={chosen.has(index) ? 'Do not add this scenario' : 'Add this scenario'}
                    >
                      {chosen.has(index) ? <Check size={13} /> : <X size={13} />}
                    </button>
                    <div className="generated-body">
                      <input
                        className="generated-title-input"
                        value={scenario.title}
                        onChange={(e) => patchScenario(index, { title: e.target.value })}
                        placeholder="Scenario title"
                        disabled={busy}
                      />
                      <input
                        className="generated-goal-input"
                        value={scenario.goal}
                        onChange={(e) => patchScenario(index, { goal: e.target.value })}
                        placeholder="Goal — what the agent should do"
                        disabled={busy}
                      />
                      {scenario.rationale && (
                        <div className="generated-why">{scenario.rationale}</div>
                      )}
                      <button
                        type="button"
                        className="steps-toggle"
                        onClick={() => toggleExpanded(index)}
                      >
                        {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                        {stepCount ? `${stepCount} step${stepCount > 1 ? 's' : ''}` : 'No steps yet'} — {isOpen ? 'hide' : 'review & edit'}
                      </button>
                      {isOpen && (
                        <div className="generated-steps-editor">
                          <StepEditor
                            steps={scenario.steps || []}
                            onChange={(steps) => patchScenario(index, { steps })}
                            disabled={busy}
                          />
                        </div>
                      )}
                    </div>
                    <span className="layer-tag">{scenario.layer}</span>
                    {/* Editable: the generator reads the kind off what it wrote,
                        and the reviewer who knows the rules has the last word. */}
                    <select
                      className={`type-select t-${(scenario.type || 'Positive').toLowerCase()}`}
                      value={scenario.type || 'Positive'}
                      onChange={(event) => patchScenario(index, { type: event.target.value })}
                      aria-label="Scenario type"
                    >
                      {['Positive', 'Negative', 'Boundary'].map((value) => (
                        <option key={value} value={value}>{value}</option>
                      ))}
                    </select>
                    <select
                      className={`priority-select p-${scenario.priority.toLowerCase()}`}
                      value={scenario.priority}
                      onChange={(event) => patchScenario(index, { priority: event.target.value })}
                      title="The generator proposes a priority from the standard; you have the last word."
                    >
                      {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
                    </select>
                  </div>
                </li>
              );
            })}
          </ul>

          {needsTarget && (
            <div className="generator-target">
              <label>Save into</label>
              <select
                value={targetSuite || NEW_SET}
                onChange={(e) => setTargetSuite(e.target.value)}
                disabled={busy}
                aria-label="Test Set to file the scenarios into"
              >
                {suites.map((suite) => (
                  <option key={suite.id} value={suite.id}>{suite.name}</option>
                ))}
                <option value={NEW_SET}>＋ New Test Set…</option>
              </select>
              {targetSuite === NEW_SET && (
                <>
                  <input
                    type="text"
                    className="generator-newset"
                    value={newSetName}
                    onChange={(e) => setNewSetName(e.target.value)}
                    placeholder="Test Set name (required) — e.g. Tek yön uçuş ara"
                    disabled={busy}
                  />
                  <input
                    type="text"
                    className="generator-newmodule"
                    value={newSetModule}
                    onChange={(e) => setNewSetModule(e.target.value)}
                    placeholder="Module (optional) — e.g. Uçuş Arama"
                    title="Sets sharing a module are grouped together in Test Sets"
                    disabled={busy}
                  />
                </>
              )}
            </div>
          )}

          <div className="generator-actions">
            <button className="btn btn-primary" onClick={save} disabled={busy || !chosen.size}>
              <Save size={14} /> Save {chosen.size} to Test Set
            </button>
            <div className="generator-run">
              <input
                type="text"
                className="generator-execname"
                value={execName}
                onChange={(e) => setExecName(e.target.value)}
                placeholder="Execution name (required to run)"
                disabled={busy}
              />
              <button className="btn btn-accent" onClick={saveAndRun} disabled={busy || !chosen.size}>
                <Play size={14} /> Save & run
              </button>
            </div>
            <button className="btn btn-ghost" onClick={() => setScenarios([])} disabled={busy}>
              Discard
            </button>
          </div>
        </>
      )}
    </div>
  );
}
