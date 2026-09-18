import { useEffect } from 'react';
import { Sparkles, X } from 'lucide-react';

import { ScenarioGenerator } from './ScenarioGenerator';

/**
 * The chat "write scenarios" flow, as a review step rather than a silent save.
 *
 * Asking for scenarios from the composer used to generate them and file them
 * into a set with no chance to look. This opens the same generator in a dialog,
 * seeded with the brief and generating on open, so the scenarios are ticked,
 * edited and named before anything is saved or run.
 */
export function ScenarioReviewModal({
  sessionId, brief = '', onClose,
  // Already-written scenarios (from the agent) to review, with the set name
  // and page they came from. When given, nothing is generated on open.
  scenarios = null, readFrom = null, suggestedName = '',
  // Platform of the session being reviewed, so a new set is filed under it.
  kind = 'web',
}) {
  const seeded = Array.isArray(scenarios) && scenarios.length > 0;
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="modal-overlay" onClick={onClose} role="dialog" aria-label="Review scenarios">
      <div className="modal-card scenario-review" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2 className="modal-title"><Sparkles size={16} /> Review scenarios</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <p className="modal-sub">
          Tick the ones to keep, edit any title, goal or step, name the Test Set,
          then save — or save and run.
        </p>
        <div className="modal-body">
          <ScenarioGenerator
            sessionId={sessionId}
            defaultBrief={brief}
            autoGenerate={!seeded && Boolean(brief)}
            initialScenarios={seeded ? scenarios : null}
            initialReadFrom={readFrom}
            defaultSetName={suggestedName}
            kind={kind}
            onAdded={onClose}
          />
        </div>
      </div>
    </div>
  );
}
