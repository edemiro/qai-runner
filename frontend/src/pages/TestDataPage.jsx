import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import {
  Copy, Eye, EyeOff, KeyRound, Loader2, Pencil, Plus, ScanSearch, Search, Trash2,
} from 'lucide-react';

import { api } from '../api';
import { EmptyState } from '../components/EmptyState';
import { useToast } from '../hooks/useToast';
import './test-data.css';

/**
 * Test Data — the values every scenario reaches by name.
 *
 * A test card, a test account, a phone number used to be typed into each
 * scenario that needed them, so a card expiring meant reading every Test Set
 * and whichever one was missed failed later for a reason nobody connected to
 * the change. They live here now, and a scenario names one instead of carrying
 * it: `{{kart.visa.numara}}` in a step is filled in as the run starts.
 *
 * Secrets are listed masked and never fetched in bulk: the real value is asked
 * for one entry at a time, when someone presses for it.
 */

// The shape the runner fills, kept the same here so what this page finds is
// what a run would resolve — a name may carry dots and dashes.
const PLACEHOLDER = /\{\{\s*([\w.-]+)\s*\}\}/g;

// Every full-width row in the table spans exactly this. A colSpan that
// disagrees with the header is how a row silently stops lining up, so the
// count lives in one place rather than being retyped at each of them.
const COLUMNS = 5;

// Below this the whole store is read at a glance and a search box would be a
// control that narrows nothing.
const SEARCH_FROM = 8;

