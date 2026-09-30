import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CheckCircle2, ChevronLeft, ChevronRight, Clock, Film, Image as ImageIcon, Loader2, Wrench, X,
  XCircle,
} from 'lucide-react';
import { api } from '../api';
import { formatDuration } from '../lib/format';

/* When a step began, in seconds since the epoch. Its row is written as it
   ends, carrying how long it took. */
const startOf = (step) => (step.created_at || 0) - (step.duration_ms || 0) / 1000;

const clock = (seconds) => {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};

/* `click: "Uçuş ara"`, `type: name-input ← "Ergün"` — what was done, to what,
   in the shape a reader scans a column of. */
function describe(step) {
  const action = (step.action || '').replace(/_/g, ' ');
  const target = step.target ? `: ${step.target}` : '';
  const value = step.value ? ` ← “${step.value}”` : '';
  return `${action}${target}${value}`;
}

function Lightbox({ src, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="lightbox" onClick={onClose} role="dialog" aria-label="Screenshot">
      <img src={src} alt="Screenshot, full size" onClick={(e) => e.stopPropagation()} />
      <button className="lightbox-close" onClick={onClose} aria-label="Close">
        <X size={20} />
      </button>
    </div>
  );
}

function StepShot({ runId, step, shots, onLoaded }) {
  const [zoomed, setZoomed] = useState(false);
  const shot = shots[step.id];

  useEffect(() => {
    if (shot !== undefined || !step.hasScreenshot) return undefined;
    let gone = false;
    (async () => {
      try {
        const data = await api.stepScreenshot(runId, step.id);
        if (!gone) onLoaded(step.id, data.screenshot);
      } catch (err) {
        if (!gone) onLoaded(step.id, { error: err.message || 'No screenshot was kept for this step.' });
      }
    })();
    return () => { gone = true; };
  }, [runId, step.id, step.hasScreenshot, shot, onLoaded]);

  if (!step.hasScreenshot) return <p className="player-note">No screenshot was kept for this step.</p>;
  if (shot === undefined) return <p className="player-note"><Loader2 size={14} className="spin" /> Loading…</p>;
  if (shot?.error) return <p className="player-note">{shot.error}</p>;

  // A browser step is kept as JPEG, a phone's as PNG.
  const src = `data:${shot.startsWith('/9j/') ? 'image/jpeg' : 'image/png'};base64,${shot}`;
  return (
    <>
      <img
        className="player-shot"
        src={src}
        alt={`Screen after step ${step.idx}`}
        title="Click to view full size"
        onClick={() => setZoomed(true)}
      />
      {zoomed && <Lightbox src={src} onClose={() => setZoomed(false)} />}
    </>
  );
}

/**
 * A run as it happened: its recording beside the steps it took.
 *
 * The report used to be a column of steps, each with a screenshot behind a
 * button — where the run stood after every action, and nothing of what went
 * on between them. With a recording, picking a step plays the run from that
 * moment, and playing it walks down the steps. A run with no recording keeps
 * the same shape, with the picked step's screenshot where the video would be.
 *
 * `steps` are the ones on show (the filters above may hide some); the
 * recording is placed against the steps' own times, so it lines up either way.
 */
