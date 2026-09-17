import { useEffect, useRef, useState } from 'react';
import {
  Building2, Check, Cloud, Cpu, Eye, EyeOff, Key, Loader2, Play, Save, Square,
  Terminal, Zap,
} from 'lucide-react';
import { api } from '../api';
import { useToast } from '../hooks/useToast';

function AppiumCard({ status, logs, onStart, onStop, busy }) {
  const terminalRef = useRef(null);
  const toast = useToast();

  useEffect(() => {
    const node = terminalRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [logs]);

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

        <div className="terminal">
          <div className="terminal-bar">
            <Terminal size={12} />
            <span>appium.log</span>
            <button
              className="terminal-btn"
              onClick={() => {
                navigator.clipboard.writeText(logs);
                toast.success('Logs copied');
              }}
            >
              Copy
            </button>
          </div>
          <pre ref={terminalRef} className="terminal-body">
            {logs || '> Waiting for output…'}
          </pre>
        </div>
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
              Powers the agent's reasoning. Keys are written to the backend .env, never to the browser, and
              each provider keeps its own — switching does not erase the others.
            </p>
          </div>
        </div>
        {active && (
          <span className="run-status passed">
            <span className="status-dot" />
            {active.providerLabel}
          </span>
        )}
      </div>

      <div className="card-body">
        <div className="provider-tabs" role="tablist" aria-label="Model provider">
          {catalog.map((item) => (
            <button
              key={item.id}
              role="tab"
              aria-selected={selected === item.id}
              className={`provider-tab ${selected === item.id ? 'active' : ''}`}
              onClick={() => selectProvider(item.id)}
            >
              <span className="provider-tab-label">{item.label}</span>
              <span className={`provider-tab-state ${item.configured ? 'ok' : ''}`}>
                {item.configured ? <Check size={11} /> : null}
                {item.id === active?.provider ? 'in use' : item.configured ? 'key saved' : 'no key'}
              </span>
            </button>
          ))}
        </div>

        {entry && (
          <>
            <div className="field">
              <label htmlFor="api-key">
                {entry.label} API key
                <a href={entry.consoleUrl} target="_blank" rel="noreferrer">
                  Get one →
                </a>
              </label>
              <div className="input-with-icon">
                <Key size={15} />
                <input
                  id="api-key"
                  type={showKey ? 'text' : 'password'}
                  placeholder={entry.configured ? '•••••••••••••••••••••••••' : entry.keyHint}
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
              {entry.configured && (
                <p className="field-hint">A key is already saved. Enter a new one to replace it.</p>
              )}
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
                  list="model-suggestions"
                  value={model}
                  onChange={(event) => setModel(event.target.value)}
                  placeholder={entry.defaultModel}
                  autoComplete="off"
                  spellCheck="false"
                />
              </div>
              <datalist id="model-suggestions">
                {(liveModels ?? entry.models.map((m) => m.id)).map((id) => (
                  <option key={id} value={id} />
                ))}
              </datalist>
              <div className="model-chips">
                {liveModels
                  ? liveModels.map((id) => (
                      <button
                        key={id}
                        className={`model-chip ${model === id ? 'active' : ''}`}
                        onClick={() => setModel(id)}
                      >
                        {id}
                      </button>
                    ))
                  : entry.models.map((item) => (
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
    <section className="card">
      <div className="card-header">
        <div className="card-title-group">
          <div className="card-icon">
            <Cloud size={17} />
          </div>
          <div>
            <h3 className="card-title">BrowserStack</h3>
            <p className="card-desc">
              Run on real devices without keeping them on a desk. Optional.
            </p>
          </div>
        </div>
        {configured && (
          <span className="run-status passed">
            <span className="status-dot" />
            connected
          </span>
        )}
      </div>

      <form className="card-body" onSubmit={save}>
        <label className="field">
          <span className="field-label">Username</span>
          <input
            type="text"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            placeholder="ergun_AbCdEf"
            autoComplete="off"
          />
        </label>

        <label className="field">
          <span className="field-label">Access key</span>
          <div className="input-with-button">
            <input
              type={showKey ? 'text' : 'password'}
              value={accessKey}
              onChange={(event) => setAccessKey(event.target.value)}
              placeholder={configured ? 'Saved — type a new key to replace it' : 'Access key'}
              autoComplete="off"
            />
            <button
              type="button"
              className="btn-icon"
              onClick={() => setShowKey((current) => !current)}
              aria-label={showKey ? 'Hide the access key' : 'Show the access key'}
            >
              {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
          </div>
          <span className="field-hint">
            Both are on your BrowserStack account settings page. The app under test has
            to be uploaded to BrowserStack already — QAi picks from what is there.
          </span>
        </label>

        <button className="btn btn-primary" type="submit" disabled={saving}>
          {saving ? <Loader2 size={14} className="spin" /> : <Save size={14} />}
          {saving ? 'Checking…' : 'Save and verify'}
        </button>
      </form>
    </section>
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
          <p className="page-subtitle">Automation server and model credentials.</p>
        </div>
      </header>

      <div className="settings-grid">
        <AppiumCard status={appiumStatus} logs={logs} onStart={start} onStop={stop} busy={busy} />
        <ModelCard onSaved={onRefreshHealth} />
        <BrowserStackCard />
      </div>
    </main>
  );
}
