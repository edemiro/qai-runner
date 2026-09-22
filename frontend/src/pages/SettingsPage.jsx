import { useEffect, useRef, useState } from 'react';
import {
  Building2, Check, Cloud, Cpu, Eye, EyeOff, Globe, Key, Link as LinkIcon, Loader2,
  Play, Save, Square,
  Terminal, User, Zap,
} from 'lucide-react';
import { api } from '../api';
import { useToast } from '../hooks/useToast';
import './settings.css';

function AppiumCard({ status, logs, onStart, onStop, busy }) {
  const terminalRef = useRef(null);
  const toast = useToast();

  useEffect(() => {
    const node = terminalRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [logs]);

  /** A folded-away element has no layout, so the scroll above moves nothing
   *  while the log is shut. Opening it lands on the newest line rather than on
   *  wherever the reader happened to be an hour ago. */
  const revealLatest = (event) => {
    const node = terminalRef.current;
    if (event.currentTarget.open && node) node.scrollTop = node.scrollHeight;
  };

  return (
    <section className="card">
      <div className="card-header">
        <div className="card-title-group">
          <div className="card-icon">
            <Cpu size={17} />
          </div>
          <div>
            <h3 className="card-title">Appium server</h3>
            <p className="card-desc">Started as a child process with <code>--allow-cors</code>, on port 4723.</p>
          </div>
        </div>
        <span className={`run-status ${status === 'running' ? 'passed' : status === 'starting' ? 'running' : 'cancelled'}`}>
          <span className="status-dot" />
          {status}
        </span>
      </div>

      <div className="card-body">
        <p className="muted">
          {status === 'running'
            ? 'Device mirroring, the inspector and agent actions are all available.'
            : status === 'starting'
              ? 'Waiting for the server to come up — watch the log below.'
              : 'Nothing can reach a device until this server is running.'}
        </p>

        <div className="button-row">
          <button className="btn btn-primary" onClick={onStart} disabled={busy || status !== 'stopped'}>
            {busy ? <Loader2 size={14} className="spin" /> : <Play size={14} />}
            Start server
          </button>
          <button className="btn btn-danger" onClick={onStop} disabled={busy || status === 'stopped'}>
            <Square size={13} />
            Stop server
          </button>
        </div>

        {/* The log is read when something has already gone wrong, which is not
            most visits. Behind a line it costs a line; open on arrival it cost
            a quarter of the screen to everyone else. */}
        <details className="settings-fold settings-log" onToggle={revealLatest}>
          <summary>
            <Terminal size={12} />
            appium.log
            <button
              className="terminal-btn"
              onClick={(event) => {
                // A click inside a summary opens the fold unless the default is
                // cancelled, and copying is not a request to read.
                event.preventDefault();
                navigator.clipboard.writeText(logs);
                toast.success('Logs copied');
              }}
            >
              Copy
            </button>
          </summary>
          <div className="settings-fold-body">
            <pre ref={terminalRef} className="terminal-body">
              {logs || '> Waiting for output…'}
            </pre>
          </div>
        </details>
      </div>
    </section>
  );
}

function ModelCard({ onSaved }) {
  const toast = useToast();
  const [catalog, setCatalog] = useState([]);
  const [active, setActive] = useState(null);
  const [selected, setSelected] = useState(null);
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [extra, setExtra] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [liveModels, setLiveModels] = useState(null);
  const [loadingModels, setLoadingModels] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.providers();
        if (cancelled) return;
        setCatalog(data.providers);
        setActive(data.active);
        setSelected((current) => current ?? data.active.provider);
        setModel((current) => current || data.active.model);
        const entry = data.providers.find((item) => item.id === (data.active.provider));
        setExtra((current) => current || entry?.extra?.value || '');
      } catch (err) {
        if (!cancelled) toast.error(err.message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reloadKey, toast]);

  const entry = catalog.find((item) => item.id === selected);
  const isActive = active?.provider === selected;

  /** The chip list is the only thing on this card long enough to scroll — a
   *  live list off a real key runs to dozens — so the model field doubles as
   *  the search over it rather than leaving the reader to scroll a box for a
   *  name they can already type. An exact hit shows the whole list again:
   *  once you have landed on a model there is nothing left to narrow, and
   *  hiding its neighbours would take away the only way to compare them. */
  const suggestions = liveModels ? liveModels.map((id) => ({ id })) : entry?.models ?? [];
  const typed = model.trim().toLowerCase();
  const matches = !typed || suggestions.some((item) => item.id === model)
    ? suggestions
    : suggestions.filter((item) => item.id.toLowerCase().includes(typed));

  /** Switching the provider tab resets the model to that provider's default,
   *  unless it is the active one — then keep what is actually configured. */
  const selectProvider = (id) => {
    if (id === selected) return;
    const next = catalog.find((item) => item.id === id);
    setSelected(id);
    setApiKey('');
    setResult(null);
    setModel(active?.provider === id ? active.model : next?.defaultModel || '');
    setExtra(next?.extra?.value || '');
    setLiveModels(null);
  };

  /** Ask the provider what this key can actually reach. Hardcoded lists go
   *  stale when a provider retires a model, which shows up as a saved config
   *  suddenly failing with "model not found". */
  const loadModels = async () => {
    if (!entry || loadingModels) return;
    setLoadingModels(true);
    try {
      const data = await api.providerModels(selected);
      setLiveModels(data.models);
      if (data.models.length && !data.models.includes(model)) {
        toast.warning(`“${model}” is not in this key's model list. Pick one below.`);
      }
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoadingModels(false);
    }
  };

  const save = async () => {
    if (!entry) return;
    if (!apiKey.trim() && !entry.configured) {
      toast.warning(`Enter an API key for ${entry.label} first.`);
      return;
    }
    setSaving(true);
    try {
      const data = await api.saveProvider(selected, model, apiKey || null, entry.extra ? extra : null);
      setApiKey('');
      setResult(null);
      setActive(data.active);
      setReloadKey((key) => key + 1);
      toast.success(`${entry.label} is now driving the agent.`);
      onSaved();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTesting(true);
    setResult(null);
    try {
      const data = await api.testProvider(selected, model, apiKey || null, entry?.extra ? extra : null);
      setResult({ ok: true, message: `${data.message} (${data.latency}s)` });
    } catch (err) {
      setResult({ ok: false, message: err.message });
    } finally {
      setTesting(false);
    }
  };

  return (
    <section className="card">
      <div className="card-header">
        <div className="card-title-group">
          <div className="card-icon">
            <Zap size={17} />
          </div>
          <div>
            <h3 className="card-title">Model provider</h3>
            <p className="card-desc">
              Powers the agent's reasoning. Keys are written to the backend .env, never to the browser.
            </p>
          </div>
        </div>
      </div>

      <div className="card-body">
        {/* The strip below is the only place the card reports what is in use
            and which providers have a key, because it can say it for all of
            them at once — a pill in the header could only repeat one row of
            it. The provider object is always returned, `configured` and all,
            so a tick keyed on mere activeness showed a green tab for a
            provider with no key saved. Whether a key exists is the only thing
            that decides the colour. */}
        <div className="provider-tabs" role="tablist" aria-label="Model provider">
          {catalog.map((item) => {
            const inUse = item.id === active?.provider;
            return (
              <button
                key={item.id}
                role="tab"
                aria-selected={selected === item.id}
                className={`provider-tab ${selected === item.id ? 'active' : ''}`}
                onClick={() => selectProvider(item.id)}
              >
                <span className="provider-tab-label">{item.label}</span>
                <span
                  className={`provider-tab-state ${item.configured ? 'ok' : inUse ? 'warn' : ''}`}
                >
                  {item.configured ? <Check size={11} /> : null}
                  {inUse
                    ? item.configured ? 'in use' : 'in use — no key'
                    : item.configured ? 'key saved' : 'no key'}
                </span>
              </button>
            );
          })}
        </div>

        {entry && (
          <>
            {/* A key is typed once and then never again, so once one is saved
                it folds away and the model — the field that does get changed —
                sits directly under the tabs. A provider with no key opens on
                it, because then it is the only thing between here and a
                working agent; saving one closes it on the way past. The tab
                strip above already says which state that is, so the line
                itself does not repeat it. */}
            <details key={selected} className="settings-fold" open={!entry.configured}>
              <summary>
                <Key size={13} />
                {entry.label} credentials
              </summary>
              <div className="settings-fold-body">
                <div className="field">
                  <label htmlFor="api-key">
                    API key
                    <a href={entry.consoleUrl} target="_blank" rel="noreferrer">
                      Get one →
                    </a>
                  </label>
                  <div className="input-with-icon">
                    <Key size={15} />
                    <input
                      id="api-key"
                      type={showKey ? 'text' : 'password'}
                      placeholder={entry.configured ? 'Saved — type a new key to replace it' : entry.keyHint}
                      value={apiKey}
                      onChange={(event) => setApiKey(event.target.value)}
                      autoComplete="off"
                    />
                    <button
                      className="icon-btn-tiny"
                      onClick={() => setShowKey((value) => !value)}
                      aria-label="Toggle key visibility"
                    >
                      {showKey ? <EyeOff size={15} /> : <Eye size={15} />}
                    </button>
                  </div>
                </div>

                {entry.extra && (
                  <div className="field">
                    <label htmlFor="provider-extra">
                      {entry.extra.label}
                      {entry.extra.optional && <span className="field-optional">optional</span>}
                    </label>
                    <div className="input-with-icon">
                      <Building2 size={15} />
                      <input
                        id="provider-extra"
                        value={extra}
                        onChange={(event) => setExtra(event.target.value)}
                        placeholder={entry.extra.hint}
                        autoComplete="off"
                        spellCheck="false"
                      />
                    </div>
                    <p className="field-hint">{entry.extra.help}</p>
                  </div>
                )}
              </div>
            </details>

            <div className="field">
              <label htmlFor="model-input">
                Model
                <button
                  className="link-btn"
                  onClick={loadModels}
                  disabled={loadingModels || !entry.configured}
                  title={entry.configured ? 'Ask the provider what this key can reach'
                    : 'Save an API key first'}
                >
                  {loadingModels ? 'Loading…' : 'Load available models'}
                </button>
              </label>
              <div className="input-with-icon">
                <Cpu size={15} />
                <input
                  id="model-input"
                  value={model}
                  onChange={(event) => setModel(event.target.value)}
                  placeholder={entry.defaultModel}
                  autoComplete="off"
                  spellCheck="false"
                />
              </div>
              {matches.length > 0 && (
                <div className="model-chips">
                  {matches.map((item) => (
                    <button
                      key={item.id}
                      className={`model-chip ${model === item.id ? 'active' : ''}`}
                      onClick={() => setModel(item.id)}
                      title={item.note}
                    >
                      {item.id}
                    </button>
                  ))}
                </div>
              )}
              <p className="field-hint">
                Any model id this key can reach works — the suggestions are a starting point, not a limit.
              </p>
            </div>

            <div className="button-row">
              <button
                className="btn btn-ghost"
                onClick={test}
                disabled={testing || (!apiKey && !entry.configured)}
              >
                {testing ? <Loader2 size={14} className="spin" /> : null}
                Test connection
              </button>
              <button
                className="btn btn-primary"
                onClick={save}
                disabled={saving || (!apiKey.trim() && !entry.configured)}
              >
                <Save size={14} />
                {saving ? 'Saving…' : isActive ? 'Save' : `Use ${entry.label}`}
              </button>
            </div>

            {result && (
              <div className={`banner ${result.ok ? 'success' : 'danger'}`}>
                <span>{result.message}</span>
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}

/**
 * BrowserStack credentials.
 *
 * Optional throughout: without them the Mobile page simply offers the devices
 * attached to this computer, which is what it did before. The key is stored
 * the same way the model keys are — in the backend's .env, never in the
 * browser — and is verified by actually asking BrowserStack for the device
 * list, because a key that saves but cannot book anything is not saved.
 */
function BrowserStackCard() {
  const toast = useToast();
  const [username, setUsername] = useState('');
  const [accessKey, setAccessKey] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [configured, setConfigured] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const status = await api.browserstackStatus();
        if (cancelled) return;
        setConfigured(status.configured);
        if (status.username) setUsername(status.username);
      } catch {
        // An older backend, or none reachable — the card still explains itself.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const save = async (event) => {
    event.preventDefault();
    if (!username.trim() || !accessKey.trim()) {
      toast.warning('Both the username and the access key are needed.');
      return;
    }
    setSaving(true);
    try {
      await api.saveBrowserstackCredentials(username.trim(), accessKey.trim());
      setConfigured(true);
      setAccessKey('');
      toast.success('BrowserStack connected — its devices are on the Mobile page.');
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <details className="settings-fold is-card">
      <summary>
        <Cloud size={15} />
        BrowserStack
        <span className="settings-fold-aside">{configured ? 'connected' : 'optional'}</span>
      </summary>

      <form className="settings-fold-body" onSubmit={save}>
        <p className="muted">
          Run on real devices without keeping them on a desk. The app under test has
          to be uploaded to BrowserStack already — QAi picks from what is there.
        </p>

        <div className="field-row">
          <label className="field">
            <span className="field-label">Username</span>
            <div className="input-with-icon">
              <User size={15} />
              <input
                type="text"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                placeholder="ergun_AbCdEf"
                autoComplete="off"
              />
            </div>
          </label>

          <label className="field">
            <span className="field-label">Access key</span>
            <div className="input-with-icon">
              <Key size={15} />
              <input
                type={showKey ? 'text' : 'password'}
                value={accessKey}
                onChange={(event) => setAccessKey(event.target.value)}
                placeholder={configured ? 'Saved — type a new key to replace it' : 'Access key'}
                autoComplete="off"
              />
              <button
                type="button"
                className="icon-btn-tiny"
                onClick={() => setShowKey((current) => !current)}
                aria-label={showKey ? 'Hide the access key' : 'Show the access key'}
              >
                {showKey ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
          </label>
        </div>

        <p className="field-hint">Both are on your BrowserStack account settings page.</p>

        <button className="btn btn-primary" type="submit" disabled={saving}>
          {saving ? <Loader2 size={14} className="spin" /> : <Save size={14} />}
          {saving ? 'Checking…' : 'Save and verify'}
        </button>
      </form>
    </details>
  );
}

/**
 * Both are Server installations rather than Cloud, which decides the
 * credential: a Personal Access Token, sent as a Bearer header. A Cloud API
 * token is a different thing and comes back 401 with nothing useful said, so
 * the field says which one to paste. The address doubles as the default the
 * fields open on, so it is written once here rather than once per use.
 */
const TRACKERS = [
  { id: 'jira', label: 'Jira', address: 'https://jira.thy.com' },
  { id: 'confluence', label: 'Confluence', address: 'https://confluence.thy.com' },
];

/**
 * Jira and Confluence, so a story or an analysis page can be handed to the
 * scenario writer as a link instead of retyped.
 *
 * One fold, two independent services: a team can have one and not the other,
 * and the half that works should keep working.
 */
function TrackerCard() {
  const toast = useToast();
  const [status, setStatus] = useState({});
  const [draft, setDraft] = useState(() =>
    Object.fromEntries(TRACKERS.map((service) => [service.id, { baseUrl: service.address, token: '' }])));
  const [saving, setSaving] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.trackerStatus();
        if (cancelled) return;
        setStatus(data);
        setDraft((current) => Object.fromEntries(TRACKERS.map((service) => [
          service.id,
          { ...current[service.id], baseUrl: data[service.id]?.baseUrl || current[service.id].baseUrl },
        ])));
      } catch {
        // An older backend: the fold still explains what it is for.
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const save = async (id, label) => {
    const entry = draft[id];
    if (!entry.baseUrl.trim() || !entry.token.trim()) {
      toast.warning('Both the address and the token are needed.');
      return;
    }
    setSaving(id);
    try {
      await api.saveTrackerCredentials(id, entry.baseUrl.trim(), entry.token.trim());
      setStatus((current) => ({
        ...current, [id]: { configured: true, baseUrl: entry.baseUrl.trim() },
      }));
      // Not kept in the field after it is saved: it lives in the backend's
      // .env, and leaving it on screen only invites it into a screenshot.
      setDraft((current) => ({ ...current, [id]: { ...current[id], token: '' } }));
      toast.success(`${label} connected — paste a link on Test Sets.`);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSaving(null);
    }
  };

  // Shut, the line has room for which halves are working, and that is the only
  // thing anyone wants from a fold they last opened months ago. Named rather
  // than counted, because "one of two" leaves the reader to open it and look.
  const connected = TRACKERS.filter((service) => status[service.id]?.configured);
  const state = connected.length === TRACKERS.length
    ? 'connected'
    : connected.length
      ? `${connected.map((service) => service.label).join(' and ')} connected`
      : 'optional';

  return (
    <details className="settings-fold is-card">
      <summary>
        <LinkIcon size={15} />
        Jira &amp; Confluence
        <span className="settings-fold-aside">{state}</span>
      </summary>

      <div className="settings-fold-body">
        <p className="muted">
          Paste a story or an analysis page on Test Sets and write the scenarios
          from it. Each one stands on its own.
        </p>

        <div className="tracker-services">
          {TRACKERS.map(({ id, label, address }) => (
            <div className="tracker-service" key={id}>
              <div className="tracker-service-head">
                <strong>{label}</strong>
              </div>
              <div className="field-row">
                <label className="field">
                  <span className="field-label">Address</span>
                  <div className="input-with-icon">
                    <Globe size={15} />
                    <input
                      type="text"
                      value={draft[id].baseUrl}
                      onChange={(e) => setDraft((c) => ({
                        ...c, [id]: { ...c[id], baseUrl: e.target.value },
                      }))}
                      placeholder={address}
                    />
                  </div>
                </label>
                <label className="field">
                  <span className="field-label">Personal Access Token</span>
                  <div className="input-with-icon">
                    <Key size={15} />
                    <input
                      type="password"
                      value={draft[id].token}
                      onChange={(e) => setDraft((c) => ({
                        ...c, [id]: { ...c[id], token: e.target.value },
                      }))}
                      placeholder={status[id]?.configured
                        ? 'Saved — paste a new one to replace it'
                        : 'Profile → Personal Access Tokens'}
                    />
                  </div>
                </label>
              </div>
              <button
                className="btn btn-primary btn-sm tracker-save"
                onClick={() => save(id, label)}
                disabled={saving === id}
              >
                {saving === id ? 'Saving…' : `Save ${label}`}
              </button>
            </div>
          ))}
        </div>
      </div>
    </details>
  );
}

export function SettingsPage({ appiumStatus, onRefreshHealth }) {
  const toast = useToast();
  const [logs, setLogs] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer = null;

    const tick = async () => {
      try {
        const data = await api.appiumLogs();
        if (!cancelled) setLogs(data.logs || '');
      } catch {
        /* the status strip already reports connectivity */
      } finally {
        if (!cancelled) timer = setTimeout(tick, 2000);
      }
    };

    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  const start = async () => {
    setBusy(true);
    try {
      const data = await api.startAppium();
      toast.success(data.message);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    setBusy(true);
    try {
      const data = await api.stopAppium();
      toast.info(data.message);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="page">
      <header className="page-header">
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="page-subtitle">The agent's model, the Appium server, and the keys for everything else.</p>
        </div>
      </header>

      {/* The model and the server are the two things touched on an ordinary
          day, so they get the first screen. BrowserStack and the trackers are
          typed once and then left alone for months; they wait behind a line
          each rather than taking a column apiece from the two that are used. */}
      <div className="settings-grid">
        <ModelCard onSaved={onRefreshHealth} />
        <AppiumCard status={appiumStatus} logs={logs} onStart={start} onStop={stop} busy={busy} />
      </div>

      <div className="settings-optional">
        <BrowserStackCard />
        <TrackerCard />
      </div>
    </main>
  );
}
