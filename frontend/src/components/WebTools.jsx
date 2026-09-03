import { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  Camera,
  KeyRound,
  Network,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react';

import { ScenarioGenerator } from './ScenarioGenerator';
import { api } from '../api';
import { useToast } from '../hooks/useToast';

const TABS = [
  { id: 'auth', label: 'Sign-in', icon: KeyRound },
  { id: 'mock', label: 'Network', icon: Network },
  { id: 'visual', label: 'Visual', icon: Camera },
  { id: 'errors', label: 'Errors', icon: AlertTriangle },
  { id: 'scenarios', label: 'Scenarios', icon: Sparkles },
];

const SAMPLE_RULES = `[
  { "url": "**/api/search*", "status": 500, "body": { "error": "boom" } },
  { "url": "**/api/slow*", "abort": "timedout" }
]`;

/**
 * The things a tester reaches for that are not "drive the page": reuse a
 * sign-in, force an API to fail, check the page still looks right, and see
 * what the browser is complaining about.
 */
export function WebTools({ sessionId, onClose }) {
  const toast = useToast();
  const [tab, setTab] = useState('auth');

  const [profiles, setProfiles] = useState([]);
  const [profileName, setProfileName] = useState('');

  const [rulesText, setRulesText] = useState('');
  const [rulesError, setRulesError] = useState(null);
  const [activeRules, setActiveRules] = useState([]);

  const [baselines, setBaselines] = useState([]);
  const [baselineName, setBaselineName] = useState('');
  const [visualResult, setVisualResult] = useState(null);

  const [events, setEvents] = useState([]);

  const refresh = useCallback(async () => {
    try {
      const [auth, baseline, routes] = await Promise.all([
        api.authProfiles(),
        api.baselines(),
        api.routes(sessionId).catch(() => ({ rules: [] })),
      ]);
      setProfiles(auth.profiles);
      setBaselines(baseline.baselines);
      setActiveRules(routes.rules || []);
    } catch (err) {
      toast.error(err.message);
    }
  }, [sessionId, toast]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!cancelled) await refresh();
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  // The error list is only meaningful while it is being looked at, so it is
  // polled on the tab rather than kept in sync all the time.
  useEffect(() => {
    if (tab !== 'errors' || !sessionId) return undefined;
    let cancelled = false;
    const tick = async () => {
      try {
        const data = await api.pageEvents(sessionId);
        if (!cancelled) setEvents(data.events);
      } catch {
        /* the page may be gone; the workspace banner already says so */
      }
    };
    tick();
    const timer = setInterval(tick, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [tab, sessionId]);

  const saveAuth = async () => {
    const name = profileName.trim();
    if (!name) return;
    try {
      await api.saveAuth(sessionId, name);
      setProfileName('');
      await refresh();
      toast.success(`Sign-in “${name}” saved. Cases using it start already logged in.`);
    } catch (err) {
      toast.error(err.message);
    }
  };

  const applyRules = async () => {
    let parsed;
    try {
      parsed = JSON.parse(rulesText || '[]');
    } catch (err) {
      setRulesError(`That is not valid JSON: ${err.message}`);
      return;
    }
    if (!Array.isArray(parsed)) {
      setRulesError('Rules must be a list.');
      return;
    }
    try {
      const data = await api.setRoutes(sessionId, parsed);
      setRulesError(null);
      setActiveRules(data.rules);
      toast.success(
        data.installed
          ? `${data.installed} rule(s) active. Matching requests are now faked.`
          : 'All rules cleared — the page talks to the real backend again.',
      );
    } catch (err) {
      toast.error(err.message);
    }
  };

  const runVisualCheck = async (update) => {
    const name = baselineName.trim();
    if (!name) {
      toast.warning('Give the baseline a name first.');
      return;
    }
    try {
      const result = await api.visualCheck(sessionId, { name, update });
      setVisualResult({ ...result, checkedAt: Date.now() });
      await refresh();
      if (result.status === 'created') toast.success(`Baseline “${name}” created.`);
      else if (result.passed) toast.success(`“${name}” matches its baseline.`);
      else toast.warning(result.message);
    } catch (err) {
      toast.error(err.message);
    }
  };

  return (
    <aside className="web-tools">
      <div className="web-tools-head">
        <div className="segmented">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button key={id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>
              <Icon size={13} /> {label}
            </button>
          ))}
        </div>
        <button className="btn-icon" onClick={onClose} aria-label="Close tools">
          <X size={15} />
        </button>
      </div>

      {tab === 'auth' && (
        <div className="tool-body">
          <p className="muted small">
            Freeze the current session's cookies and storage. A suite case can
            then start already signed in — faster, and it stops a shared test
            account being locked out by repeated logins.
          </p>
          <div className="field-row tight">
            <input
              value={profileName}
              onChange={(event) => setProfileName(event.target.value)}
              placeholder="Profile name, e.g. standard-user"
            />
            <button className="btn btn-primary btn-sm" onClick={saveAuth} disabled={!profileName.trim()}>
              Save sign-in
            </button>
          </div>

          {profiles.length === 0 ? (
            <p className="muted small">No saved sign-ins yet.</p>
          ) : (
            <ul className="tool-list">
              {profiles.map((profile) => (
                <li key={profile.name}>
                  <span>{profile.name}</span>
                  <span className="muted small">
                    {new Date(profile.savedAt * 1000).toLocaleDateString()}
                  </span>
                  <button
                    className="btn-icon danger"
                    onClick={async () => {
                      await api.deleteAuthProfile(profile.name);
                      refresh();
                    }}
                    aria-label={`Delete ${profile.name}`}
                  >
                    <Trash2 size={13} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {tab === 'mock' && (
        <div className="tool-body">
          <p className="muted small">
            Fulfil or fail matching requests, so the error paths can be tested. A
            live backend that insists on working cannot be made to return a 500.
          </p>
          <textarea
            className="mono"
            rows={8}
            value={rulesText}
            onChange={(event) => {
              setRulesText(event.target.value);
              setRulesError(null);
            }}
            placeholder={SAMPLE_RULES}
          />
          {rulesError && <span className="field-error">{rulesError}</span>}
          <div className="row-actions">
            <button className="btn btn-sm" onClick={() => setRulesText(SAMPLE_RULES)}>
              Insert example
            </button>
            <button className="btn btn-primary btn-sm" onClick={applyRules}>
              Apply
            </button>
            <button
              className="btn btn-sm"
              onClick={() => {
                setRulesText('[]');
                api.setRoutes(sessionId, []).then(() => {
                  setActiveRules([]);
                  toast.info('Rules cleared.');
                });
              }}
            >
              Clear
            </button>
          </div>
          {activeRules.length > 0 && (
            <p className="muted small">
              {activeRules.length} rule(s) active: {activeRules.map((r) => r.url).join(', ')}
            </p>
          )}
        </div>
      )}

      {tab === 'visual' && (
        <div className="tool-body">
          <p className="muted small">
            Compare the page against a stored baseline. Small differences are
            absorbed on purpose — the check is for a layout that broke, not for
            a moved caret.
          </p>
          <div className="field-row tight">
            <input
              value={baselineName}
              onChange={(event) => setBaselineName(event.target.value)}
              placeholder="Baseline name, e.g. home-desktop"
              list="baseline-names"
            />
            <datalist id="baseline-names">
              {baselines.map((baseline) => (
                <option key={baseline.name} value={baseline.name} />
              ))}
            </datalist>
            <button className="btn btn-primary btn-sm" onClick={() => runVisualCheck(false)}>
              Check
            </button>
            <button
              className="btn btn-sm"
              onClick={() => runVisualCheck(true)}
              title="Replace the stored baseline with what is on screen now"
            >
              Update
            </button>
          </div>

          {visualResult && (
            <div className={`visual-result ${visualResult.passed ? 'ok' : 'bad'}`}>
              <strong>{visualResult.message}</strong>
              {visualResult.diffPath && (
                <img
                  // The diff is written to a fixed path, so the browser would
                  // serve the previous check's image from cache without this.
                  src={`${api.baselineImageUrl(visualResult.name, true)}&t=${visualResult.checkedAt}`}
                  alt="What changed since the baseline"
                  className="visual-diff"
                />
              )}
            </div>
          )}

          {baselines.length > 0 && (
            <ul className="tool-list">
              {baselines.map((baseline) => (
                <li key={baseline.name}>
                  <span>{baseline.name}</span>
                  <span className="muted small">{baseline.width}×{baseline.height}</span>
                  <button
                    className="btn-icon danger"
                    onClick={async () => {
                      await api.deleteBaseline(baseline.name);
                      refresh();
                    }}
                    aria-label={`Delete ${baseline.name}`}
                  >
                    <Trash2 size={13} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {tab === 'scenarios' && (
        <ScenarioGenerator sessionId={sessionId} />
      )}

      {tab === 'errors' && (
        <div className="tool-body">
          <p className="muted small">
            Console errors and failed requests since this page was opened. A run
            that ends green with entries here has not proved the feature works.
          </p>
          {events.length === 0 ? (
            <p className="muted small">Nothing logged. The page is clean.</p>
          ) : (
            <ul className="error-list compact">
              {events.map((event, index) => (
                <li key={index} className="error-row">
                  <span className={`error-kind ${event.kind}`}>{event.kind}</span>
                  <div className="error-body">
                    <span className="error-text">{event.text}</span>
                    {event.url && <span className="error-url">{event.url}</span>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </aside>
  );
}
