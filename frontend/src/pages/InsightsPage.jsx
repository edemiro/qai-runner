import { useEffect, useMemo, useState } from 'react';
import { Activity, AlertTriangle, Coins, Search, ShieldAlert, TrendingUp } from 'lucide-react';

import { api } from '../api';
import { DEFAULT_PLATFORM, PlatformTabs } from '../components/PlatformTabs';
import { useToast } from '../hooks/useToast';
import { formatTokens } from '../lib/format';
import { OS_TABS } from '../lib/platforms';
import './insights.css';

/* Above this share the verdict is changing often enough to be the reason a
   case cannot be trusted rather than a run or two of noise. The rows mark it
   and the chip collects it, so both read the line from here. */
const FLAKY_HIGH = 0.3;

/* Twenty rows run past the fold; ten do not, and a search box over a list that
   is already in view is furniture. */
const FLAKY_TOOLS_MIN = 10;

/* The two states that start work, and both are already marked in the rows: a
   verdict that keeps changing, and one that is red as of the last run. */
const FLAKY_FILTERS = [
  { id: 'all', label: 'All', match: () => true },
  { id: 'flipping', label: 'Flipping', match: (row) => row.flakiness > FLAKY_HIGH },
  { id: 'red', label: 'Failing now', match: (row) => row.last_status === 'failed' },
];

/**
 * A suite's history is only useful if it answers three questions: is it getting
 * better or worse, is the damage where it matters, and which cases cannot be
 * trusted. Everything here serves one of those.
 *
 * What the runs cost the model is the exception, and it comes last for that
 * reason: it is real and it has a ceiling worth watching, but it is a fact
 * about the bill rather than about the suite, and a page that opens on it
 * answers none of the three.
 */
