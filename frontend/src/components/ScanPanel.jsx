import {
  AlertTriangle, ArrowLeft, CheckCircle2, ExternalLink, Loader2,
  MousePointerClick, Square, XCircle,
} from 'lucide-react';

const TITLES = {
  explore: 'Keşif testi',
  links: 'Kırık bağlantı taraması',
  a11y: 'Erişilebilirlik taraması',
};

// How each crawl outcome reads and reads back. Order matters: findings first.
const OUTCOMES = {
  dead: { label: 'çalışmıyor', tone: 'bad', help: 'Tıklandı, hiçbir şey olmadı' },
  error: { label: 'hata', tone: 'bad', help: 'Hata verdi' },
  navigated: { label: 'yönlendirdi', tone: 'ok', help: 'Başka sayfaya gitti' },
  changed: { label: 'değiştirdi', tone: 'ok', help: 'Sayfayı değiştirdi' },
  dialog: { label: 'pencere açtı', tone: 'ok', help: 'Modal açtı' },
  request: { label: 'istek attı', tone: 'warn', help: 'Sunucuya gitti, ekran değişmedi' },
  blocked: { label: 'atlandı', tone: 'muted', help: 'Güvenlik gereği denenmedi' },
  gone: { label: 'kayboldu', tone: 'muted', help: 'Sırası gelince yoktu' },
};

const SEVERITY = {
  critical: 'bad',
  serious: 'bad',
  moderate: 'warn',
  minor: 'muted',
};

/**
 * Results for the checks that need no model: the exploratory crawl, the link
 * scan and the accessibility scan.
 *
 * The findings are what matters, so they sort to the top and everything that
 * merely worked collapses into a tally — a wall of green rows would bury the
 * two dead buttons that are the reason to run this.
 */
export function ScanPanel({ mode, running, rows, summary, tally, error, warning, progress, runId, onBack, onStop, onOpenRun }) {
  const findings = mode === 'explore'
    ? rows.filter((row) => ['dead', 'error'].includes(row.outcome))
    : rows;
  const rest = mode === 'explore'
    ? rows.filter((row) => !['dead', 'error'].includes(row.outcome))
    : [];

  return (
    <div className="scan-panel">
      <header className="scan-head">
        <button className="btn btn-ghost btn-sm" onClick={onBack}>
          <ArrowLeft size={14} /> Agent'a dön
        </button>
        <span className="scan-title">{TITLES[mode] || 'Tarama'}</span>
        {running && (
          <button className="btn btn-danger btn-sm" onClick={onStop}>
            <Square size={13} /> Durdur
          </button>
        )}
      </header>

      {running && (
        <div className="scan-progress">
          <Loader2 size={14} className="spin" />
          <span>
            {progress
              ? `${progress.done} / ${progress.total} kontrol denendi`
              : 'Sayfa taranıyor…'}
          </span>
          {progress && (
            <div className="scan-bar">
              <div
                className="scan-bar-fill"
                style={{ width: `${(progress.done / Math.max(progress.total, 1)) * 100}%` }}
              />
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="banner danger">
          <XCircle size={15} />
          <span>{error}</span>
        </div>
      )}

      {warning && (
        <div className="banner warn">
          <AlertTriangle size={15} />
          <span>{warning}</span>
        </div>
      )}

      {summary && (
        <div className={`scan-summary ${findings.length ? 'bad' : 'ok'}`}>
          {findings.length ? <AlertTriangle size={16} /> : <CheckCircle2 size={16} />}
          <span>{summary}</span>
          {runId && (
            <button className="btn-link" onClick={() => onOpenRun?.(runId)}>
              raporu aç
            </button>
          )}
        </div>
      )}

      {mode === 'explore' && tally && (
        <div className="scan-tally">
          {Object.entries(tally).map(([key, count]) => (
            <span key={key} className={`tally-chip ${OUTCOMES[key]?.tone || 'muted'}`}
                  title={OUTCOMES[key]?.help || key}>
              {count} {OUTCOMES[key]?.label || key}
            </span>
          ))}
        </div>
      )}

      {mode === 'links' && tally && (
        <div className="scan-tally">
          <span className="tally-chip ok">{tally.ok} çalışıyor</span>
          <span className={`tally-chip ${tally.broken ? 'bad' : 'muted'}`}>{tally.broken} kırık</span>
        </div>
      )}

      <div className="scan-rows">
        {findings.length > 0 && mode === 'explore' && (
          <h4 className="scan-section">Bulgular</h4>
        )}

        {mode === 'explore' && findings.map((row, index) => (
          <ExploreRow key={`f-${index}`} row={row} />
        ))}

        {mode === 'links' && rows.map((row, index) => (
          <div key={index} className="scan-row bad">
            <span className="scan-badge bad">{row.status || row.error || 'hata'}</span>
            <div className="scan-row-body">
              <span className="scan-row-label">{row.text || '(metinsiz bağlantı)'}</span>
              <a className="scan-row-url" href={row.href} target="_blank" rel="noreferrer">
                {row.href} <ExternalLink size={11} />
              </a>
            </div>
          </div>
        ))}

        {mode === 'a11y' && rows.map((row, index) => (
          <div key={index} className={`scan-row ${SEVERITY[row.severity] || 'muted'}`}>
            <span className={`scan-badge ${SEVERITY[row.severity] || 'muted'}`}>{row.severity}</span>
            <div className="scan-row-body">
              <span className="scan-row-label">{row.detail}</span>
              <span className="scan-row-url">
                {row.element}{row.hint ? ` — ${row.hint}` : ''}
              </span>
            </div>
          </div>
        ))}

        {rest.length > 0 && (
          <>
            <h4 className="scan-section">Çalışanlar ({rest.length})</h4>
            {rest.map((row, index) => <ExploreRow key={`r-${index}`} row={row} muted />)}
          </>
        )}

        {!running && !error && rows.length === 0 && (
          <p className="muted small">Sonuç yok.</p>
        )}
      </div>
    </div>
  );
}

function ExploreRow({ row, muted = false }) {
  const outcome = OUTCOMES[row.outcome] || { label: row.outcome, tone: 'muted' };
  return (
    <div className={`scan-row ${muted ? 'muted' : outcome.tone}`}>
      <span className={`scan-badge ${outcome.tone}`}>
        <MousePointerClick size={10} />
        {outcome.label}
      </span>
      <div className="scan-row-body">
        <span className="scan-row-label">{row.label}</span>
        <span className="scan-row-url">{row.detail}</span>
        {/* The browser's own words, so a reported error can be judged rather
            than just counted. */}
        {row.events?.length > 0 && (
          <ul className="scan-events">
            {row.events.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>
        )}
      </div>
      {row.durationMs != null && (
        <span className="scan-row-time">{(row.durationMs / 1000).toFixed(1)}s</span>
      )}
    </div>
  );
}
