import { useEffect, useRef, useCallback, useState } from 'react';

const WS_RECONNECT_MS = 2000;

/**
 * useWebSocket — manages the Aurora backend WebSocket connection.
 *
 * Args:
 *   wsUrl    — WebSocket endpoint URL
 *   onEvent  — optional synchronous callback (topic, payload) called immediately on inbound events,
 *              avoiding React state batching drops when events arrive in rapid succession.
 *
 * Returns:
 *   connected   — boolean
 *   send(topic, payload) — publish an event to the bus
 *   lastEvent   — {topic, payload} | null — most-recent bus event
 */
export function useWebSocket(wsUrl, onEvent) {
  const wsRef        = useRef(null);
  const retryTimer   = useRef(null);
  const onEventRef   = useRef(onEvent);
  onEventRef.current = onEvent;

  const [connected, setConnected]   = useState(false);
  const [lastEvent,  setLastEvent]  = useState(null);

  const connect = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState <= WebSocket.OPEN) return;
    clearTimeout(retryTimer.current);

    let ws;
    try { ws = new WebSocket(wsUrl); }
    catch { scheduleRetry(); return; }

    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      console.log('[aurora:ws] connected', wsUrl);
    };

    ws.onclose = () => {
      setConnected(false);
      console.warn('[aurora:ws] disconnected — retrying…');
      scheduleRetry();
    };

    ws.onerror = (e) => console.error('[aurora:ws] error', e);

    ws.onmessage = (evt) => {
      let msg;
      try { msg = JSON.parse(evt.data); } catch { return; }
      if (msg.type === 'event') {
        const topic = msg.topic;
        const payload = msg.payload || {};
        try {
          onEventRef.current?.(topic, payload);
        } catch (err) {
          console.error('[aurora:ws] onEvent callback error:', err);
        }
        setLastEvent({ topic, payload });
      } else if (msg.type === 'error') {
        console.error('[aurora:ws] server error:', msg.message);
      }
    };

    function scheduleRetry() {
      retryTimer.current = setTimeout(connect, WS_RECONNECT_MS);
    }
  }, [wsUrl]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(retryTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const send = useCallback((topic, payload = {}) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      console.warn('[aurora:ws] not connected, dropping', topic);
      return;
    }
    ws.send(JSON.stringify({ type: 'publish', topic, payload }));
  }, []);

  return { connected, send, lastEvent };
}
