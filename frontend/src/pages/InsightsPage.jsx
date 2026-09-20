import { useEffect, useMemo, useState } from 'react';
import { Activity, AlertTriangle, Coins, ShieldAlert, TrendingUp } from 'lucide-react';

import { api } from '../api';
import { DEFAULT_PLATFORM, PlatformTabs } from '../components/PlatformTabs';
import { useToast } from '../hooks/useToast';
import { formatTokens } from '../lib/format';
import { OS_TABS } from '../lib/platforms';

/**
 * A suite's history is only useful if it answers three questions: is it getting
 * better or worse, is the damage where it matters, and which cases cannot be
 * trusted. Everything here serves one of those.
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
        <PlatformTabs value={platform} onChange={setPlatform} counts={platformCounts} />
        {platform === 'mobile' && (
          <PlatformTabs
            options={OS_TABS} value={os} onChange={setOs}
            counts={loading ? null : osCounts} sub
          />
        )}
      </header>

      {loading ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          {/* Each figure carries what it is out of. A pass rate with no
              denominator reads the same at 3 runs as at 300, and the two mean
              very different things. */}
          <div className="stat-row">
            <div className="stat-card">
              <span className="stat-label">Runs</span>
              <span className="stat-value">{totals.total}</span>
              <span className="stat-note">
                over {days} day{days === 1 ? '' : 's'}
              </span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Pass rate</span>
              <span className={`stat-value ${totals.rate != null && totals.rate < 0.8 ? 'bad' : 'ok'}`}>
                {totals.rate == null ? '—' : `${Math.round(totals.rate * 100)}%`}
              </span>
              <span className="stat-note">
                {totals.total ? `${totals.passed} of ${totals.total} passed` : 'nothing run yet'}
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
              <>
                <div className="usage-row">
                  <div className="usage-total">
                    <span className="stat-label">Total tokens</span>
                    <span className="stat-value">{formatTokens(usage.total_tokens)}</span>
                    <span className="muted small">{usage.calls} model calls</span>
                  </div>
                  <ul className="usage-breakdown">
                    <li><span>Input</span><strong>{formatTokens(usage.input_tokens)}</strong></li>
                    <li><span>Output</span><strong>{formatTokens(usage.output_tokens)}</strong></li>
                    <li>
                      <span>Cache read</span>
                      <strong className="ok">{formatTokens(usage.cache_read_tokens)}</strong>
                    </li>
                    <li><span>Cache write</span><strong>{formatTokens(usage.cache_write_tokens)}</strong></li>
                  </ul>
                </div>
                <p className="muted small">
                  Cache reads are billed at a fraction of fresh input, so they are
                  counted apart — a high cache-read share means a cheap run, not a
                  costly one.
                </p>
              </>
            )}
          </section>

          {/* Two narrow charts side by side rather than a full-width row each:
              on a desktop that read as a column of mostly empty boxes. */}
          <div className="insights-grid">
          <section className="card">
            <div className="card-head">
              <h2 className="card-title">
                <TrendingUp size={16} /> Pass / fail per day
              </h2>
              <span className="muted small">
                {totals.total ? `${totals.total} scenario${totals.total === 1 ? '' : 's'}` : ''}
              </span>
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
              <p className="muted small">
                A pass rate on its own does not say whether the team is in trouble — the
                same number is routine when the failures are Low and an emergency when
                they are Critical.
              </p>
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
                    <div className="priority-bar">
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
                      {band.pass_rate !== null && ` · ${band.pass_rate}%`}
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
              </p>
            </div>

            {flaky.length === 0 ? (
              <p className="muted small">
                Nothing to report. A case needs at least two finished runs before
                its consistency can be judged.
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
                  {flaky.map((row) => (
                    <tr key={row.case_id}>
                      <td>{row.name}</td>
                      <td className="muted">{row.suite_name || '—'}</td>
                      <td className="num">{row.runs}</td>
                      <td className={`num ${row.pass_rate < 0.8 ? 'bad' : ''}`}>
                        {Math.round(row.pass_rate * 100)}%
                      </td>
                      <td className="num">
                        <span className={`flake-pip ${row.flakiness > 0.3 ? 'high' : ''}`}>
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
