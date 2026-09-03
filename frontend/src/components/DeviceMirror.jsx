import { useCallback, useRef, useState } from 'react';
import {
  ChevronLeft, Circle, CornerDownLeft, KeyboardOff, MousePointerClick,
  RefreshCw, Smartphone, Square,
} from 'lucide-react';
import { api } from '../api';
import { useToast } from '../hooks/useToast';

const DRAG_THRESHOLD_PX = 8;
const LONG_PRESS_MS = 500;

export function DeviceMirror({
  sessionId,
  device,
  screenshot,
  connection,
  screen,
  fps,
  onFpsChange,
  highlight,
  onElementPicked,
  onScreenChanged,
  interactive = true,
}) {
  const toast = useToast();
  const imgRef = useRef(null);
  const pressRef = useRef(null);
  const [typedText, setTypedText] = useState('');
  const [pickMode, setPickMode] = useState(false);
  const [busy, setBusy] = useState(false);

  const isWeb = device?.kind === 'web';
  const screenWidth = screen?.width || (isWeb ? 1440 : 1080);
  const screenHeight = screen?.height || (isWeb ? 900 : 2400);

  /** Map a browser event to logical device coordinates. */
  const toDeviceCoords = useCallback(
    (event) => {
      const node = imgRef.current;
      if (!node) return null;
      const rect = node.getBoundingClientRect();
      if (!rect.width || !rect.height) return null;
      return {
        x: Math.round(((event.clientX - rect.left) / rect.width) * screenWidth),
        y: Math.round(((event.clientY - rect.top) / rect.height) * screenHeight),
      };
    },
    [screenWidth, screenHeight],
  );

  const sendGesture = useCallback(
    async (payload) => {
      if (!sessionId) return;
      setBusy(true);
      try {
        await api.gesture(sessionId, payload);
        setTimeout(() => onScreenChanged?.(), 700);
      } catch (err) {
        toast.error(err.message);
      } finally {
        setBusy(false);
      }
    },
    [sessionId, onScreenChanged, toast],
  );

  const handlePointerDown = (event) => {
    if (!interactive || !sessionId || event.button !== 0) return;
    const coords = toDeviceCoords(event);
    if (!coords) return;
    pressRef.current = { ...coords, clientX: event.clientX, clientY: event.clientY, at: Date.now() };
  };

  const handlePointerUp = async (event) => {
    if (!interactive || !sessionId || event.button !== 0) return;
    const start = pressRef.current;
    pressRef.current = null;
    if (!start) return;

    const end = toDeviceCoords(event);
    if (!end) return;

    if (pickMode) {
      setPickMode(false);
      try {
        const data = await api.elementAt(sessionId, end.x, end.y);
        if (data.element) {
          onElementPicked?.(data.element);
        } else {
          toast.warning('No element found at that point. Reload the source and try again.');
        }
      } catch (err) {
        toast.error(err.message);
      }
      return;
    }

    const heldMs = Date.now() - start.at;
    const moved =
      Math.abs(event.clientX - start.clientX) > DRAG_THRESHOLD_PX ||
      Math.abs(event.clientY - start.clientY) > DRAG_THRESHOLD_PX;

    if (moved) {
      await sendGesture({ type: 'swipe', x: start.x, y: start.y, endX: end.x, endY: end.y, duration: Math.max(200, heldMs) });
    } else if (heldMs >= LONG_PRESS_MS) {
      await sendGesture({ type: 'long_press', x: start.x, y: start.y, duration: heldMs });
    } else {
      await sendGesture({ type: 'tap', x: start.x, y: start.y });
    }
  };

  const handleContextMenu = async (event) => {
    if (!interactive || !sessionId) return;
    event.preventDefault();
    const coords = toDeviceCoords(event);
    if (coords) await sendGesture({ type: 'long_press', ...coords, duration: 1000 });
  };

  const handleWheel = async (event) => {
    if (!interactive || !sessionId || busy) return;
    const direction = event.deltaY > 0 ? 'down' : 'up';
    await sendGesture({ type: 'scroll', direction });
  };

  const sendText = async () => {
    if (!typedText.trim()) return;
    await sendGesture({ type: 'type_text', text: typedText });
    setTypedText('');
  };

  return (
    <aside className="mirror">
      <header className="mirror-header">
        <div>
          <h2 className="mirror-title">{isWeb ? 'Page' : 'Device'}</h2>
          <p className="mirror-subtitle truncate">
            {device ? (isWeb ? device.name : `${device.name} · ${device.platform}`) : 'Not connected'}
          </p>
        </div>
        {sessionId && (
          <span className={`stream-pill ${connection}`}>
            <span className="status-dot" />
            {connection === 'live' ? 'socket' : connection === 'polling' ? 'polling' : connection}
          </span>
        )}
      </header>

      <div className="phone-frame-wrap">
        <div
          className={`phone-frame ${isWeb ? 'browser-frame' : ''} ${pickMode ? 'picking' : ''}`}
          style={isWeb ? { aspectRatio: `${screenWidth} / ${screenHeight}` } : undefined}
        >
          {isWeb ? (
            <div className="browser-chrome">
              <span className="chrome-dot" />
              <span className="chrome-dot" />
              <span className="chrome-dot" />
              <span className="browser-url">{device?.name}</span>
            </div>
          ) : (
            <div className="phone-notch" />
          )}
          <div className="phone-screen">
            {screenshot ? (
              <>
                <img
                  ref={imgRef}
                  src={`data:image/png;base64,${screenshot}`}
                  alt="Live device screen"
                  className="phone-image"
                  draggable={false}
                  onMouseDown={handlePointerDown}
                  onMouseUp={handlePointerUp}
                  onContextMenu={handleContextMenu}
                  onWheel={handleWheel}
                  style={{ cursor: pickMode ? 'crosshair' : interactive ? 'pointer' : 'default' }}
                />
                {highlight && (
                  <svg className="phone-overlay" aria-hidden="true">
                    <rect
                      x={`${highlight.left}%`}
                      y={`${highlight.top}%`}
                      width={`${highlight.width}%`}
                      height={`${highlight.height}%`}
                      fill={highlight.color}
                      fillOpacity="0.16"
                      stroke={highlight.color}
                      strokeWidth="2"
                      rx="2"
                    />
                  </svg>
                )}
              </>
            ) : (
              <div className="phone-placeholder">
                <Smartphone size={30} />
                <p>{sessionId ? 'Waiting for the first frame…' : 'Connect a device in Studio'}</p>
              </div>
            )}
          </div>
        </div>
      </div>

      {sessionId && (
        <div className="mirror-controls">
          <div className="key-row">
            <button className="key-btn" onClick={() => sendGesture({ type: 'key', key: 'back' })} title="Back">
              <ChevronLeft size={15} />
            </button>
            {isWeb ? (
              <>
                <button
                  className="key-btn"
                  onClick={() => sendGesture({ type: 'key', key: 'forward' })}
                  title="Forward"
                >
                  <ChevronLeft size={15} style={{ transform: 'rotate(180deg)' }} />
                </button>
                <button
                  className="key-btn"
                  onClick={() => sendGesture({ type: 'key', key: 'enter' })}
                  title="Enter"
                >
                  <CornerDownLeft size={14} />
                </button>
              </>
            ) : (
              <>
                <button className="key-btn" onClick={() => sendGesture({ type: 'key', key: 'home' })} title="Home">
                  <Circle size={13} />
                </button>
                <button className="key-btn" onClick={() => sendGesture({ type: 'key', key: 'recents' })} title="Recents">
                  <Square size={12} />
                </button>
                <button className="key-btn" onClick={() => sendGesture({ type: 'hide_keyboard' })} title="Hide keyboard">
                  <KeyboardOff size={14} />
                </button>
              </>
            )}
            <button
              className={`key-btn ${pickMode ? 'armed' : ''}`}
              onClick={() => setPickMode((v) => !v)}
              title="Pick an element by tapping the screen"
            >
              <MousePointerClick size={14} />
            </button>
            <button className="key-btn" onClick={() => onScreenChanged?.()} title="Reload source">
              <RefreshCw size={14} />
            </button>
          </div>

          <div className="text-row">
            <input
              className="text-input"
              placeholder="Type into the focused field…"
              value={typedText}
              onChange={(event) => setTypedText(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && sendText()}
            />
            <button className="btn btn-ghost btn-sm" onClick={sendText} disabled={!typedText.trim()}>
              <CornerDownLeft size={14} />
            </button>
          </div>

          <div className="fps-row">
            <label htmlFor="fps-slider">Stream</label>
            <input
              id="fps-slider"
              type="range"
              min="1"
              max="20"
              value={fps}
              onChange={(event) => onFpsChange(Number(event.target.value))}
            />
            <span className="fps-value">{fps} fps</span>
          </div>

          {pickMode && <p className="mirror-hint">Tap anywhere on the screen to inspect that element.</p>}
        </div>
      )}
    </aside>
  );
}
