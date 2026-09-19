import { ArrowDown, ArrowUp, Plus, Trash2 } from 'lucide-react';

/**
 * The scenario written out as ordered steps, each with the result it expects.
 *
 * A scenario with steps is run and judged step by step: the agent gets one at a
 * time and has to prove that step's expected result before moving on, so the
 * report can answer "did step 3 pass". Without steps the run stays open-ended
 * and is judged only as a whole — which is why the expected result is the field
 * that matters most here. A step with no expected result can only ever be
 * reported as "carried out", never as verified.
 */
export function StepEditor({ steps, onChange, disabled = false }) {
  /* Rewriting a step drops what the last green run recorded for it. The runner
     replays that recording instead of working the step out again, so a step
     that now says something different has to be worked out again — and the
     danger is not the wasted effort, it is a recorded assertion general enough
     to still pass, which would report the new step as verified without it ever
     having been carried out. Reordering keeps them: a step that moved is still
     the same step. */
  const update = (index, patch) => {
    onChange(steps.map((step, i) => {
      if (i !== index) return step;
      const next = { ...step, ...patch };
      if ('action' in patch || 'expected' in patch) delete next.recorded;
      return next;
    }));
  };

  const add = () => onChange([...steps, { action: '', expected: '' }]);
  const remove = (index) => onChange(steps.filter((_, i) => i !== index));

  const move = (index, delta) => {
    const target = index + delta;
    if (target < 0 || target >= steps.length) return;
    const next = [...steps];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  };

  return (
    <div className="step-editor">
      <div className="step-editor-head">
        <span className="field-label">Steps</span>
        <span className="field-hint">
          {steps.length
            ? 'Each step is run and reported on its own.'
            : 'Optional. Without steps the run is judged only as a whole.'}
        </span>
      </div>

      {steps.length > 0 && (
        <ol className="step-editor-list">
          {steps.map((step, index) => (
            <li key={index} className="step-editor-row">
              <span className="step-editor-idx">{index + 1}</span>
              <div className="step-editor-fields">
                <input
                  type="text"
                  value={step.action}
                  onChange={(event) => update(index, { action: event.target.value })}
                  placeholder="What to do — e.g. tap “Book a flight”"
                  disabled={disabled}
                />
                <input
                  type="text"
                  value={step.expected || ''}
                  onChange={(event) => update(index, { expected: event.target.value })}
                  placeholder="Expected result — what proves this step worked"
                  disabled={disabled}
                />
              </div>
              <div className="step-editor-actions">
                <button
                  type="button"
                  className="btn-icon"
                  onClick={() => move(index, -1)}
                  disabled={disabled || index === 0}
                  aria-label={`Move step ${index + 1} up`}
                >
                  <ArrowUp size={13} />
                </button>
                <button
                  type="button"
                  className="btn-icon"
                  onClick={() => move(index, 1)}
                  disabled={disabled || index === steps.length - 1}
                  aria-label={`Move step ${index + 1} down`}
                >
                  <ArrowDown size={13} />
                </button>
                <button
                  type="button"
                  className="btn-icon danger"
                  onClick={() => remove(index)}
                  disabled={disabled}
                  aria-label={`Delete step ${index + 1}`}
                >
                  <Trash2 size={13} />
                </button>
              </div>
            </li>
          ))}
        </ol>
      )}

      <button type="button" className="btn btn-ghost btn-sm" onClick={add} disabled={disabled}>
        <Plus size={13} /> Add step
      </button>
    </div>
  );
}
