import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle, Bug, Camera, ChevronLeft, ChevronRight, ExternalLink, Folder, Layers,
  ListChecks, Loader2, Search, Trash2,
} from 'lucide-react';

import { api } from '../api';
import { DEFAULT_PLATFORM, PlatformTabs } from '../components/PlatformTabs';
import { useToast } from '../hooks/useToast';
import { OS_TABS } from '../lib/platforms';
import './bugs.css';

/**
 * Defects raised off failed scenarios.
 *
 * A run says a scenario failed; a bug says what that turned out to mean, and
 * outlives the run that found it. Everything needed to read one — the title,
 * the body, the frame — was copied in when it was raised, so a bug still reads
 * correctly after its run, its scenario and its Test Set have all been deleted.
 */

const STATUS_LABEL = {
  open: 'Open',
  triaged: 'Triaged',
  fixed: 'Fixed',
  closed: 'Closed',
  'not-a-bug': 'Not a bug',
};

// Dealt with, one way or another. Still listed — a fix that did not hold is
// found by looking here — but quieter than the ones that need someone.
const SETTLED = new Set(['fixed', 'closed', 'not-a-bug']);

// A row standing for several bugs takes the state of the one that most needs
// someone, and the severity of the worst.
const STATUS_URGENCY = ['open', 'triaged', 'fixed', 'closed', 'not-a-bug'];
const SEVERITY_RANK = { critical: 4, high: 3, medium: 2, low: 1 };

// Bugs whose scenario belonged to no Test Set: raised from a chat run, or by
// hand with nothing to point back to.
const NO_SET = 'Outside any Test Set';

/* The labelled parts of a bug's body, in the order a reader wants them: what
   should have happened and what did first, then why the tool thinks so, then
   where. Each label has its own colour so the eye finds "Actual" without
   reading the others. */
const FACTS = [
  { key: 'expected', label: 'Expected', tone: 'success' },
  { key: 'actual', label: 'Actual', tone: 'danger' },
  { key: 'failing', label: 'Failing action', tone: 'warning' },
  { key: 'cause', label: 'Cause', tone: 'purple' },
  { key: 'where', label: 'Where', tone: 'info' },
];

