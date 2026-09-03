import { useEffect, useState } from 'react';
import { Check, Globe, HelpCircle, Loader2, Sparkles, X } from 'lucide-react';

import { api } from '../api';
import { useToast } from '../hooks/useToast';

/**
 * Scenarios written to the Digital Channels standard, reviewed before they land.
 *
 * Three things shape this screen. Nothing saves automatically — generated
 * scenarios are proposals, and a wrong one that lands unreviewed gets copied by
 * the next person. There is no "how many" field, because the right number is a
 * property of the screen, not of the request. And when the brief is too vague
 * to write against, the model asks rather than inventing scenarios the team
 * would have to rewrite.
 */

const PRIORITIES = ['Critical', 'High', 'Medium', 'Low'];

export function ScenarioGenerator({ suiteId = null, sessionId = null, defaultBrief = '', onAdded }) {
  const toast = useToast();
  const [brief, setBrief] = useState(defaultBrief);
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [scenarios, setScenarios] = useState([]);
  const [rejected, setRejected] = useState([]);
  const [questions, setQuestions] = useState([]);
  const [answers, setAnswers] = useState('');
  const [readFrom, setReadFrom] = useState(null);
  const [chosen, setChosen] = useState(() => new Set());

  // Where to look. A device or page that is already open is the best source
  // there is — it is the real app in its current state — and for a mobile app
  // it is the only one, since there is no URL to re-open.
  const [sessions, setSessions] = useState([]);
  const [source, setSource] = useState(sessionId ? 'session' : 'url');

  // Generating from a workspace means no Test Set is in scope yet, so one has
  // to be chosen before the scenarios have anywhere to go.
  const [suites, setSuites] = useState([]);
  const [targetSuite, setTargetSuite] = useState(suiteId);
  const needsTarget = !suiteId;

  useEffect(() => {
    if (sessionId) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const data = await api.sessions();
        if (cancelled) return;
        setSessions(data.sessions || []);
        // Default to whatever is open: if the tester has the app in front of
        // them, that is what "look at the app" means.
        if ((data.sessions || []).length) setSource(data.sessions[0].sessionId);
      } catch {
        if (!cancelled) setSessions([]);
      }
    })();
    return () => { cancelled = true; };
  }, [sessionId]);

  useEffect(() => {
    if (!needsTarget) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const data = await api.suites();
        if (cancelled) return;
        setSuites(data.suites);
        setTargetSuite((current) => current || data.suites[0]?.id || null);
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
        || (source !== 'url' && source !== 'brief' ? source : null);
      const body = {
        brief: brief.trim() || null,
        url: liveSession ? null : (source === 'url' ? url.trim() || null : null),
        answers: withAnswers.trim() || null,
      };
      const data = liveSession
        ? await api.generateScenariosFromScreen(liveSession, body)
        : await api.generateScenarios(body);

      setQuestions(data.questions || []);
      setScenarios(data.scenarios);
      setRejected(data.rejected || []);
      setReadFrom(data.readFrom || null);
      // Everything that passed the format check starts ticked: the common case
      // is keeping them all, and unticking two is less work than ticking six.
      setChosen(new Set(data.scenarios.map((_, index) => index)));
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

  const setPriority = (index, priority) => {
    setScenarios((list) => list.map((s, i) => (i === index ? { ...s, priority } : s)));
  };

  const toggle = (index) => {
    setChosen((set) => {
      const next = new Set(set);
      if (next.has(index)) next.delete(index); else next.add(index);
      return next;
    });
  };

  const save = async () => {
    const picked = scenarios.filter((_, index) => chosen.has(index));
    if (!picked.length) return;
    if (!suiteId && !targetSuite) {
      toast.warning('Create a Test Set first — the scenarios need somewhere to go.');
      return;
    }
    setBusy(true);
    try {
      await api.addCases(suiteId || targetSuite, picked.map((s) => ({
        name: s.title,
        goal: s.goal,
        url: readFrom || null,
        priority: s.priority,
        layer: s.layer,
        tags: [],
      })));
      toast.success(`${picked.length} scenario${picked.length > 1 ? 's' : ''} added.`);
      setScenarios([]);
      setChosen(new Set());
      onAdded?.();
    } catch (err) {
      toast.error(err.message);
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
            ? 'What should these cover? Leave empty for the screen as a whole.'
            : 'What to cover — e.g. “Homepage flight search” or “Booking - Payment, Klarna”'}
          disabled={busy}
        />
        {!sessionId && (
          <select
            className="generator-source"
            value={source}
            onChange={(event) => setSource(event.target.value)}
            disabled={busy}
            aria-label="What to read the scenarios from"
          >
            {sessions.map((item) => (
              <option key={item.sessionId} value={item.sessionId}>
                {item.device.kind === 'mobile' ? '📱' : '🌐'} {item.device.name}
              </option>
            ))}
            <option value="url">🌐 Open a URL…</option>
            <option value="brief">✎ Brief only</option>
          </select>
        )}
        {!sessionId && source === 'url' && (
          <div className="generator-url">
            <Globe size={13} />
            <input
              type="text"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="turkishairlines.com"
              disabled={busy}
            />
          </div>
        )}
        {needsTarget && (
          <select
            className="generator-suite"
            value={targetSuite || ''}
            onChange={(e) => setTargetSuite(e.target.value)}
            disabled={busy}
            aria-label="Test Set to file the scenarios into"
          >
            {suites.length === 0 && <option value="">No Test Set yet</option>}
            {suites.map((suite) => (
              <option key={suite.id} value={suite.id}>{suite.name}</option>
            ))}
          </select>
        )}
        <button className="btn btn-primary" onClick={() => generate()} disabled={busy}>
          {busy ? <Loader2 size={15} className="spin" /> : <Sparkles size={15} />}
          {busy ? 'Writing…' : 'Generate'}
        </button>
      </div>

      <p className="generator-hint">
        {sessionId || (source !== 'url' && source !== 'brief')
          ? 'Read from the device or page you have open, in the state it is in now.'
          : source === 'url'
            ? 'The page is opened and read, so scenarios name real fields and buttons.'
            : 'Written from the brief alone. Point it at a device or page for real fields.'}
        {' '}As many as the screen warrants — format and priority follow the standard.
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
            <strong>{scenarios.length}</strong> scenarios · {chosen.size} selected
          </div>
          <ul className="generated-list">
            {scenarios.map((scenario, index) => (
              <li key={index} className={chosen.has(index) ? 'picked' : ''}>
                <button
                  className="pick-box"
                  onClick={() => toggle(index)}
                  aria-label={chosen.has(index) ? 'Do not add this scenario' : 'Add this scenario'}
                >
                  {chosen.has(index) ? <Check size={13} /> : <X size={13} />}
                </button>
                <div className="generated-body">
                  <div className="generated-title">{scenario.title}</div>
                  {scenario.rationale && (
                    <div className="generated-why">{scenario.rationale}</div>
                  )}
                </div>
                <span className="layer-tag">{scenario.layer}</span>
                <select
                  className={`priority-select p-${scenario.priority.toLowerCase()}`}
                  value={scenario.priority}
                  onChange={(event) => setPriority(index, event.target.value)}
                  title="The generator proposes a priority from the standard; you have the last word."
                >
                  {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
                </select>
              </li>
            ))}
          </ul>
          <div className="generator-actions">
            <button className="btn btn-primary" onClick={save} disabled={busy || !chosen.size}>
              Add {chosen.size} to Test Set
            </button>
            <button className="btn btn-ghost" onClick={() => setScenarios([])} disabled={busy}>
              Discard
            </button>
          </div>
        </>
      )}
    </div>
  );
}