function when(seconds) {
  if (!seconds) return '—';
  return new Date(seconds * 1000).toLocaleString('tr-TR', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

/** Every `{{name}}` written in the given texts. */
function namesIn(texts) {
  const names = new Set();
  for (const text of texts) {
    if (typeof text !== 'string' || !text) continue;
    for (const match of text.matchAll(PLACEHOLDER)) names.add(match[1]);
  }
  return names;
}

export function TestDataPage() {
  const toast = useToast();

  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');

  /* Values somebody has asked to see, by key. Only ever filled from the
     single-entry call: what the listing carries for a secret is the mask. */
  const [revealed, setRevealed] = useState({});
  const [revealing, setRevealing] = useState(null);

  /* The one open form, whether it is adding or editing. Only one can be open
     at a time, so one piece of state describes both and the row being edited
     is replaced by the form rather than sitting above a copy of itself. */
  const [form, setForm] = useState(null);
  const [formError, setFormError] = useState(null);
  const [saving, setSaving] = useState(false);

  // Where each name is written, read out of the Test Sets on request.
  const [usage, setUsage] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [openUsage, setOpenUsage] = useState(null);

  /* Bumped to re-read after a change of our own. The fetch lives inside the
     effect rather than in a callback it calls, so nothing sets state while the
     effect body is still running. */
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.testData();
        if (!cancelled) setEntries(data.entries || []);
      } catch (err) {
        if (!cancelled) toast.error(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [reload, toast]);

  const editingKey = form?.mode === 'edit' ? form.key : null;

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return entries;
    /* A secret matches on the four digits its mask keeps — which is how
       someone looking for a particular card recognises it here anyway. The row
       being edited stays whatever the search says: a form that disappears
       mid-edit takes the edit with it and leaves nothing to save. */
    return entries.filter((entry) =>
      entry.key === editingKey
      || entry.key.toLowerCase().includes(needle)
      || (entry.note || '').toLowerCase().includes(needle)
      || String(entry.value || '').toLowerCase().includes(needle));
  }, [entries, query, editingKey]);

  /* Names the Test Sets ask for that this store cannot answer. Derived from
     the scan rather than stored with it, so adding the missing entry strikes
     it off the list without another read of every set. */
  const unresolved = useMemo(() => {
    if (!usage) return [];
    const known = new Set(entries.map((entry) => entry.key));
    return Object.keys(usage.found).filter((name) => !known.has(name)).sort();
  }, [usage, entries]);

  const copyKey = (key) => {
    navigator.clipboard.writeText(`{{${key}}}`);
    // The braces go with it: what gets pasted into a step is the placeholder,
    // not the bare name.
    toast.success(`{{${key}}} copied`);
  };

  const toggleReveal = async (entry) => {
    if (entry.key in revealed) {
      setRevealed((current) => {
        const next = { ...current };
        delete next[entry.key];
        return next;
      });
      return;
    }
    setRevealing(entry.key);
    try {
      const data = await api.testDataValue(entry.key);
      setRevealed((current) => ({ ...current, [entry.key]: data.value }));
    } catch (err) {
      toast.error(err.message);
    } finally {
      setRevealing(null);
    }
  };

  /* Which scenarios name each value. There is no endpoint that answers this,
     but a Test Set carries its scenarios and a scenario carries its text, so
     it is read the same way a person would: every set, every case, every place
     the runner substitutes. That is a request per Test Set, which is why it is
     asked for rather than done on arrival. */
  const scanUsage = useCallback(async () => {
    setScanning(true);
    try {
      const { suites = [] } = await api.suites();
      if (!suites.length) {
        toast.info('There are no Test Sets yet, so nothing names these values.');
        return;
      }
      const loaded = await Promise.all(
        suites.map((suite) => api.suite(suite.id).catch(() => null)),
      );
      const found = {};
      for (const suite of loaded) {
        if (!suite) continue;
        for (const item of suite.cases || []) {
          /* A case's own dataset column answers a name before the store does,
             so a case carrying one is not reading this value at all and would
             be a wrong answer to "what changes if I edit this". */
          const columns = new Set(
            (Array.isArray(item.dataset) ? item.dataset : [])
              .flatMap((row) => Object.keys(row || {})),
          );
          const names = namesIn([
            item.goal, item.precondition, item.url,
            ...(item.steps || []).flatMap((step) => [step.action, step.expected]),
          ]);
          for (const name of names) {
            if (columns.has(name)) continue;
            if (!found[name]) found[name] = [];
            found[name].push({ suite: suite.name, scenario: item.name });
          }
        }
      }
      setUsage({ found });
    } catch (err) {
      toast.error(err.message);
    } finally {
      setScanning(false);
    }
  }, [toast]);

  const startAdd = (key = '') => {
    setFormError(null);
    setOpenUsage(null);
    setForm({ mode: 'new', key, value: '', note: '', secret: false, wasSecret: false });
  };

  const startEdit = (entry) => {
    setFormError(null);
    setForm({
      mode: 'edit',
      key: entry.key,
      /* A secret's listed value is the mask, and saving that back would write
         •••• over the card number. The field starts empty — unless it has
         already been revealed, in which case the real value is on screen and
         hiding it from the editor helps nobody. */
      value: entry.secret ? (revealed[entry.key] ?? '') : (entry.value ?? ''),
      note: entry.note || '',
      secret: entry.secret,
      wasSecret: entry.secret,
    });
  };

  const cancelForm = () => {
    setForm(null);
    setFormError(null);
  };

  const save = async () => {
    const key = form.key.trim();
    if (!key) {
      setFormError('Give it a name — this is what a scenario writes between braces.');
      return;
    }
    if (form.mode === 'new' && entries.some((entry) => entry.key === key)) {
      setFormError(`There is already an entry called ${key}. Edit that one — `
        + 'saving here would replace it for every scenario that names it.');
      return;
    }
    /* Saving replaces the whole entry, so changing only the note on a secret
       still has to send a value with it — and the one on screen is the mask.
       Blank means "keep what is stored", which is fetched at the last moment
       rather than held in the page. */
    const keepCurrent = form.mode === 'edit' && form.wasSecret && form.value === '';
    if (!keepCurrent && !form.value.trim()) {
      setFormError('A value is needed — this is what the runner types in.');
      return;
    }

    setSaving(true);
    setFormError(null);
    try {
      const value = keepCurrent ? (await api.testDataValue(key)).value : form.value;
      await api.saveTestData(key, value, form.note.trim() || null, form.secret);
      const added = form.mode === 'new';
      setForm(null);
      setRevealed((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
      // A new row hidden behind a filter reads as nothing having happened.
      if (added) setQuery('');
      setReload((n) => n + 1);
      toast.success(added ? `{{${key}}} added.` : `{{${key}}} saved.`);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  };

  const remove = async (entry) => {
    const uses = usage?.found?.[entry.key]?.length || 0;
    /* Asked first, because one value stands behind many scenarios: deleting it
       does not fail here, it fails later in whatever names it. */
    const confirmed = window.confirm(
      `Delete ${entry.key}?`
      + (uses
        ? `\n\n${uses} scenario${uses === 1 ? '' : 's'} name${uses === 1 ? 's' : ''} it, `
          + 'and would run with the placeholder typed out instead of a value.'
        : '')
      + '\n\nThis cannot be undone.',
    );
    if (!confirmed) return;
    try {
      await api.deleteTestData(entry.key);
      if (form?.key === entry.key) setForm(null);
      // Dropped rather than left behind: a later entry under the same name
      // would otherwise be shown the value of the one that was deleted.
      setRevealed((current) => {
        const next = { ...current };
        delete next[entry.key];
        return next;
      });
      setReload((n) => n + 1);
      toast.success(`{{${entry.key}}} deleted.`);
    } catch (err) {
      toast.error(err.message);
    }
  };

  /* The same fields whether a value is being added or changed. Written as
     plain JSX rather than a component so that editing a row and adding one do
     not drift apart, and so the inputs keep focus between renders. */
  const formBody = () => (
    <div className="td-form">
      {form.mode === 'new' ? (
        <label className="td-field td-field-key">
          <span className="td-field-label">Key</span>
          <input
            className="td-input mono"
            value={form.key}
            onChange={(event) => setForm({ ...form, key: event.target.value })}
            placeholder="kart.visa.numara"
            autoFocus
          />
        </label>
      ) : (
        /* The form stands in place of the row, so without this there is
           nothing on screen saying which entry is being changed. Fixed, not a
           field: the key is the identity every scenario names, and typing a
           new one here would leave the old entry behind rather than rename
           it. */
        <span className="td-form-key mono">{`{{${form.key}}}`}</span>
      )}
      <label className="td-field td-field-value">
        <span className="td-field-label">Value</span>
        <input
          className="td-input mono"
          value={form.value}
          onChange={(event) => setForm({ ...form, value: event.target.value })}
          placeholder={form.wasSecret ? 'Leave blank to keep the current value' : '4111 1111 1111 1111'}
          autoFocus={form.mode === 'edit'}
        />
      </label>
      <label className="td-field td-field-note">
        <span className="td-field-label">Note</span>
        <input
          className="td-input"
          value={form.note}
          onChange={(event) => setForm({ ...form, note: event.target.value })}
          placeholder="What it is for, or when it expires"
        />
      </label>
      <label className="td-field-check">
        <input
          type="checkbox"
          className="td-check"
          checked={form.secret}
          onChange={(event) => setForm({ ...form, secret: event.target.checked })}
        />
        <span>Secret</span>
      </label>
      <div className="td-form-actions">
        <button className="btn btn-ghost btn-sm" onClick={cancelForm} disabled={saving}>
          Cancel
        </button>
        <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>
          {saving && <Loader2 size={14} className="spin" />}
          {form.mode === 'new' ? 'Add' : 'Save'}
        </button>
      </div>
      {formError && <p className="td-form-error">{formError}</p>}
    </div>
  );

  const adding = form?.mode === 'new';

  return (
    <main className="page test-data-page">
      <header className="page-header">
        <div>
          <h1 className="page-title">Test Data</h1>
          <p className="page-subtitle">
            The values scenarios reach by name. Change one here and every
            scenario that names it changes with it.
          </p>
        </div>
        {/* Reading every Test Set costs a request each, so the button holds
            itself shut until the answer is in rather than letting a second
            press start the whole sweep again. It sits in the heading because it
            asks something of the whole store; the row below narrows it. */}
        {!loading && (
          <button className="btn btn-ghost btn-sm" onClick={scanUsage} disabled={scanning}>
            {scanning ? <Loader2 size={14} className="spin" /> : <ScanSearch size={14} />}
            {scanning
              ? 'Reading the Test Sets…'
              : usage ? 'Check the Test Sets again' : 'Where are these used?'}
          </button>
        )}
      </header>

      {!loading && (entries.length >= SEARCH_FROM || query) && (
        <div className="td-tools">
          <label className="td-search">
            <Search size={14} />
            <input
              type="search"
              className="td-search-input"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search a key, a note, the last four digits"
              aria-label="Search test data"
            />
          </label>
          {query && (
            <span className="muted small">
              {shown.length} match{shown.length === 1 ? '' : 'es'}
            </span>
          )}
        </div>
      )}

      {usage && unresolved.length > 0 && (
        <div className="td-unresolved">
          <p className="td-unresolved-text">
            {unresolved.length === 1
              ? 'One name the scenarios ask for has no value here.'
              : `${unresolved.length} names the scenarios ask for have no value here.`}
            {' '}A run types them out as written instead of filling them in.
          </p>
          <div className="td-unresolved-keys">
            {unresolved.map((name) => (
              <button
                key={name}
                className="td-missing-key"
                onClick={() => startAdd(name)}
                title={`Add a value for ${name}`}
              >
                <span className="mono">{`{{${name}}}`}</span>
                <Plus size={12} />
              </button>
            ))}
          </div>
        </div>
      )}

      <section className="card">
        {loading ? (
          <p className="muted td-loading">Loading…</p>
        ) : shown.length === 0 ? (
          query ? (
            <EmptyState
              icon={Search}
              title="Nothing matches that"
              compact
              action={(
                <button className="btn btn-ghost btn-sm" onClick={() => setQuery('')}>
                  Clear the search
                </button>
              )}
            >
              The rest of the store is still here.
            </EmptyState>
          ) : (
            <EmptyState icon={KeyRound} title="No shared values yet">
              A scenario writes <code>{'{{kart.visa.numara}}'}</code> in a step and the
              run fills it from here, so a card that expires is changed once
              instead of hunted through every Test Set.
            </EmptyState>
          )
        ) : (
          <div className="td-table-wrap">
            <table className="data-table td-table">
              <thead>
                <tr>
                  <th>Key</th>
                  <th>Value</th>
                  <th>Note</th>
                  <th>Changed</th>
                  <th><span className="td-sr">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {shown.map((entry) => {
                  if (form?.mode === 'edit' && form.key === entry.key) {
                    return (
                      <tr key={entry.key} className="td-form-row">
                        <td colSpan={COLUMNS}>{formBody()}</td>
                      </tr>
                    );
                  }
                  const uses = usage?.found?.[entry.key] || [];
                  const isRevealed = entry.key in revealed;
                  return (
                    <Fragment key={entry.key}>
                      <tr>
                        <td className="td-cell-key">
                          <button
                            className="td-key"
                            onClick={() => copyKey(entry.key)}
                            title={`Copy {{${entry.key}}}`}
                          >
                            <span className="td-key-text">{`{{${entry.key}}}`}</span>
                            <Copy size={12} />
                          </button>
                          {usage && (
                            uses.length === 0 ? (
                              <span className="td-uses muted small">No scenario names it</span>
                            ) : (
                              <button
                                className="td-uses td-uses-link"
                                onClick={() => setOpenUsage(
                                  (current) => (current === entry.key ? null : entry.key),
                                )}
                              >
                                {uses.length} scenario{uses.length === 1 ? '' : 's'}
                              </button>
                            )
                          )}
                        </td>
                        <td className="td-cell-value">
                          <span className="td-value-wrap">
                            <span className="td-value">
                              {isRevealed ? revealed[entry.key] : entry.value}
                            </span>
                            {entry.secret && (
                              <button
                                className="icon-btn-tiny"
                                onClick={() => toggleReveal(entry)}
                                disabled={revealing === entry.key}
                                title={isRevealed ? 'Hide it again' : 'Show the real value'}
                                aria-label={isRevealed ? 'Hide the value' : 'Show the real value'}
                              >
                                {revealing === entry.key
                                  ? <Loader2 size={13} className="spin" />
                                  : isRevealed ? <EyeOff size={13} /> : <Eye size={13} />}
                              </button>
                            )}
                          </span>
                        </td>
                        <td className="td-cell-note">
                          {entry.note || <span className="td-dash">—</span>}
                        </td>
                        <td className="td-cell-when">{when(entry.updatedAt)}</td>
                        <td className="td-cell-actions">
                          <span className="td-row-actions">
                            <button
                              className="icon-btn-tiny"
                              onClick={() => startEdit(entry)}
                              title={`Edit ${entry.key}`}
                              aria-label={`Edit ${entry.key}`}
                            >
                              <Pencil size={14} />
                            </button>
                            <button
                              className="icon-btn-tiny td-danger"
                              onClick={() => remove(entry)}
                              title={`Delete ${entry.key}`}
                              aria-label={`Delete ${entry.key}`}
                            >
                              <Trash2 size={14} />
                            </button>
                          </span>
                        </td>
                      </tr>
                      {openUsage === entry.key && uses.length > 0 && (
                        <tr className="td-usage-row">
                          <td colSpan={COLUMNS}>
                            <ul className="td-usage-list">
                              {uses.map((use, index) => (
                                <li key={`${use.suite}-${use.scenario}-${index}`}>
                                  <span className="td-usage-suite">{use.suite}</span>
                                  <span className="td-usage-sep">›</span>
                                  {use.scenario}
                                </li>
                              ))}
                            </ul>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Adding comes after the list, because the list is what the page is
            for; the form is a row that appears when it is asked for. */}
        <div className="td-add">
          {adding ? formBody() : (
            <button className="btn btn-ghost btn-sm" onClick={() => startAdd()}>
              <Plus size={14} />
              Add an entry
            </button>
          )}
        </div>
      </section>

      <details className="td-fold">
        <summary>How a scenario reaches these</summary>
        <div className="td-fold-body">
          <p>
            Write the key between double braces in a step, a goal, a precondition
            or the scenario&apos;s address: <code>{'{{kart.visa.numara}}'}</code>. The
            run fills it in as it starts, so the scenario never carries the value
            itself.
          </p>
          <p>
            A scenario&apos;s own dataset row wins over this store, so a case that
            runs once per row can answer a name for itself without changing it
            for everybody.
          </p>
          <p>
            A name nothing answers is left exactly as written, so a typo arrives
            in the report as <code>{'{{emial}}'}</code> rather than quietly
            becoming an empty field.
          </p>
        </div>
      </details>
    </main>
  );
}
