import React, { useState, useEffect, useCallback, useRef } from 'react';
import GlobalNav from './components/GlobalNav';
import Sidebar from './components/Sidebar';
import TopHeader from './components/TopHeader';
import Canvas from './components/Canvas';
import InputBar from './components/InputBar';
import { useWebSocket } from './hooks/useWebSocket';
import { useTheme } from './hooks/useTheme';

const DEFAULT_WS_URL = 'ws://127.0.0.1:8765/stream';

let msgCounter = 0;
function nextId() { return ++msgCounter; }

export default function App() {
  const [wsUrl, setWsUrl] = useState(DEFAULT_WS_URL);
  const [messages, setMessages] = useState([]);
  const [streaming, setStreaming] = useState(false);
  const { dark, toggle: toggleTheme } = useTheme();
  const streamIdRef = useRef(null);   // id of the assistant bubble being streamed into

  /* ── Load config from preload (Electron IPC) ───────────── */
  useEffect(() => {
    if (window.aurora) {
      window.aurora.getConfig().then(cfg => {
        if (cfg?.wsUrl) setWsUrl(cfg.wsUrl);
      }).catch(() => { });
    }
  }, []);

  const [stats, setStats] = useState({ active_model: 'qwen2.5:3b-instruct-q4_K_M', total_tokens: 0 });

  // Timer for chat responses
  const [responseTime, setResponseTime] = useState(null);
  const startTimeRef = useRef(null);
  const watchdogTimerRef = useRef(null);

  /* ── Watchdog timer for stream completion ──────────────── */
  const clearWatchdog = useCallback(() => {
    if (watchdogTimerRef.current) {
      clearTimeout(watchdogTimerRef.current);
      watchdogTimerRef.current = null;
    }
  }, []);

  const resetWatchdog = useCallback(() => {
    clearWatchdog();
    watchdogTimerRef.current = setTimeout(() => {
      console.warn('[aurora:stream] watchdog timed out (10s inactivity) — auto-finalizing stream');
      setStreaming(false);
      streamIdRef.current = null;
      setMessages(prev => prev.map(m => m.streaming ? { ...m, streaming: false } : m));
    }, 10000);
  }, [clearWatchdog]);

  /* ── Handle inbound bus events synchronously ───────────── */
  const handleBusEvent = useCallback((topic, payload) => {
    if (topic === 'stats.update') {
      setStats(payload);
    }

    if (topic === 'llm.token') {
      const token = payload.token || '';
      resetWatchdog();
      setMessages(prev => {
        // If no assistant bubble in progress yet, append one
        if (!streamIdRef.current) {
          const id = nextId();
          streamIdRef.current = id;
          setStreaming(true);
          return [...prev, { id, role: 'assistant', content: token, streaming: true }];
        }
        // Append token to the existing streaming bubble
        return prev.map(m =>
          m.id === streamIdRef.current
            ? { ...m, content: m.content + token }
            : m
        );
      });
    }

    if (topic === 'chat.complete') {
      clearWatchdog();
      // Calculate elapsed time in seconds
      if (startTimeRef.current) {
        const elapsed = ((performance.now() - startTimeRef.current) / 1000).toFixed(2);
        setResponseTime(elapsed);
      }

      setMessages(prev =>
        prev.map(m => (m.streaming ? { ...m, streaming: false } : m))
      );
      streamIdRef.current = null;
      setStreaming(false);
      setRefreshKey(k => k + 1);
    }
  }, [resetWatchdog, clearWatchdog]);

  /* ── WebSocket with synchronous event listener ─────────── */
  const { connected, send } = useWebSocket(wsUrl, handleBusEvent);

  /* ── Manually stop streaming / release input lock ───────── */
  const handleStopStreaming = useCallback(() => {
    clearWatchdog();
    setStreaming(false);
    streamIdRef.current = null;
    setMessages(prev =>
      prev.map(m => (m.streaming ? { ...m, streaming: false } : m))
    );
  }, [clearWatchdog]);

  const [sessionId, setSessionId] = useState(() => localStorage.getItem('aurora_last_session') || ('sess_' + Math.random().toString(36).slice(2, 10)));
  const [refreshKey, setRefreshKey] = useState(0);
  const initialLoadDone = useRef(false);

  /* ── Auto-restore last active session on launch ─────────── */
  useEffect(() => {
    if (initialLoadDone.current) return;
    initialLoadDone.current = true;

    let retryTimer = null;
    const restoreSession = async () => {
      try {
        const res = await fetch('http://127.0.0.1:8765/sessions');
        if (!res.ok) {
          retryTimer = setTimeout(restoreSession, 1200);
          return;
        }
        const data = await res.json();
        const sessionList = data.sessions || [];
        if (sessionList.length === 0) return;

        // Try to resume the last active session, or default to the most recent chat
        const savedId = localStorage.getItem('aurora_last_session');
        const target = sessionList.find(s => s.session_id === savedId) || sessionList[0];

        if (target && target.session_id) {
          const msgRes = await fetch(`http://127.0.0.1:8765/sessions/${target.session_id}/messages`);
          if (msgRes.ok) {
            const msgData = await msgRes.json();
            const loaded = (msgData.messages || []).map((m, idx) => ({
              id: m.id || idx + 1,
              role: m.role,
              content: m.content,
              attachments: m.metadata?.attachments || [],
              streaming: false,
            }));
            if (loaded.length > 0) {
              setMessages(loaded);
              setSessionId(target.session_id);
              localStorage.setItem('aurora_last_session', target.session_id);
            }
          }
        }
      } catch (_) {
        retryTimer = setTimeout(restoreSession, 1500);
      }
    };

    restoreSession();
    return () => { if (retryTimer) clearTimeout(retryTimer); };
  }, []);

  /* ── Switch to a previous session ────────────────────────── */
  const handleSelectSession = useCallback(async (targetSessionId) => {
    if (targetSessionId === sessionId) return;

    // 1. Notify backend about session switch so Turbo Cache hydrates that session's memory
    send('session.switch', {
      prev_session_id: sessionId,
      session_id: targetSessionId,
    });

    // 2. Fetch session history from backend
    try {
      const res = await fetch(`http://127.0.0.1:8765/sessions/${targetSessionId}/messages`);
      if (res.ok) {
        const data = await res.json();
        const loaded = (data.messages || []).map((m, idx) => ({
          id: m.id || idx + 1,
          role: m.role,
          content: m.content,
          attachments: m.metadata?.attachments || [],
          streaming: false,
        }));
        setMessages(loaded);
      } else {
        setMessages([]);
      }
    } catch (err) {
      console.error('[aurora] failed to load session messages:', err);
      setMessages([]);
    }

    setSessionId(targetSessionId);
    localStorage.setItem('aurora_last_session', targetSessionId);
    setStreaming(false);
    streamIdRef.current = null;
    setPrefill('');
    setResponseTime(null);
  }, [sessionId, send]);

  /* ── Send a user message ────────────────────────────────── */
  const handleSend = useCallback((text, attachments = []) => {
    // Record start timestamp
    startTimeRef.current = performance.now();
    setResponseTime(null);

    setMessages(prev => [
      ...prev,
      { id: nextId(), role: 'user', content: text, attachments, streaming: false }
    ]);
    streamIdRef.current = null;
    setStreaming(true);

    const history = messages
      .filter(m => !m.streaming && m.content)
      .map(m => ({ role: m.role, content: m.content }));

    localStorage.setItem('aurora_last_session', sessionId);

    send('user.message', {
      text,
      attachments: attachments.map(a => typeof a === 'string' ? a : a.path),
      session_id: sessionId,
      history: history,
    });
    setRefreshKey(k => k + 1);
  }, [send, sessionId, messages]);

  /* ── Pre-fill prompt from canvas cards ──────────────────── */
  const handleFillPrompt = useCallback((text) => {
    // We raise a custom event that InputBar listens to — or we lift state.
    // Simplest: store it in App state and pass as prop.
    setPrefill(text);
  }, []);

  const [prefill, setPrefill] = useState('');

  /* ── New chat ────────────────────────────────────────────── */
  const handleNewChat = useCallback(() => {
    const newId = 'sess_' + Math.random().toString(36).slice(2, 10);
    setSessionId(newId);
    localStorage.setItem('aurora_last_session', newId);
    setMessages([]);
    setStreaming(false);
    streamIdRef.current = null;
    setPrefill('');
  }, []);

  /* ── Keyboard shortcuts ──────────────────────────────────── */
  useEffect(() => {
    const handler = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'l') { e.preventDefault(); handleNewChat(); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [handleNewChat]);

  return (
    <div id="app">
      {/* Offline banner */}
      {!connected && (
        <div className="offline-banner visible">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
            strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
          Backend offline — retrying connection…
        </div>
      )}

      <GlobalNav dark={dark} onToggleTheme={toggleTheme} />
      <Sidebar
        onNewChat={handleNewChat}
        currentSessionId={sessionId}
        onSelectSession={handleSelectSession}
        refreshKey={refreshKey}
      />

      <main className="main">
        <TopHeader connected={connected} onNewChat={handleNewChat} stats={stats} />
        <Canvas
          messages={messages}
          onFillPrompt={handleFillPrompt}
        />
        <InputBar
          connected={connected}
          streaming={streaming}
          onSend={handleSend}
          onStop={handleStopStreaming}
          prefill={prefill}
          onPrefillConsumed={() => setPrefill('')}
          responseTime={responseTime}
        />
      </main>
    </div>
  );
}