export function InsightsPage() {
  const toast = useToast();
  const [trend, setTrend] = useState([]);
  const [flaky, setFlaky] = useState([]);
  const [breakdown, setBreakdown] = useState([]);
  const [usage, setUsage] = useState(null);
  const [budget, setBudget] = useState(null);
  const [days, setDays] = useState(14);
  /* Read one platform at a time, like every other page. Averaged together, a
     mature web suite and a handful of mobile runs produce a pass rate that is
     true of neither — and the flaky table would rank cases from both against
     each other as though they shared a device, a driver and a failure mode. */
  const [platform, setPlatform] = useState(DEFAULT_PLATFORM);
  const [platformCounts, setPlatformCounts] = useState(null);
  /* And, on Mobile, which phone. "Which of my scenarios are flaky" is a
     different question on an iPhone than on a Pixel — different selectors,
     different gestures, different app — and ranking them in one table against
     each other answers neither. */
  const [os, setOs] = useState('ios');
  const [osCounts, setOsCounts] = useState(null);
  const [loading, setLoading] = useState(true);
  const [flakySearch, setFlakySearch] = useState('');
  const [flakyFilter, setFlakyFilter] = useState('all');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // Awaited first so nothing sets state during the same commit that
      // scheduled this effect.
      const phone = platform === 'mobile' ? os : null;
      const [trendData, flakyData, priorityData, usageData, budgetData] = await Promise.all([
        api.trend(days, platform, phone).catch((err) => ({ error: err })),
        api.flaky(20, platform, phone).catch((err) => ({ error: err })),
        api.priorityBreakdown(days, platform, phone).catch((err) => ({ error: err })),
        api.usage(days, platform, phone).catch((err) => ({ error: err })),
        // The gateway may be unreachable or not offer this; the rest of the
        // page must not fail with it, so its error stays local.
        api.budget().catch(() => null),
      ]);
      if (!cancelled) setBudget(budgetData);
      if (cancelled) return;
      const failure = trendData.error || flakyData.error || priorityData.error || usageData.error;
      if (failure) toast.error(failure.message);
      if (trendData.trend) setTrend(trendData.trend);
      if (trendData.counts) setPlatformCounts(trendData.counts);
      if (trendData.osCounts) setOsCounts(trendData.osCounts);
      if (flakyData.flaky) setFlaky(flakyData.flaky);
      if (priorityData.breakdown) setBreakdown(priorityData.breakdown);
      if (usageData.usage) setUsage(usageData.usage);
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [days, platform, os, toast]);

  /* A different platform or phone is a different set of cases, so whatever was
     typed against the last one is dropped with it: kept, it would hide rows
     for a reason no longer on screen — an empty table that looks like good
     news. */
  const clearFlakyTools = () => {
    setFlakySearch('');
    setFlakyFilter('all');
  };

  const totals = useMemo(() => {
    const passed = trend.reduce((sum, day) => sum + day.passed, 0);
    const failed = trend.reduce((sum, day) => sum + day.failed, 0);
    const total = passed + failed;
    const durations = trend.filter((day) => day.avg_ms).map((day) => day.avg_ms);
    return {
      passed,
      failed,
      total,
      rate: total ? passed / total : null,
      avgMs: durations.length
        ? Math.round(durations.reduce((a, b) => a + b, 0) / durations.length)
        : null,
    };
  }, [trend]);

  const peak = useMemo(
    () => Math.max(1, ...trend.map((day) => day.total)),
    [trend],
  );

  /* Searched first, then filtered, so the count on each chip describes the
     rows the table is about to show rather than the whole report. A chip
     reading 12 over a table of 3 is worse than no count at all. */
  const searchedFlaky = useMemo(() => {
    const needle = flakySearch.trim().toLowerCase();
    if (!needle) return flaky;
    return flaky.filter(
      (row) => `${row.name} ${row.suite_name || ''}`.toLowerCase().includes(needle),
    );
  }, [flaky, flakySearch]);

  const visibleFlaky = useMemo(() => {
    const rule = FLAKY_FILTERS.find((item) => item.id === flakyFilter) || FLAKY_FILTERS[0];
    return searchedFlaky.filter(rule.match);
  }, [searchedFlaky, flakyFilter]);

  /* Spend is the one figure here with a ceiling, so it is worth drawing rather
     than listing. The gateway sends its own labels and formatting, which are
     shown verbatim — this only reads the two numbers back out to size the bar,
     and shows nothing if they do not parse. */
  const spend = useMemo(() => {
    if (!budget?.available) return null;
    const find = (label) => budget.items.find(
      (item) => item.label.toLowerCase() === label,
    )?.value;
    const num = (value) => {
      const parsed = parseFloat(String(value ?? '').replace(/[^0-9.]/g, ''));
      return Number.isFinite(parsed) ? parsed : null;
    };
    const used = num(find('spend'));
    const limit = num(find('budget'));
    if (used == null || limit == null || limit <= 0) return null;
    return {
      used, limit,
      share: Math.min(1, used / limit),
      usedLabel: find('spend'),
      limitLabel: find('budget'),
      period: find('period'),
    };
  }, [budget]);

  return (
    <main className="page">
      <header className="page-header">
        <div>
          <h1 className="page-title">Insights</h1>
          <p className="page-subtitle">What the run history says about the suite.</p>
        </div>
        <div className="segmented">
          {[7, 14, 30].map((value) => (
            <button
              key={value}
              className={days === value ? 'active' : ''}
              onClick={() => setDays(value)}
            >
              {value}d
            </button>
          ))}
        </div>
        <PlatformTabs
          value={platform}
          onChange={(next) => { setPlatform(next); clearFlakyTools(); }}
          counts={platformCounts}
        />
        {platform === 'mobile' && (
          <PlatformTabs
            options={OS_TABS} value={os}
            onChange={(next) => { setOs(next); clearFlakyTools(); }}
            counts={loading ? null : osCounts} sub
          />
        )}
      </header>

      {loading ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          {/* The volume rides with the rate rather than standing as a figure of
              its own. A pass rate with no denominator reads the same at 3 runs
              as at 300; the same total set beside it in a card of its own reads
              as a second measurement, and the window it covers is already the
              chosen tab above. */}
          <div className="stat-row">
            <div className="stat-card">
              <span className="stat-label">Pass rate</span>
              <span className={`stat-value ${totals.rate != null && totals.rate < 0.8 ? 'bad' : 'ok'}`}>
                {totals.rate == null ? '—' : `${Math.round(totals.rate * 100)}%`}
              </span>
              <span className="stat-note">
                {totals.total
                  ? `of ${totals.total} run${totals.total === 1 ? '' : 's'}`
                  : 'nothing run yet'}
              </span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Failed</span>
              <span className={`stat-value ${totals.failed ? 'bad' : ''}`}>{totals.failed}</span>
              <span className="stat-note">
                {totals.failed ? 'needs a look' : 'nothing outstanding'}
              </span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Avg duration</span>
              <span className="stat-value">
                {totals.avgMs == null ? '—' : `${(totals.avgMs / 1000).toFixed(1)}s`}
              </span>
              <span className="stat-note">per scenario</span>
            </div>
          </div>

          {/* Two narrow charts side by side rather than a full-width row each:
              on a desktop that read as a column of mostly empty boxes. */}
          <div className="insights-grid">
          <section className="card">
            <div className="card-head">
              <h2 className="card-title">
                <TrendingUp size={16} /> Pass / fail per day
              </h2>
            </div>

            {trend.length === 0 ? (
              <p className="muted small">No runs in this window yet.</p>
            ) : (
              <div className="trend-chart" role="img" aria-label="Pass and fail counts per day">
                {trend.map((day) => (
                  <div key={day.day} className="trend-col" title={
                    `${day.day}: ${day.passed} passed, ${day.failed} failed`
                  }>
                    <div className="trend-bars">
                      {/* Heights are a share of the busiest day, so a quiet day
                          reads as quiet rather than being rescaled to full. */}
                      <div
                        className="trend-bar failed"
                        style={{ height: `${(day.failed / peak) * 100}%` }}
                      />
                      <div
                        className="trend-bar passed"
                        style={{ height: `${(day.passed / peak) * 100}%` }}
                      />
                    </div>
                    <span className="trend-label">{day.day.slice(5)}</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="card">
            <div className="card-head stacked">
              <h2 className="card-title">
                <ShieldAlert size={16} /> Pass rate by priority
              </h2>
              <details className="insights-fold is-note">
                <summary>Why the bands are kept apart</summary>
                <div className="insights-fold-body">
                  <p className="muted small">
                    A pass rate on its own does not say whether the team is in trouble — the
                    same number is routine when the failures are Low and an emergency when
                    they are Critical.
                  </p>
                </div>
              </details>
            </div>

            {breakdown.every((band) => !band.total) ? (
              <p className="muted small">
                Nothing graded yet. Scenarios run from a Test Set carry a priority; runs
                started from the chat do not.
              </p>
            ) : (
              <ul className="priority-bars">
                {breakdown.filter((band) => band.total > 0).map((band) => (
                  <li key={band.priority ?? 'ungraded'}>
                    <span
                      className={`priority-tag p-${(band.priority || 'low').toLowerCase()}`}
                      title={band.priority ? undefined : 'Runs started from the chat, outside any Test Set'}
                    >
                      {band.priority || 'Ungraded'}
                    </span>
                    {/* The bar is the share and the count beside it is what the
                        share is of; the percentage is the same fact a third
                        time, so it waits on the hover. */}
                    <div
                      className="priority-bar"
                      title={band.pass_rate !== null ? `${band.pass_rate}% passed` : undefined}
                    >
                      <div
                        className="priority-bar-pass"
                        style={{ width: `${(band.passed / band.total) * 100}%` }}
                      />
                      <div
                        className="priority-bar-fail"
                        style={{ width: `${(band.failed / band.total) * 100}%` }}
                      />
                    </div>
                    <span className="priority-count">
                      {band.passed}/{band.total}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
          </div>

          <section className="card">
            <div className="card-head stacked">
              <h2 className="card-title">
                <AlertTriangle size={16} /> Flaky cases
              </h2>
              <p className="muted small">
                Ranked by how often the verdict changes, not by how often it fails —
                a case that always fails is broken, one that flips is untrustworthy.
                Each case is read over its last twenty runs, whenever they happened,
                so the window chosen above does not move these rows.
              </p>
            </div>

            {flaky.length === 0 ? (
              <p className="muted small">
                Nothing to report. A case needs at least two finished runs before
                its consistency can be judged.
              </p>
            ) : (
              <>
                {/* Kept on the full report rather than on what is left after a
                    search, so the toolbar does not disappear out from under the
                    hand that is using it. */}
                {flaky.length >= FLAKY_TOOLS_MIN && (
                  <div className="flaky-tools">
                    <div className="insights-search">
                      <Search size={14} />
                      <input
                        type="search"
                        value={flakySearch}
                        onChange={(event) => setFlakySearch(event.target.value)}
                        placeholder="Search by case or suite…"
                        aria-label="Search flaky cases"
                      />
                    </div>
                    <div className="filter-row">
                      {FLAKY_FILTERS.map((option) => (
                        <button
                          key={option.id}
                          type="button"
                          className={`filter-chip ${flakyFilter === option.id ? 'active' : ''}`}
                          onClick={() => setFlakyFilter(option.id)}
                        >
                          {option.label}
                          <span className="filter-count">
                            {searchedFlaky.filter(option.match).length}
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {visibleFlaky.length === 0 ? (
                  <p className="muted small">
                    None of the {flaky.length} cases here match the search and filter above.
                  </p>
                ) : (
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Case</th>
                        <th>Suite</th>
                        <th className="num">Runs</th>
                        <th className="num">Pass rate</th>
                        <th className="num">Flips</th>
                        <th>Last</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleFlaky.map((row) => (
                        <tr key={row.case_id}>
                          <td>{row.name}</td>
                          <td className="muted">{row.suite_name || '—'}</td>
                          <td className="num">{row.runs}</td>
                          <td className={`num ${row.pass_rate < 0.8 ? 'bad' : ''}`}>
                            {Math.round(row.pass_rate * 100)}%
                          </td>
                          <td className="num">
                            <span className={`flake-pip ${row.flakiness > FLAKY_HIGH ? 'high' : ''}`}>
                              {row.flips}
                            </span>
                          </td>
                          <td>
                            <span className={`badge ${row.last_status === 'passed' ? 'ok' : 'bad'}`}>
                              {row.last_status}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </>
            )}
          </section>

          {/* What QAi spent at the model. The quota left on the key is not
              QAi's to read — it belongs to whoever issues the key — but what
              the runs consumed is, and that is the half worth watching. */}
          <section className="card">
            <div className="card-head">
              <h2 className="card-title">
                <Coins size={16} /> Model usage
              </h2>
              <span className="muted small">
                {usage?.runs ? `${usage.runs} run${usage.runs === 1 ? '' : 's'} measured` : ''}
              </span>
            </div>

            {/* Straight from the gateway, which is the only place the figure
                exists — the key is the gateway's, not Anthropic's. Its own
                labels are shown verbatim rather than reworded here. */}
            {spend ? (
              <div className="spend-meter">
                <div className="spend-head">
                  <span className="stat-label">Spend this period</span>
                  <span className="spend-figures">
                    <strong>{spend.usedLabel}</strong>
                    <span className="muted"> of {spend.limitLabel}</span>
                    {spend.period && <span className="muted"> · {spend.period}</span>}
                  </span>
                </div>
                <div
                  className="spend-bar"
                  role="img"
                  aria-label={`${spend.usedLabel} of ${spend.limitLabel} spent`}
                >
                  <div
                    className={`spend-bar-fill ${spend.share > 0.85 ? 'hot' : ''}`}
                    style={{ width: `${spend.share * 100}%` }}
                  />
                </div>
              </div>
            ) : budget?.available ? (
              <ul className="budget-row">
                {budget.items.map((item) => (
                  <li key={item.label}>
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                  </li>
                ))}
              </ul>
            ) : null}

            {!usage || !usage.calls ? (
              <p className="muted small">
                Nothing measured in this window yet. Runs from here on record what
                they ask of the model; older runs predate the counter.
              </p>
            ) : (
              /* The total is the part anyone acts on — it is what moves the
                 meter above. How it divides between fresh input, output and
                 cache is accounting: true, occasionally needed to explain a
                 bill, and never a reason to do anything differently, so it
                 waits behind the line instead of taking four tiles. */
              <details className="insights-fold">
                <summary>
                  Tokens
                  <span className="muted small fold-aside">
                    {`${formatTokens(usage.total_tokens)} over ${usage.calls} model call${usage.calls === 1 ? '' : 's'}`}
                  </span>
                </summary>
                <div className="insights-fold-body">
                  <ul className="usage-breakdown">
                    <li><span>Input</span><strong>{formatTokens(usage.input_tokens)}</strong></li>
                    <li><span>Output</span><strong>{formatTokens(usage.output_tokens)}</strong></li>
                    <li>
                      <span>Cache read</span>
                      <strong className="ok">{formatTokens(usage.cache_read_tokens)}</strong>
                    </li>
                    <li><span>Cache write</span><strong>{formatTokens(usage.cache_write_tokens)}</strong></li>
                  </ul>
                  <p className="muted small">
                    Cache reads are billed at a fraction of fresh input, so they are
                    counted apart — a high cache-read share means a cheap run, not a
                    costly one.
                  </p>
                </div>
              </details>
            )}
          </section>

          {trend.length === 0 && flaky.length === 0 && (
            <div className="empty-state card">
              <Activity size={28} />
              <p>Run a suite and this page fills in.</p>
            </div>
          )}
        </>
      )}
    </main>
  );
}