function when(seconds) {
  if (!seconds) return '—';
  // The reader's own locale, not one written into the source. Pinned to
  // tr-TR, an English product showed "27 Eyl 23:37" on every row of two of
  // its pages — the first thing anyone outside the team saw.
  return new Date(seconds * 1000).toLocaleString(undefined, {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

// The end of a span that began the same day needs only its time.
function until(first, last) {
  if (!last || last <= first) return '';
  const start = new Date(first * 1000);
  const end = new Date(last * 1000);
  const sameDay = start.toDateString() === end.toDateString();
  return ` – ${sameDay
    ? end.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    : when(last)}`;
}

function severityLabel(severity) {
  return severity[0].toUpperCase() + severity.slice(1).toLowerCase();
}

/* One group per Test Set — which is also what its executions are named
   after — in the order of each set's newest bug, so the set that just broke
   is the one on top. A flat list of a hundred bugs read as a hundred
   unrelated things; most of them are a handful of areas breaking in a
   handful of ways. Within a set the ones still needing someone come first,
   newest first; the settled ones follow, so closing fourteen stale bugs does
   not leave them sitting over the one that is real. */
function groupBugs(bugs) {
  const groups = new Map();
  for (const bug of bugs) {
    const name = bug.suite_name || NO_SET;
    if (!groups.has(name)) groups.set(name, { key: name, name, bugs: [], open: 0 });
    const group = groups.get(name);
    group.bugs.push(bug);
    if (bug.status === 'open') group.open += 1;
  }
  for (const group of groups.values()) {
    const same = new Map();
    for (const bug of group.bugs) {
      const key = sameErrorKey(bug);
      if (!same.has(key)) same.set(key, []);
      same.get(key).push(bug);
    }
    // Stable: the server's newest-first order holds within each half.
    group.rows = [...same.values()].map(rowOf)
      .sort((a, b) => Number(SETTLED.has(a.status)) - Number(SETTLED.has(b.status)));
  }
  return [...groups.values()];
}

/* One execution that failed fourteen scenarios on the same error filed
   fourteen bugs, and the list showed fourteen identical rows. They are one
   finding, so they are one row — but only within one execution: the same
   words from two executions are two findings. Each bug is kept whole
   underneath, with its own steps and screen, and is opened from the row. */
function sameErrorKey(bug) {
  if (!bug.suite_run_id) return `bug:${bug.id}`;
  return `${bug.suite_run_id}::${bug.title.trim().toLowerCase().replace(/\s+/g, ' ')}`;
}

function rowOf(bugs) {
  const lead = bugs[0];
  if (bugs.length === 1) {
    return { key: lead.id, bugs, lead, status: lead.status, severity: lead.severity, many: false };
  }
  const status = STATUS_URGENCY.find((s) => bugs.some((b) => b.status === s)) || lead.status;
  const severity = bugs.map((b) => b.severity).filter(Boolean).sort(
    (a, b) => (SEVERITY_RANK[b.toLowerCase()] || 0) - (SEVERITY_RANK[a.toLowerCase()] || 0),
  )[0] || null;
  return { key: sameErrorKey(lead), bugs, lead, status, severity, many: true };
}

function holds(row, key) {
  return row.key === key || row.bugs.some((bug) => bug.id === key);
}

/* A screenshot belongs to its bug, so this panel is keyed by the bug at the
   call site: selecting another one gets a new panel rather than the old one
   with the previous bug's screen still in it. Guarded here as well, because a
   caller that forgets the key would show one bug's evidence under another's
   title — which is worse than showing none. */
function BugShot({ bugId }) {
  const [image, setImage] = useState(null);
  const [state, setState] = useState('idle');
  const [shownFor, setShownFor] = useState(bugId);

  if (shownFor !== bugId) {
    setShownFor(bugId);
    setImage(null);
    setState('idle');
  }

  const load = async () => {
    if (image || state === 'loading') return;
    setState('loading');
    try {
      const data = await api.bugScreenshot(bugId);
      setImage(data.screenshot);
      setState('shown');
    } catch (err) {
      setState(err.message || 'No screenshot was kept with this bug.');
    }
  };

  if (image) {
    const src = `data:${image.startsWith('/9j/') ? 'image/jpeg' : 'image/png'};base64,${image}`;
    return <img className="bug-shot" src={src} alt="The screen when the scenario failed" />;
  }
  if (state !== 'idle' && state !== 'loading' && state !== 'shown') {
    return <span className="muted small">{state}</span>;
  }
  return (
    <button className="btn btn-ghost btn-sm bug-shot-button" onClick={load}
            disabled={state === 'loading'}>
      {state === 'loading' ? <Loader2 size={14} className="spin" /> : <Camera size={14} />}
      Show the screen
    </button>
  );
}

/* A row says what is broken, then where it was found. The scenario used to be
   the title — "Hotel - Guests | [Jolly: IST] adults per room 5 - raise
   adults to the limit and control a… — step 4: Yetişkin…", 161 characters at
   the median — and the defect was nowhere in it. */
function BugRow({ bug, active, notApp, codeText, onSelect }) {
  const scenario = bug.case_name || bug.parts?.fields?.scenario;
  const settled = SETTLED.has(bug.status);
  return (
    <button
      type="button"
      className={`bug-item ${active ? 'active' : ''} ${settled ? 'settled' : ''}`}
      onClick={onSelect}
      aria-current={active ? 'true' : undefined}
    >
      <span className={`bug-dot s-${bug.status}`} title={STATUS_LABEL[bug.status] || bug.status}>
        <span className="visually-hidden">{STATUS_LABEL[bug.status] || bug.status}: </span>
      </span>
      <span className="bug-item-main">
        <span className="bug-item-title">{bug.title}</span>
        {scenario && (
          <span className="bug-item-scenario" title={scenario}>
            {bug.case_idx != null && <span className="bug-item-idx">#{bug.case_idx}</span>}
            {scenario}
          </span>
        )}
      </span>
      <span className="bug-item-side">
        {bug.severity && (
          <span className={`priority-tag p-${bug.severity.toLowerCase()}`}>
            {severityLabel(bug.severity)}
          </span>
        )}
        {/* Only when the cause is the run's rather than the app's: saying
            "expected result did not hold" on every row said nothing. */}
        {notApp && <span className="bug-code soft" title={codeText}>Run issue</span>}
        <span className="bug-item-when">{when(bug.created_at)}</span>
      </span>
    </button>
  );
}

/* One error, several scenarios: the row says how many and which, and opens
   the list of them. */
function SameErrorRow({ row, active, notApp, codeText, onSelect }) {
  const settled = SETTLED.has(row.status);
  const numbers = row.bugs.map((bug) => bug.case_idx).filter((idx) => idx != null);
  const names = row.bugs.map((bug) => bug.case_name || bug.parts?.fields?.scenario).filter(Boolean);
  return (
    <button
      type="button"
      className={`bug-item ${active ? 'active' : ''} ${settled ? 'settled' : ''}`}
      onClick={onSelect}
      aria-current={active ? 'true' : undefined}
    >
      <span className={`bug-dot s-${row.status}`} title={STATUS_LABEL[row.status] || row.status}>
        <span className="visually-hidden">{STATUS_LABEL[row.status] || row.status}: </span>
      </span>
      <span className="bug-item-main">
        <span className="bug-item-title">{row.lead.title}</span>
        <span className="bug-item-scenario" title={names.join('\n')}>
          <span className="same-count">{row.bugs.length} scenarios</span>
          {numbers.map((idx) => `#${idx}`).join(' · ')}
        </span>
      </span>
      <span className="bug-item-side">
        {row.severity && (
          <span className={`priority-tag p-${row.severity.toLowerCase()}`}>
            {severityLabel(row.severity)}
          </span>
        )}
        {notApp && <span className="bug-code soft" title={codeText}>Run issue</span>}
        <span className="bug-item-when">{when(row.lead.created_at)}</span>
      </span>
    </button>
  );
}

/* What the scenarios share is said once — the error, why, where — and what
   each has of its own, the steps and the screen, is one click further. */
function SameErrorDetail({ row, meta, onStatus, onDelete, onPick }) {
  const { lead, bugs } = row;
  const fields = lead.parts?.fields || {};
  const shared = FACTS.filter((fact) => ['actual', 'cause', 'where'].includes(fact.key)
    && fields[fact.key] && fields[fact.key] !== '—');
  const notes = new Set(bugs.map((bug) => bug.note || ''));
  const note = notes.size === 1 ? lead.note : null;
  const times = bugs.map((bug) => bug.created_at || 0);
  const first = Math.min(...times);
  const last = Math.max(...times);

  return (
    <>
      <div className="bug-detail-head">
        <div className="bug-detail-crumbs">
          <span className="bug-detail-set"><Folder size={13} /> {lead.suite_name || NO_SET}</span>
          <span>One execution · {when(first)}{until(first, last)}</span>
        </div>
        <h2 className="bug-detail-title">{lead.title}</h2>
        <p className="bug-detail-scenario">
          <Layers size={14} aria-hidden="true" />
          <span>
            The same error in {bugs.length} scenarios of one execution. Each keeps its own
            steps and screen — open one below.
          </span>
        </p>
      </div>

      <div className="bug-actions">
        <span className={`bug-dot s-${row.status}`} aria-hidden="true" />
        <select
          value={row.status}
          onChange={(event) => onStatus(row, event.target.value)}
          aria-label={`Status of all ${bugs.length} bugs`}
        >
          {(meta.statuses || []).map((value) => (
            <option key={value} value={value}>{STATUS_LABEL[value] || value}</option>
          ))}
        </select>
        <span className="muted small">for all {bugs.length}</span>
        {row.severity && (
          <span className={`priority-tag p-${row.severity.toLowerCase()}`}>
            {severityLabel(row.severity)}
          </span>
        )}
        {lead.parts?.autoRaised && (
          <span className="bug-auto" title="Filed by the run when the scenarios failed, before anybody had read them.">
            Auto-raised
          </span>
        )}
        <span className="bug-actions-gap" />
        <button className="btn btn-danger btn-sm" onClick={() => onDelete(row)}>
          <Trash2 size={14} /> Delete all {bugs.length}
        </button>
      </div>

      {(lead.parts?.notes || []).map((text) => (
        <p key={text} className="bug-callout">
          <AlertTriangle size={14} aria-hidden="true" />
          <span>{text}</span>
        </p>
      ))}

      {(shared.length > 0 || note) && (
        <dl className="facts">
          {note && (
            <div className="fact">
              <dt className="fact-label tone-accent">Note</dt>
              <dd className="fact-value">{note}</dd>
            </div>
          )}
          {shared.map((fact) => (
            <div className="fact" key={fact.key}>
              <dt className={`fact-label tone-${fact.tone}`}>{fact.label}</dt>
              <dd className="fact-value">
                <FactValue fact={fact} value={fields[fact.key]} bug={lead} meta={meta} />
              </dd>
            </div>
          ))}
        </dl>
      )}

      <section className="bug-section">
        <h3 className="bug-section-title">Affected scenarios ({bugs.length})</h3>
        <ul className="bug-members">
          {bugs.map((bug) => {
            const failed = (bug.parts?.steps || []).find((step) => step.failed);
            return (
              <li key={bug.id}>
                <button type="button" className="bug-member" onClick={() => onPick(bug.id)}>
                  <span className={`bug-dot s-${bug.status}`} title={STATUS_LABEL[bug.status] || bug.status} />
                  <span className="bug-member-main">
                    <span className="bug-member-name">
                      {bug.case_idx != null && <span className="bug-item-idx">#{bug.case_idx}</span>}
                      {bug.case_name || bug.parts?.fields?.scenario || bug.title}
                    </span>
                    {failed && (
                      <span className="bug-member-step" title={failed.action}>
                        Step {failed.idx}: {failed.action}
                      </span>
                    )}
                  </span>
                  <span className="bug-item-when">{when(bug.created_at)}</span>
                  <ChevronRight size={14} className="bug-member-go" aria-hidden="true" />
                </button>
              </li>
            );
          })}
        </ul>
      </section>
    </>
  );
}

function FactValue({ fact, value, bug, meta }) {
  if (fact.key === 'where' && /^https?:\/\//.test(value)) {
    return <a href={value} target="_blank" rel="noreferrer">{value}</a>;
  }
  if (fact.key === 'cause') {
    // The sentence, with the code on hover: the code is for grouping, the
    // sentence is for reading.
    const code = bug.code || value.split(' — ')[0];
    const notApp = (meta.notAppDefects || []).includes(code);
    return (
      <>
        <span title={code}>{meta.codes?.[code] || value}</span>
        {notApp && <span className="fact-flag">Not an app defect</span>}
      </>
    );
  }
  if (fact.key === 'failing' && value.includes(' — ')) {
    const [action, ...said] = value.split(' — ');
    return (
      <>
        <code className="fact-code">{action}</code> {said.join(' — ')}
      </>
    );
  }
  return value;
}

function BugDetail({ bug, meta, onOpenRun, onStatus, onDelete, sameError = null, onBack = null }) {
  const parts = bug.parts || {};
  const fields = parts.fields || {};
  const scenario = bug.case_name || fields.scenario;
  const facts = FACTS.filter((fact) => fields[fact.key] && fields[fact.key] !== '—');
  const steps = parts.steps || [];
  const events = parts.events || [];

  return (
    <>
      {sameError && onBack && (
        <button type="button" className="bug-back" onClick={onBack}>
          <ChevronLeft size={14} aria-hidden="true" />
          One of {sameError.bugs.length} scenarios with this error
        </button>
      )}
      <div className="bug-detail-head">
        <div className="bug-detail-crumbs">
          <span className="bug-detail-set"><Folder size={13} /> {bug.suite_name || NO_SET}</span>
          {bug.case_idx != null && <span className="bug-detail-idx">#{bug.case_idx}</span>}
        </div>
        <h2 className="bug-detail-title">{bug.title}</h2>
        {scenario && (
          <p className="bug-detail-scenario">
            <ListChecks size={14} aria-hidden="true" />
            <span>{scenario}</span>
          </p>
        )}
      </div>

      <div className="bug-actions">
        <span className={`bug-dot s-${bug.status}`} aria-hidden="true" />
        <select
          value={bug.status}
          onChange={(event) => onStatus(bug, event.target.value)}
          aria-label="Bug status"
        >
          {(meta.statuses || []).map((value) => (
            <option key={value} value={value}>{STATUS_LABEL[value] || value}</option>
          ))}
        </select>
        {bug.severity && (
          <span className={`priority-tag p-${bug.severity.toLowerCase()}`}>
            {severityLabel(bug.severity)}
          </span>
        )}
        {parts.autoRaised && (
          <span className="bug-auto" title="Filed by the run when the scenario failed, before anybody had read it.">
            Auto-raised
          </span>
        )}
        <span className="muted small">{when(bug.created_at)}</span>
        <span className="bug-actions-gap" />
        {onOpenRun && bug.run_id && (
          <button className="btn btn-ghost btn-sm" onClick={() => onOpenRun(bug.run_id)}>
            <ExternalLink size={14} /> Open the run
          </button>
        )}
        <button className="btn btn-danger btn-sm" onClick={() => onDelete(bug)}>
          <Trash2 size={14} /> Delete
        </button>
      </div>

      {(parts.notes || []).map((note) => (
        <p key={note} className="bug-callout">
          <AlertTriangle size={14} aria-hidden="true" />
          <span>{note}</span>
        </p>
      ))}

      {(facts.length > 0 || bug.note) && (
        <dl className="facts">
          {bug.note && (
            <div className="fact">
              <dt className="fact-label tone-accent">Note</dt>
              <dd className="fact-value">{bug.note}</dd>
            </div>
          )}
          {facts.map((fact) => (
            <div className="fact" key={fact.key}>
              <dt className={`fact-label tone-${fact.tone}`}>{fact.label}</dt>
              <dd className="fact-value">
                <FactValue fact={fact} value={fields[fact.key]} bug={bug} meta={meta} />
              </dd>
            </div>
          ))}
        </dl>
      )}

      {steps.length > 0 && (
        <section className="bug-section">
          <h3 className="bug-section-title">Steps to reproduce</h3>
          <ol className="bug-steps">
            {steps.map((step) => (
              <li key={step.idx} className={`bug-step ${step.failed ? 'failed' : ''}`}>
                <span className="bug-step-idx">{step.idx}</span>
                <div className="bug-step-body">
                  <span className="bug-step-action">{step.action}</span>
                  {step.expected && (
                    <span className="bug-step-expected">
                      <span className="bug-step-expected-label">Expected</span> {step.expected}
                    </span>
                  )}
                </div>
                <span className="visually-hidden">{step.failed ? 'failed' : 'passed'}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {/* Folded: they are the evidence for a bug about a 404, and noise on
          every other one — the page answers a dozen 4xx on a good day. */}
      {events.map((section) => (
        <details key={section.label} className={`bug-events level-${section.level}`}>
          <summary>
            <ChevronRight size={14} className="bug-events-chevron" aria-hidden="true" />
            <span className="bug-events-title">
              {section.level === 'error' ? 'Page errors' : 'Page warnings'}
            </span>
            <span className="tab-count">{section.count || section.items.length}</span>
            {section.level !== 'error' && (
              <span className="muted small">They do not decide the verdict</span>
            )}
          </summary>
          <ul>
            {section.items.map((item, index) => (
              <li key={`${index}-${item.text}`} className="bug-event">
                <span className="bug-event-kind">{item.kind}</span>
                <span className="bug-event-text">{item.text}</span>
                {item.url && <span className="bug-event-url">{item.url}</span>}
              </li>
            ))}
            {section.more && <li className="bug-event muted small">{section.more}</li>}
          </ul>
        </details>
      ))}

      {/* Anything the parts above did not recognise — a bug typed by hand, or
          a body edited out of shape — is shown whole rather than dropped. */}
      {parts.rest && <p className="bug-rest">{parts.rest}</p>}

      {bug.hasScreenshot && <BugShot key={bug.id} bugId={bug.id} />}

      {parts.runId && <p className="bug-foot">Raised from run {parts.runId}</p>}
    </>
  );
}

export function BugsPage({ onOpenRun = null, initialPlatform = null,
                           initialOs = null }) {
  const toast = useToast();
  const [bugs, setBugs] = useState([]);
  const [counts, setCounts] = useState({});
  const [meta, setMeta] = useState({ codes: {}, notAppDefects: [], statuses: [] });
  const [status, setStatus] = useState('');
  const [search, setSearch] = useState('');
  // A bug's id, or the key of a row standing for several bugs with one error.
  const [selectedKey, setSelectedKey] = useState(null);
  const [loading, setLoading] = useState(true);
  // Groups the tester opened or closed. The rest follow the default: the
  // newest set open, the others folded to one line each.
  const [folded, setFolded] = useState({});
  /* The platform is above the status filter, not beside it: a web bug and a
     mobile bug are fixed by different people in different code, so "everything
     open" is a question worth asking one platform at a time. The status counts
     below are recounted within it for the same reason. */
  const [platform, setPlatform] = useState(initialPlatform || DEFAULT_PLATFORM);
  const [platformCounts, setPlatformCounts] = useState(null);
  /* And within Mobile, which phone — for the same reason, one step down. An
     iOS bug and an Android one are different code and usually different
     people. Which phone is read off the run that raised the bug; one filed by
     hand has no run and stays on both. */
  /* Opens on the phone the caller names, when one sent us here. Landing on
     iOS regardless was how pressing Bug on an Android scenario produced three
     unrelated iOS bugs and an empty detail pane. */
  const [os, setOs] = useState(initialOs || 'ios');
  const [osCounts, setOsCounts] = useState(null);

  // Bumped to re-read after a change of our own. The fetch itself lives in the
  // effect rather than in a callback the effect calls, so nothing sets state
  // while the effect body is still running.
  const [reload, setReload] = useState(0);
  const refresh = useCallback(() => setReload((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.bugs({
          status: status || null, search: search || null, kind: platform,
          os: platform === 'mobile' ? os : null,
        });
        if (cancelled) return;
        setBugs(data.bugs || []);
        setCounts(data.counts || {});
        setPlatformCounts(data.platformCounts || null);
        setOsCounts(data.osCounts || null);
        setMeta({
          codes: data.codes || {},
          notAppDefects: data.notAppDefects || [],
          statuses: data.statuses || [],
        });
      } catch (err) {
        if (!cancelled) toast.error(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [status, search, platform, os, reload, toast]);

  const groups = useMemo(() => groupBugs(bugs), [bugs]);
  const rows = groups.flatMap((group) => group.rows);
  // A row of several is read as the error they share; one of its bugs, opened
  // from it, is read on its own with the way back to the others.
  const selectedRow = rows.find((row) => row.many && row.key === selectedKey) || null;
  const selected = selectedRow ? null : bugs.find((b) => b.id === selectedKey) || null;
  const sameError = selected
    ? rows.find((row) => row.many && row.bugs.some((bug) => bug.id === selected.id)) || null
    : null;

  /* Open by default: the newest set, and the one holding the bug being read.
     While searching, every set that matched — a match folded out of sight is
     a match not found. */
  const isOpen = (group, index) => {
    if (search) return true;
    if (group.key in folded) return !folded[group.key];
    return index === 0 || group.rows.some((row) => holds(row, selectedKey));
  };
  const toggle = (group, open) => setFolded((current) => ({ ...current, [group.key]: open }));

  /* A search box and a filter row are for narrowing something. Before the
     first bug is raised they narrow nothing, so a first visit is the empty
     state and the sentence saying where bugs come from, not two controls over
     blank space. They come back the moment there is a list — or the moment a
     filter is what emptied it, so nobody is left holding a filter they cannot
     clear. */
  const showTools = loading || bugs.length > 0 || Boolean(status || search);

  const setBugStatus = async (bug, next) => {
    try {
      await api.updateBug(bug.id, { status: next });
      refresh();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const remove = async (bug) => {
    try {
      await api.deleteBug(bug.id);
      setSelectedKey((current) => (current === bug.id ? null : current));
      refresh();
      toast.success('Bug deleted.');
    } catch (err) {
      toast.error(err.message);
    }
  };

  // Said once for the error, done for every bug that carries it.
  const setRowStatus = async (row, next) => {
    try {
      await Promise.all(row.bugs.filter((bug) => bug.status !== next)
        .map((bug) => api.updateBug(bug.id, { status: next })));
    } catch (err) {
      toast.error(err.message);
    } finally {
      refresh();
    }
  };

  const removeRow = async (row) => {
    if (!window.confirm(`Delete all ${row.bugs.length} bugs with this error? This cannot be undone.`)) {
      return;
    }
    try {
      await Promise.all(row.bugs.map((bug) => api.deleteBug(bug.id)));
      setSelectedKey(null);
      toast.success(`${row.bugs.length} bugs deleted.`);
    } catch (err) {
      toast.error(err.message);
    } finally {
      refresh();
    }
  };

  return (
    <main className="page">
      <header className="page-header">
        <div>
          <h1 className="page-title">Bug Report</h1>
          <p className="page-subtitle">
            What the failures turned out to mean, grouped by the Test Set that
            found them. Kept after the run that found them is gone.
          </p>
        </div>
        <PlatformTabs value={platform} onChange={setPlatform} counts={platformCounts} />
        {platform === 'mobile' && (
          <PlatformTabs
            options={OS_TABS} value={os} onChange={setOs}
            counts={loading ? null : osCounts} sub
          />
        )}
      </header>

      {/* Both ways of narrowing the list stand together over the list itself:
          the text you half remember and the state you care about are the same
          question asked twice, and neither belongs up in the title row. */}
      {showTools && (
        <div className="bug-tools">
          <div className="bug-search">
            <Search size={14} />
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search title, body or scenario"
              aria-label="Search bugs"
            />
            {/* The chips count by status within the platform and know nothing
                of what has been typed, so while a search is running this is
                the only number on the row that describes the rows on screen. */}
            {search && !loading && (
              <span className="muted small bug-match-count">
                {bugs.length} match{bugs.length === 1 ? '' : 'es'}
              </span>
            )}
          </div>

          {/* Every chip carries its count except All, whose total is the number
              already on the platform tab a row above — the same figure by the
              way it is counted, not merely a close one. How that total splits,
              twelve open of the thirty-seven that tab claims, is said nowhere
              else, so those counts stay. */}
          <div className="segmented">
            {[['', 'All'], ...(meta.statuses || []).map((s) => [s, STATUS_LABEL[s] || s])]
              .map(([value, label]) => (
                <button
                  key={value || 'all'}
                  className={status === value ? 'active' : ''}
                  onClick={() => setStatus(value)}
                >
                  {label}
                  {value ? <span className="tab-count">{counts[value] || 0}</span> : null}
                </button>
              ))}
          </div>
        </div>
      )}

      {loading ? (
        <p className="loading-panel">
          <Loader2 size={16} className="spin" aria-hidden="true" />
          Loading…
        </p>
      ) : bugs.length === 0 ? (
        <div className="empty-state card">
          <Bug size={30} />
          <h3>{status || search ? 'Nothing matches that' : 'No bugs raised yet'}</h3>
          <p>
            {status || search
              ? 'Clear the filter to see the rest.'
              : 'Open a failed scenario on Test Executions and raise one — the title, '
                + 'the steps to reproduce and the screen are written for you from the run.'}
          </p>
        </div>
      ) : (
        <div className="bugs-layout">
          <div className="bug-groups">
            {groups.map((group, index) => {
              const open = isOpen(group, index);
              const listId = `bug-group-${index}`;
              return (
                <section key={group.key} className={`bug-group ${open ? 'open' : ''}`}>
                  <button
                    type="button"
                    className="bug-group-head"
                    aria-expanded={open}
                    aria-controls={listId}
                    onClick={() => toggle(group, open)}
                  >
                    <ChevronRight size={15} className="bug-group-chevron" aria-hidden="true" />
                    <span className="bug-group-name" title={group.name}>{group.name}</span>
                    <span className="bug-group-counts">
                      {group.open > 0 && (
                        <span className="bug-group-open">{group.open} open</span>
                      )}
                      <span className="tab-count" aria-label={`${group.bugs.length} bugs`}>
                        {group.bugs.length}
                      </span>
                    </span>
                  </button>
                  {open && (
                    <ul className="bug-list" id={listId}>
                      {group.rows.map((row) => {
                        const { lead } = row;
                        const shared = {
                          active: holds(row, selectedKey),
                          notApp: (meta.notAppDefects || []).includes(lead.code),
                          codeText: meta.codes?.[lead.code] || lead.code,
                          onSelect: () => setSelectedKey(row.key),
                        };
                        return (
                          <li key={row.key}>
                            {row.many
                              ? <SameErrorRow row={row} {...shared} />
                              : <BugRow bug={lead} {...shared} />}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </section>
              );
            })}
          </div>

          <section className="card bug-detail">
            {selectedRow ? (
              <SameErrorDetail
                row={selectedRow}
                meta={meta}
                onStatus={setRowStatus}
                onDelete={removeRow}
                onPick={setSelectedKey}
              />
            ) : selected ? (
              <BugDetail
                key={selected.id}
                bug={selected}
                meta={meta}
                onOpenRun={onOpenRun}
                onStatus={setBugStatus}
                onDelete={remove}
                sameError={sameError}
                onBack={sameError ? () => setSelectedKey(sameError.key) : null}
              />
            ) : (
              <div className="empty-panel">
                <Bug size={24} />
                <p>Pick a bug to read it.</p>
              </div>
            )}
          </section>
        </div>
      )}
    </main>
  );
}
