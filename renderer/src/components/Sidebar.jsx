import React, { useState, useEffect, useCallback } from 'react';

export default function Sidebar({ onNewChat, currentSessionId, onSelectSession, refreshKey }) {
  const [sessions, setSessions]       = useState([]);
  const [showSearch, setShowSearch]   = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  const fetchSessions = useCallback(async () => {
    try {
      const res = await fetch('http://127.0.0.1:8765/sessions');
      if (res.ok) {
        const data = await res.json();
        setSessions(data.sessions || []);
      }
    } catch (_) {
      /* backend offline */
    }
  }, []);

  useEffect(() => {
    fetchSessions();
  }, [fetchSessions, currentSessionId, refreshKey]);

  // Periodic poll so newly completed chats appear automatically
  useEffect(() => {
    const timer = setInterval(fetchSessions, 6000);
    return () => clearInterval(timer);
  }, [fetchSessions]);

  const handleDelete = async (e, sessionId) => {
    e.stopPropagation();
    try {
      const res = await fetch(`http://127.0.0.1:8765/sessions/${sessionId}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        setSessions(prev => prev.filter(s => s.session_id !== sessionId));
        if (sessionId === currentSessionId) {
          onNewChat?.();
        }
      }
    } catch (err) {
      console.error('[aurora] failed to delete session:', err);
    }
  };

  const filtered = sessions.filter(s => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return (s.title && s.title.toLowerCase().includes(q)) ||
           (s.preview && s.preview.toLowerCase().includes(q));
  });

  return (
    <aside className="sidebar">
      {/* Header */}
      <div className="sidebar-header">
        <h2>Chat</h2>
        <button
          className="sidebar-search-btn"
          title="Search previous chats"
          onClick={() => setShowSearch(prev => !prev)}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
        </button>
      </div>

      {/* Quick Search Box (Toggleable) */}
      {showSearch && (
        <div className="sidebar-search-box">
          <input
            type="text"
            placeholder="Filter chats…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            autoFocus
          />
        </div>
      )}

      {/* New Chat */}
      <button className="new-chat-btn" onClick={onNewChat}>
        <span>+ New Chat</span>
      </button>

      {/* Scrollable list for chat history */}
      <div className="sidebar-scroll">
        <div className="sidebar-section">
          <div className="sidebar-section-label">Recent Chats</div>

          {filtered.length === 0 ? (
            <div style={{ padding: '12px 8px', color: 'var(--text-3)', fontSize: '12px', fontStyle: 'italic' }}>
              {sessions.length === 0 ? 'No previous chats' : 'No matching chats'}
            </div>
          ) : (
            filtered.map(s => {
              const isActive = s.session_id === currentSessionId;
              return (
                <div
                  key={s.session_id}
                  className={`chat-item${isActive ? ' active' : ''}`}
                  onClick={() => onSelectSession?.(s.session_id)}
                  title={`${s.title}\n${s.message_count || 0} messages`}
                >
                  <div className="chat-item-icon icon-c">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 12, height: 12 }}>
                      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                    </svg>
                  </div>
                  <div className="chat-item-content" style={{ flex: 1, minWidth: 0, overflow: 'hidden' }}>
                    <div className="chat-item-text">{s.title || 'Untitled Chat'}</div>
                    {s.message_count > 0 && (
                      <div style={{ fontSize: '10px', color: 'var(--text-3)', marginTop: '1px' }}>
                        {s.message_count} msgs
                      </div>
                    )}
                  </div>
                  <button
                    type="button"
                    className="chat-item-del"
                    title="Delete chat"
                    onClick={(e) => handleDelete(e, s.session_id)}
                  >
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 12, height: 12 }}>
                      <line x1="18" y1="6" x2="6" y2="18"/>
                      <line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                  </button>
                </div>
              );
            })
          )}
        </div>
      </div>
    </aside>
  );
}
