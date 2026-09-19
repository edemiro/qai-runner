import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';

/**
 * Live device screen over a WebSocket, with an HTTP polling fallback.
 *
 * The socket only pushes a frame when the image actually changed, so a static
 * screen costs nothing — unlike the old loop that re-fetched a full base64 PNG
 * over HTTP `fps` times a second regardless. A real device's screenshot
 * pipeline costs a couple hundred ms per frame, so 2 (up to 4) is the rate
 * this can actually sustain — higher just queues requests behind each other.
 */
export function useScreenStream(sessionId, fps = 2, enabled = true) {
  const [screenshot, setScreenshot] = useState(null);
  // idle | live | polling | error | lost
  const [connection, setConnection] = useState('idle');
  const [lostReason, setLostReason] = useState(null);
  const socketRef = useRef(null);
  // Bumped to tear the stream down and build it again. Once a session is
  // declared lost the loop stops for good and the effect's own deps never
  // change, so reopening the page left the viewer stuck on "page lost" with a
  // live session behind it.
  const [attempt, setAttempt] = useState(0);
  // Read through a ref so retuning the rate never tears down the stream.
  const fpsRef = useRef(fps);

  useEffect(() => {
    fpsRef.current = fps;
  }, [fps]);

  useEffect(() => {
    let cancelled = false;

    if (!sessionId || !enabled) {
      // Drop the stale frame asynchronously so this effect does not set state
      // during the same commit that scheduled it.
      queueMicrotask(() => {
        if (cancelled) return;
        setScreenshot(null);
        setConnection('idle');
        setLostReason(null);
      });
      return () => {
        cancelled = true;
      };
    }

    let pollTimer = null;
    let socket = null;

    // One failed socket fires both onerror and onclose, and each used to start
    // its own tick chain — two concurrent loops at double the request rate for
    // the life of the session.
    let polling = false;
    // A page that crashed answers 502 rather than 404. Treated as fatal only
    // after a few in a row, so a single blip does not tear a live stream down,
    // but a dead page stops being polled forever and does raise the banner.
    let consecutiveErrors = 0;
    const ERROR_LIMIT = 3;

    const startPolling = () => {
      if (cancelled || polling) return;
      polling = true;
      setConnection('polling');
      const tick = async () => {
        if (cancelled) return;
        let keepPolling = true;
        try {
          const data = await api.screenshot(sessionId);
          if (!cancelled && data?.screenshot) {
            consecutiveErrors = 0;
            setScreenshot(data.screenshot);
            setConnection('polling');
          }
        } catch (err) {
          if (cancelled) return;
          if (err.status === 404) {
            // The session is gone for good. Retrying just logs a 404 a few
            // times a second until the tab is closed.
            keepPolling = false;
            setConnection('lost');
            setLostReason('the session was closed');
          } else if ((consecutiveErrors += 1) >= ERROR_LIMIT) {
            keepPolling = false;
            setConnection('lost');
            setLostReason('the page stopped answering');
          } else {
            setConnection('error');
          }
        } finally {
          if (!cancelled && keepPolling) {
            pollTimer = setTimeout(tick, Math.round(1000 / fpsRef.current));
          } else {
            polling = false;
          }
        }
      };
      tick();
    };

    try {
      socket = new WebSocket(api.screenSocketUrl(sessionId));
      socketRef.current = socket;

      socket.onopen = () => {
        if (cancelled) return;
        setConnection('live');
        socket.send(JSON.stringify({ fps: fpsRef.current }));
      };

      socket.onmessage = (event) => {
        if (cancelled) return;
        try {
          const data = JSON.parse(event.data);
          if (data.screenshot) {
            setScreenshot(data.screenshot);
            setConnection('live');
          } else if (data.dead) {
            // Without this the last good frame stays on screen forever and a
            // dead page is indistinguishable from a slow one.
            setConnection('lost');
            setLostReason(data.reason || 'the page is gone');
          }
        } catch {
          /* ignore malformed frame */
        }
      };

      socket.onerror = () => {
        if (!cancelled && connectionIsUnusable(socket)) startPolling();
      };

      socket.onclose = () => {
        socketRef.current = null;
        if (!cancelled) startPolling();
      };
    } catch {
      startPolling();
    }

    return () => {
      cancelled = true;
      if (pollTimer) clearTimeout(pollTimer);
      if (socket && socket.readyState <= WebSocket.OPEN) {
        socket.onclose = null;
        socket.close();
      }
      socketRef.current = null;
    };
    // `fps` is deliberately absent: rate changes are pushed over the open
    // socket by the effect below rather than by reconnecting the stream.
  }, [sessionId, enabled, attempt]);

  useEffect(() => {
    const socket = socketRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ fps }));
    }
  }, [fps]);

  // Called after the page has been reopened server-side, to rebuild the stream.
  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  return { screenshot, connection, lostReason, retry };
}

function connectionIsUnusable(socket) {
  return !socket || socket.readyState === WebSocket.CLOSED || socket.readyState === WebSocket.CLOSING;
}