export function RunPlayer({ run, steps }) {
  const video = useMemo(
    () => (run.artifacts || []).find((a) => a.kind === 'video' && a.started_at),
    [run.artifacts],
  );
  const videoRef = useRef(null);
  const rowRefs = useRef({});
  const [duration, setDuration] = useState(null);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [picked, setPicked] = useState(null);
  const [showing, setShowing] = useState(video ? 'video' : 'shot');
  const [shots, setShots] = useState({});
  const loaded = useCallback((id, shot) => setShots((all) => ({ ...all, [id]: shot })), []);

  // Where each step sits in the recording, in seconds from its start.
  const timeline = useMemo(
    () => steps.map((step) => ({
      step,
      at: video ? startOf(step) - video.started_at : null,
    })),
    [steps, video],
  );
  const inVideo = (entry) => entry.at != null && entry.at >= -1
    && (duration == null || entry.at <= duration + 1);

  // The step on screen at this moment of the recording: the last to have begun.
  const current = useMemo(() => {
    let found = null;
    for (const entry of timeline) {
      if (entry.at != null && entry.at <= time + 0.05) found = entry.step;
    }
    return found;
  }, [timeline, time]);

  const fallback = steps.find((s) => s.status !== 'passed') || steps[steps.length - 1] || null;
  const activeId = (showing === 'video' && playing ? current?.id : picked ?? current?.id)
    ?? fallback?.id ?? null;
  const active = steps.find((s) => s.id === activeId) || null;

  // Playing, the list follows the recording.
  useEffect(() => {
    if (!playing || !current) return;
    rowRefs.current[current.id]?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [playing, current]);

  const choose = (entry) => {
    setPicked(entry.step.id);
    if (video && inVideo(entry) && videoRef.current) {
      setShowing('video');
      videoRef.current.currentTime = Math.max(0, entry.at);
    } else {
      setShowing('shot');
    }
  };

  const withShots = steps.filter((s) => s.hasScreenshot);
  const shotIndex = active ? withShots.findIndex((s) => s.id === active.id) : -1;
  const stepTo = (offset) => {
    const next = withShots[shotIndex + offset];
    if (next) {
      setPicked(next.id);
      setShowing('shot');
    }
  };

  return (
    <div className="run-player">
      <div className="player-media">
        {video && (
          <div className="player-switch" role="group" aria-label="What to show">
            <button
              className={showing === 'video' ? 'active' : ''}
              onClick={() => setShowing('video')}
            >
              <Film size={13} /> Recording
            </button>
            <button
              className={showing === 'shot' ? 'active' : ''}
              onClick={() => setShowing('shot')}
              disabled={!active?.hasScreenshot}
            >
              <ImageIcon size={13} /> Screenshot
            </button>
          </div>
        )}

        {/* Kept mounted while a screenshot is shown, so returning to the
            recording finds it where it was. */}
        {video && (
          <video
            ref={videoRef}
            className="player-video"
            hidden={showing !== 'video'}
            src={api.artifactUrl(video.id)}
            controls
            preload="metadata"
            onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
            onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onEnded={() => setPlaying(false)}
          />
        )}

        {showing === 'shot' && active && (
          <div className="player-still">
            <StepShot runId={run.id} step={active} shots={shots} onLoaded={loaded} />
            <div className="player-still-nav">
              <button className="btn btn-ghost btn-sm" onClick={() => stepTo(-1)} disabled={shotIndex <= 0}>
                <ChevronLeft size={14} /> Previous
              </button>
              <span className="muted small">Step {active.idx}</span>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => stepTo(1)}
                disabled={shotIndex < 0 || shotIndex >= withShots.length - 1}
              >
                Next <ChevronRight size={14} />
              </button>
            </div>
          </div>
        )}

        {!video && (
          <p className="player-note">
            No recording was kept for this run — each step’s screenshot is shown instead.
          </p>
        )}
      </div>

      <ol className="player-steps">
        {timeline.map((entry) => {
          const { step } = entry;
          const failed = step.status !== 'passed';
          return (
            <li
              key={step.id}
              ref={(node) => { rowRefs.current[step.id] = node; }}
              className={`player-step ${failed ? 'failed' : 'passed'} ${step.id === activeId ? 'active' : ''}`}
              onClick={() => choose(entry)}
              title={step.reason || undefined}
            >
              <span className="player-step-idx">{step.idx}</span>
              <span className="player-step-icon">
                {failed ? <XCircle size={15} /> : <CheckCircle2 size={15} />}
              </span>
              <div className="player-step-body">
                <code className="player-step-what">{describe(step)}</code>
                {step.message && <span className="player-step-result">{step.message}</span>}
                {step.id === activeId && step.reason && (
                  <span className="player-step-reason">{step.reason}</span>
                )}
              </div>
              <div className="player-step-meta">
                <span className="player-step-time">
                  <Clock size={11} /> {formatDuration(step.duration_ms)}
                </span>
                <span className="player-step-tags">
                  {step.healed && (
                    <span className="assert-tag healed" title="The recorded selector no longer matched; QAi re-found this element.">
                      <Wrench size={10} /> healed
                    </span>
                  )}
                  {step.hasScreenshot && (
                    <button
                      className="player-chip"
                      onClick={(e) => {
                        e.stopPropagation();
                        setPicked(step.id);
                        setShowing('shot');
                      }}
                    >
                      screenshot
                    </button>
                  )}
                  {video && (inVideo(entry)
                    ? <span className="player-at">{clock(entry.at)}</span>
                    : <span className="player-off">not in video</span>)}
                </span>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
