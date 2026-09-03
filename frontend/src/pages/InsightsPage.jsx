import { useEffect, useMemo, useState } from 'react';
import { Activity, AlertTriangle, ShieldAlert, TrendingUp } from 'lucide-react';

import { api } from '../api';
import { useToast } from '../hooks/useToast';

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
  const [days, setDays] = useState(14);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // Awaited first so nothing sets state during the same commit that
      // scheduled this effect.
      const [trendData, flakyData, priorityData] = await Promise.all([
        api.trend(days).catch((err) => ({ error: err })),
        api.flaky(20).catch((err) => ({ error: err })),
        api.priorityBreakdown(days).catch((err) => ({ error: err })),
      ]);
      if (cancelled) return;
      const failure = trendData.error || flakyData.error || priorityData.error;
      if (failure) toast.error(failure.message);
      if (trendData.trend) setTrend(trendData.trend);
      if (flakyData.flaky) setFlaky(flakyData.flaky);
      if (priorityData.breakdown) setBreakdown(priorityData.breakdown);
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [days, toast]);

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
      </header>

      {loading ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          <div className="stat-row">
            <div className="stat-card">
              <span className="stat-label">Runs</span>
              <span className="stat-value">{totals.total}</span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Pass rate</span>
              <span className={`stat-value ${totals.rate != null && totals.rate < 0.8 ? 'bad' : 'ok'}`}>
                {totals.rate == null ? '—' : `${Math.round(totals.rate * 100)}%`}
              </span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Failed</span>
              <span className="stat-value">{totals.failed}</span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Avg duration</span>
              <span className="stat-value">
                {totals.avgMs == null ? '—' : `${(totals.avgMs / 1000).toFixed(1)}s`}
              </span>
            </div>
          </div>

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
