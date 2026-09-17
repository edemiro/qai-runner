import { useEffect, useRef, useState } from 'react';
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

    const startPolling = () => {
      if (cancelled) return;
      setConnection('polling');
      const tick = async () => {
        if (cancelled) return;
        let keepPolling = true;
        try {
          const data = await api.screenshot(sessionId);
          if (!cancelled && data?.screenshot) {
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
          } else {
            setConnection('error');
          }
        } finally {
          if (!cancelled && keepPolling) {
            pollTimer = setTimeout(tick, Math.round(1000 / fpsRef.current));
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
  }, [sessionId, enabled]);

  useEffect(() => {
    const socket = socketRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ fps }));
    }
  }, [fps]);

  return { screenshot, connection, lostReason };
}

function connectionIsUnusable(socket) {
  return !socket || socket.readyState === WebSocket.CLOSED || socket.readyState === WebSocket.CLOSING;
}
