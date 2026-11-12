import React, { useState, useEffect } from 'react';

export default function TopHeader({ connected, onNewChat, stats }) {
  const [showConfig, setShowConfig] = useState(false);
  const [model, setModel]           = useState('Loading...');
  const [vlmModel, setVlmModel]     = useState('qwen3-vl:4b-instruct-q4_K_M');
  const [imageModel, setImageModel] = useState('fooocus-speed');
  const [availableImageModels, setAvailableImageModels] = useState([
    { id: 'fooocus-speed', name: 'Fooocus Speed (30-step SDXL Juggernaut)' },
    { id: 'fooocus-quality', name: 'Fooocus Quality (60-step Extreme Detail)' },
    { id: 'fooocus-anime', name: 'Fooocus Anime / Stylized Manga' },
  ]);
  const [switchingImageModel, setSwitchingImageModel] = useState(false);
  const [tokens, setTokens]         = useState(0);
  const [models, setModels]         = useState([]);
  const [switching, setSwitching]   = useState(false);
  const [switchingVlm, setSwitchingVlm] = useState(false);
  const [dbStorage, setDbStorage]   = useState(null);
  const [clearingDb, setClearingDb] = useState(false);
  const [confirmingClear, setConfirmingClear] = useState(false);
  const [clearSuccess, setClearSuccess] = useState(false);

  // Fetch stats + models + database storage from backend
  const fetchStats = async () => {
    try {
      const res = await fetch('http://127.0.0.1:8765/stats');
      if (res.ok) {
        const data = await res.json();
        if (data.active_model) setModel(data.active_model);
        if (data.vlm_model) setVlmModel(data.vlm_model);
        if (data.image_gen_model) setImageModel(data.image_gen_model);
        if (data.available_image_models && data.available_image_models.length > 0) {
          setAvailableImageModels(data.available_image_models);
        }
        if (data.total_tokens !== undefined) setTokens(data.total_tokens);
        if (data.installed_models) setModels(data.installed_models);
        if (data.database) setDbStorage(data.database);
      }
    } catch (_) { /* backend offline */ }
  };

  useEffect(() => {
    fetchStats();
  }, []);

  useEffect(() => {
    if (showConfig) fetchStats();
  }, [showConfig]);

  useEffect(() => {
    if (stats?.active_model) setModel(stats.active_model);
    if (stats?.total_tokens !== undefined) setTokens(stats.total_tokens);
  }, [stats]);

  const handleModelChange = async (newModel) => {
    if (newModel === model || switching) return;
    setSwitching(true);
    try {
      const res = await fetch('http://127.0.0.1:8765/model', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: newModel }),
      });
      if (res.ok) {
        const data = await res.json();
        setModel(data.active_model);
      }
    } catch (err) {
      console.error('[aurora] model switch failed:', err);
    } finally {
      setSwitching(false);
    }
  };

  const handleVlmModelChange = async (newModel) => {
    if (newModel === vlmModel || switchingVlm) return;
    setSwitchingVlm(true);
    try {
      const res = await fetch('http://127.0.0.1:8765/vlm_model', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: newModel }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.vlm_model) setVlmModel(data.vlm_model);
      }
    } catch (err) {
      console.error('[aurora] vision model switch failed:', err);
    } finally {
      setSwitchingVlm(false);
    }
  };

  const handleImageModelChange = async (newModel) => {
    if (newModel === imageModel || switchingImageModel) return;
    setSwitchingImageModel(true);
    try {
      const res = await fetch('http://127.0.0.1:8765/image_model', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: newModel }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.active) setImageModel(data.active);
      }
    } catch (err) {
      console.error('[aurora] image model switch failed:', err);
    } finally {
      setSwitchingImageModel(false);
    }
  };

  const handleModeChange = async (newMode) => {
    try {
      const res = await fetch('http://127.0.0.1:8765/memory/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: newMode }),
      });
      if (res.ok) {
        fetchStats();
      }
    } catch (err) {
      console.error('[aurora] memory mode switch failed:', err);
    }
  };

  useEffect(() => {
    if (!showConfig) {
      setConfirmingClear(false);
      setClearSuccess(false);
    }
  }, [showConfig]);

  const handleClearDatabase = async () => {
    if (clearingDb) return;
    setClearingDb(true);
    setClearSuccess(false);
    try {
      const res = await fetch('http://127.0.0.1:8765/database/clear', {
        method: 'POST',
      });
      if (res.ok) {
        const data = await res.json();
        if (data.database) setDbStorage(data.database);
        setConfirmingClear(false);
        setClearSuccess(true);
        setTimeout(() => setClearSuccess(false), 3500);
        onNewChat?.();
        fetchStats();
      }
    } catch (err) {
      console.error('[aurora] clear database failed:', err);
    } finally {
      setClearingDb(false);
    }
  };

  return (
    <header className="top-header" style={{ position: 'relative' }}>
      {/* Model identifier */}
      <div className="model-id">
        <span className="model-name">Aurora</span>
        <span className="plus-badge">Plus</span>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
             strokeLinecap="round" strokeLinejoin="round" style={{ width: 14, height: 14, color: 'var(--text-3)' }}>
          <polyline points="6 9 12 15 18 9"/>
        </svg>
      </div>

      <div className="header-actions">
        {/* Backend status pill */}
        <div id="backendStatus" className={connected ? 'online' : 'offline'}>
          <span className="dot" />
          <span>{connected ? 'Online' : 'Offline'}</span>
        </div>

        {/* Configuration button with popover modal */}
        <div style={{ position: 'relative' }}>
          <button className="hdr-btn" onClick={() => setShowConfig(!showConfig)}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                 strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3"/>
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
            </svg>
            Configuration
            {dbStorage?.total_human && (
              <span className="hdr-db-badge" title={`Database Space: ${dbStorage.total_human}`}>
                {dbStorage.total_human}
              </span>
            )}
          </button>

          {showConfig && (
            <div style={{
              position: 'absolute',
              top: '42px',
              right: 0,
              width: '320px',
              background: 'var(--bg)',
              border: '1px solid var(--border-2)',
              borderRadius: 'var(--r-md)',
              boxShadow: 'var(--sh-lg)',
              padding: '16px',
              zIndex: 1000,
              fontSize: '12.5px',
            }}>
              {/* Header */}
              <div style={{ fontWeight: 600, fontSize: '13.5px', marginBottom: '14px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span>⚙ System Status</span>
                <button
                  onClick={() => setShowConfig(false)}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-3)', fontSize: '14px' }}
                >✕</button>
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>

                {/* Model Selector */}
                <div>
                  <label style={{ display: 'block', color: 'var(--text-2)', marginBottom: '6px', fontWeight: 500 }}>
                    🤖 Active Model
                  </label>
                  <select
                    value={model}
                    onChange={(e) => handleModelChange(e.target.value)}
                    disabled={switching || models.length === 0}
                    style={{
                      width: '100%',
                      padding: '8px 10px',
                      borderRadius: '6px',
                      border: '1px solid var(--border-2)',
                      background: 'var(--bg-2)',
                      color: 'var(--text-1)',
                      fontSize: '12px',
                      fontWeight: 600,
                      cursor: switching ? 'wait' : 'pointer',
                      outline: 'none',
                      appearance: 'auto',
                    }}
                  >
                    {models.length > 0 ? (
                      models.map((m) => (
                        <option key={m} value={m}>{m}</option>
                      ))
                    ) : (
                      <option value={model}>{model}</option>
                    )}
                  </select>
                  {switching && (
                    <div style={{ color: 'var(--accent)', fontSize: '11px', marginTop: '4px' }}>
                      Switching model…
                    </div>
                  )}
                </div>

                {/* Vision / VLM Model Selector */}
                <div>
                  <label style={{ display: 'block', color: 'var(--text-2)', marginBottom: '6px', fontWeight: 500 }}>
                    👁️ Vision Model (VLM / OCR)
                  </label>
                  <select
                    value={vlmModel}
                    onChange={(e) => handleVlmModelChange(e.target.value)}
                    disabled={switchingVlm || models.length === 0}
                    style={{
                      width: '100%',
                      padding: '8px 10px',
                      borderRadius: '6px',
                      border: '1px solid var(--border-2)',
                      background: 'var(--bg-2)',
                      color: 'var(--text-1)',
                      fontSize: '12px',
                      fontWeight: 600,
                      cursor: switchingVlm ? 'wait' : 'pointer',
                      outline: 'none',
                      appearance: 'auto',
                    }}
                  >
                    {models.length > 0 ? (
                      models.map((m) => (
                        <option key={m} value={m}>{m}</option>
                      ))
                    ) : (
                      <option value={vlmModel}>{vlmModel}</option>
                    )}
                  </select>
                  {switchingVlm && (
                    <div style={{ color: 'var(--accent)', fontSize: '11px', marginTop: '4px' }}>
                      Switching vision model…
                    </div>
                  )}
                </div>

                {/* Image Generation Model Selector */}
                <div style={{ marginBottom: '14px' }}>
                  <label style={{ display: 'block', color: 'var(--text-2)', marginBottom: '6px', fontWeight: 500 }}>
                    🎨 Image Gen Engine (Fooocus API — 0 MB Local VRAM)
                  </label>
                  <select
                    value={imageModel}
                    onChange={(e) => handleImageModelChange(e.target.value)}
                    disabled={switchingImageModel}
                    style={{
                      width: '100%',
                      padding: '8px 10px',
                      borderRadius: '6px',
                      border: '1px solid var(--border-2)',
                      background: 'var(--bg-2)',
                      color: 'var(--text-1)',
                      fontSize: '12px',
                      fontWeight: 600,
                      cursor: switchingImageModel ? 'wait' : 'pointer',
                      outline: 'none',
                      appearance: 'auto',
                    }}
                  >
                    {availableImageModels.map((m) => (
                      <option key={m.id} value={m.id}>{m.name}</option>
                    ))}
                  </select>
                  {switchingImageModel && (
                    <div style={{ color: 'var(--accent)', fontSize: '11px', marginTop: '4px' }}>
                      Switching Fooocus profile…
                    </div>
                  )}
                </div>

                {/* Memory Pipeline Mode Selector (Installation A vs B) */}
                <div style={{
                  padding: '10px',
                  background: 'var(--bg-2)',
                  borderRadius: 'var(--r-sm)',
                  border: '1px solid var(--border-2)',
                  marginBottom: '10px'
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                    <span style={{ fontWeight: 600, color: 'var(--text-1)', fontSize: '11.5px' }}>
                      ⚡ Memory Pipeline
                    </span>
                    <span style={{
                      fontSize: '10px',
                      padding: '2px 6px',
                      borderRadius: '4px',
                      background: (dbStorage?.memory_mode === 'turbo_cache' ? 'rgba(16, 185, 129, 0.15)' : 'var(--bg)'),
                      color: (dbStorage?.memory_mode === 'turbo_cache' ? '#10b981' : 'var(--text-2)'),
                      fontWeight: 700,
                      border: '1px solid ' + (dbStorage?.memory_mode === 'turbo_cache' ? 'rgba(16, 185, 129, 0.3)' : 'var(--border-2)')
                    }}>
                      {dbStorage?.memory_mode === 'turbo_cache' ? '⚡ Turbo RAM Cache' : '💾 Standard SQLite'}
                    </span>
                  </div>
                  <div style={{ display: 'flex', gap: '6px' }}>
                    <button
                      type="button"
                      onClick={() => handleModeChange('standard')}
                      style={{
                        flex: 1,
                        padding: '5px 4px',
                        fontSize: '10.5px',
                        borderRadius: '4px',
                        border: '1px solid ' + (dbStorage?.memory_mode !== 'turbo_cache' ? 'var(--accent)' : 'var(--border-2)'),
                        background: (dbStorage?.memory_mode !== 'turbo_cache' ? 'var(--accent)' : 'transparent'),
                        color: (dbStorage?.memory_mode !== 'turbo_cache' ? '#fff' : 'var(--text-2)'),
                        fontWeight: dbStorage?.memory_mode !== 'turbo_cache' ? 600 : 400,
                        cursor: 'pointer'
                      }}
                      title="Installation A: Direct SQLite disk persistence (Low RAM usage)"
                    >
                      Low RAM (Direct)
                    </button>
                    <button
                      type="button"
                      onClick={() => handleModeChange('turbo_cache')}
                      style={{
                        flex: 1,
                        padding: '5px 4px',
                        fontSize: '10.5px',
                        borderRadius: '4px',
                        border: '1px solid ' + (dbStorage?.memory_mode === 'turbo_cache' ? '#10b981' : 'var(--border-2)'),
                        background: (dbStorage?.memory_mode === 'turbo_cache' ? '#10b981' : 'transparent'),
                        color: (dbStorage?.memory_mode === 'turbo_cache' ? '#fff' : 'var(--text-2)'),
                        fontWeight: dbStorage?.memory_mode === 'turbo_cache' ? 600 : 400,
                        cursor: 'pointer'
                      }}
                      title="Installation B: In-memory working cache with 1-min periodic batch flush (High RAM)"
                    >
                      High RAM (In-Memory)
                    </button>
                  </div>
                  {dbStorage?.memory_mode === 'turbo_cache' && dbStorage?.cached_sessions > 0 && (
                    <div style={{ marginTop: '5px', fontSize: '10px', color: '#10b981', textAlign: 'center' }}>
                      ● {dbStorage.cached_sessions} active session(s) in RAM (1-min flush timer active)
                    </div>
                  )}
                </div>

                {/* Database Storage Information Bar */}
                <div className="db-storage-box">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontWeight: 600, color: 'var(--text-1)' }}>
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 14, height: 14, color: 'var(--accent)' }}>
                        <ellipse cx="12" cy="5" rx="9" ry="3"/>
                        <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/>
                        <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
                      </svg>
                      <span>Database Storage</span>
                    </div>
                    <span style={{ fontWeight: 700, color: 'var(--text-1)', fontSize: '12.5px' }}>
                      {dbStorage?.total_human || 'Calculating...'}
                    </span>
                  </div>

                  {/* Visual Information Progress Bar */}
                  <div className="db-storage-bar-track" title={`${dbStorage?.percent_used || 0}% used of 100 MB baseline`}>
                    <div
                      className="db-storage-bar-fill"
                      style={{ width: `${Math.max(4, Math.min(100, dbStorage?.percent_used || 4))}%` }}
                    />
                  </div>

                  {/* Storage Breakdown Details */}
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '6px', fontSize: '11px', color: 'var(--text-2)' }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                      <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#3b82f6', display: 'inline-block' }} />
                      SQLite: <strong style={{ color: 'var(--text-1)' }}>{dbStorage?.sqlite_human || '0 B'}</strong>
                    </span>
                    <span style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                      <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#8b5cf6', display: 'inline-block' }} />
                      Vector RAG: <strong style={{ color: 'var(--text-1)' }}>{dbStorage?.vector_human || '0 B'}</strong>
                    </span>
                  </div>

                  {/* Counts */}
                  {dbStorage && (
                    <div style={{ marginTop: '7px', paddingTop: '6px', borderTop: '1px dashed var(--border-2)', fontSize: '10.5px', color: 'var(--text-3)', display: 'flex', justifyContent: 'space-between' }}>
                      <span>💬 {dbStorage.messages_count ?? 0} messages</span>
                      <span>🧠 {dbStorage.memories_count ?? 0} memories</span>
                    </div>
                  )}
                </div>

                {/* Clear Database Action */}
                <div style={{ marginTop: '2px' }}>
                  {!confirmingClear ? (
                    <button
                      type="button"
                      className="clear-db-btn"
                      onClick={() => setConfirmingClear(true)}
                      disabled={clearingDb}
                      style={{
                        width: '100%',
                        padding: '7px 10px',
                        borderRadius: '6px',
                        border: '1px solid rgba(239, 68, 68, 0.35)',
                        background: 'rgba(239, 68, 68, 0.08)',
                        color: '#ef4444',
                        fontSize: '11px',
                        fontWeight: 600,
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: '6px',
                        transition: 'all 0.15s ease',
                      }}
                      title="Clean entire database (messages, memories, cache, and vector store) back to 0"
                    >
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13 }}>
                        <polyline points="3 6 5 6 21 6" />
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                        <line x1="10" y1="11" x2="10" y2="17" />
                        <line x1="14" y1="11" x2="14" y2="17" />
                      </svg>
                      {clearingDb ? 'Clearing Database…' : '🗑 Clear Database (Reset to 0)'}
                    </button>
                  ) : (
                    <div style={{
                      background: 'rgba(239, 68, 68, 0.08)',
                      border: '1px solid rgba(239, 68, 68, 0.3)',
                      borderRadius: '6px',
                      padding: '8px 10px',
                      textAlign: 'center'
                    }}>
                      <div style={{ color: '#ef4444', fontWeight: 600, fontSize: '11px', marginBottom: '6px' }}>
                        ⚠️ Wipe entire database back to 0?
                      </div>
                      <div style={{ display: 'flex', gap: '6px' }}>
                        <button
                          type="button"
                          onClick={handleClearDatabase}
                          disabled={clearingDb}
                          style={{
                            flex: 1,
                            padding: '5px',
                            background: '#ef4444',
                            color: '#fff',
                            border: 'none',
                            borderRadius: '4px',
                            fontSize: '11px',
                            fontWeight: 600,
                            cursor: clearingDb ? 'wait' : 'pointer'
                          }}
                        >
                          {clearingDb ? 'Clearing…' : 'Yes, Reset to 0'}
                        </button>
                        <button
                          type="button"
                          onClick={() => setConfirmingClear(false)}
                          disabled={clearingDb}
                          style={{
                            flex: 1,
                            padding: '5px',
                            background: 'var(--bg-2)',
                            color: 'var(--text-2)',
                            border: '1px solid var(--border-2)',
                            borderRadius: '4px',
                            fontSize: '11px',
                            cursor: 'pointer'
                          }}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                  {clearSuccess && (
                    <div style={{ marginTop: '5px', color: '#10b981', fontSize: '11px', textAlign: 'center', fontWeight: 600 }}>
                      ✓ Database cleaned back to 0!
                    </div>
                  )}
                </div>

                {/* Divider */}
                <div style={{ borderTop: '1px solid var(--border-2)' }} />

                {/* Stats */}
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: 'var(--text-2)' }}>⚡ Tokens Spent:</span>
                  <span style={{ fontWeight: 600, color: 'var(--text-1)' }}>{tokens.toLocaleString()}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: 'var(--text-2)' }}>🔌 Backend:</span>
                  <span style={{ fontSize: '11px', color: 'var(--text-3)' }}>127.0.0.1:8765</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: 'var(--text-2)' }}>📡 Provider:</span>
                  <span style={{ fontWeight: 500 }}>Ollama (Local)</span>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* New Chat */}
        <button className="hdr-btn hdr-btn-primary" onClick={onNewChat}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
               strokeLinecap="round" strokeLinejoin="round">
            <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
          </svg>
          New Chat
        </button>
      </div>
    </header>
  );
}

