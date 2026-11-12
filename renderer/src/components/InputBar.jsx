import React, { useState, useRef, useCallback, useEffect } from 'react';

export default function InputBar({ connected, streaming, onSend, onStop, prefill, onPrefillConsumed, responseTime }) {
  const [text,        setText]        = useState('');
  const [attachments, setAttachments] = useState([]);
  const [recording,   setRecording]   = useState(false);
  const textareaRef = useRef(null);

  // Sync prefill from parent (card / tool-pill clicks)
  useEffect(() => {
    if (!prefill) return;
    setText(prefill);
    onPrefillConsumed?.();
    const el = textareaRef.current;
    if (el) {
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 120) + 'px';
      el.focus();
    }
  }, [prefill, onPrefillConsumed]);

  const canSend = (text.trim() || attachments.length > 0) && connected && !streaming;

  const handleInput = (e) => {
    setText(e.target.value);
    // Auto-resize
    const el = textareaRef.current;
    if (el) {
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 120) + 'px';
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const submit = useCallback(() => {
    if (!canSend) return;
    const sendText = text.trim() || (attachments.length ? 'Please summarise the attached document and highlight key points.' : '');
    onSend(sendText, attachments);
    setText('');
    setAttachments([]);
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
  }, [canSend, text, attachments, onSend]);

  const removeAttachment = (pathToRemove) => {
    setAttachments(prev => prev.filter(f => f.path !== pathToRemove));
  };

  const handleAttach = async () => {
    if (window.aurora) {
      const res = await window.aurora.dialog.openFile();
      if (!res.canceled && res.filePaths.length) {
        const newFiles = res.filePaths.map(fp => {
          const name = fp.split(/[\\/]/).pop();
          const ext = name.slice(name.lastIndexOf('.')).toLowerCase();
          const isCode = ['.py', '.js', '.ts', '.jsx', '.tsx'].includes(ext);
          const isImage = ['.jpg', '.jpeg', '.png', '.webp', '.bmp'].includes(ext);
          return { path: fp, name, ext, isCode, isImage, indexing: !isImage, chunks: 0 };
        });

        setAttachments(prev => [...prev, ...newFiles]);

        if (!text.trim()) {
          const hasImage = newFiles.some(f => f.isImage);
          setText(hasImage ? 'Analyze and describe this image' : 'Summarise this document');
        }

        // Trigger background chunking / AST indexing right away (skip for images)
        for (const file of newFiles) {
          if (file.isImage) continue;
          try {
            const body = file.isCode ? { code_path: file.path } : { source: file.path };
            const apiRes = await fetch('http://127.0.0.1:8765/memory/chunks', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(body),
            });
            if (apiRes.ok) {
              const data = await apiRes.json();
              const count = file.isCode
                ? data.summary?.total_ast_symbols || 1
                : data.rag?.count || data.summary?.total_rag_chunks || 1;
              setAttachments(prev =>
                prev.map(f => f.path === file.path ? { ...f, indexing: false, chunks: count } : f)
              );
            }
          } catch (_) {
            setAttachments(prev =>
              prev.map(f => f.path === file.path ? { ...f, indexing: false } : f)
            );
          }
        }
        textareaRef.current?.focus();
      }
    }
  };

  const handleSource = async () => {
    if (window.aurora) {
      const res = await window.aurora.dialog.openFolder();
      if (!res.canceled && res.filePaths.length) {
        setText(`Search in folder: ${res.filePaths[0]}`);
        textareaRef.current?.focus();
      }
    }
  };

  return (
    <div className="input-zone">
      <div className="input-card">
        {/* Attachment chips */}
        {attachments.length > 0 && (
          <div className="input-attachments">
            {attachments.map((att, i) => (
              <div key={i} className="attachment-chip">
                <span className="chip-icon">{att.isImage ? '🖼️' : (att.isCode ? '💻' : '📄')}</span>
                <span className="chip-name" title={att.path}>{att.name}</span>
                <span className="chip-status">
                  {att.isImage ? '✓ Vision Ready' : att.indexing
                    ? '⚡ Chunking...'
                    : (att.isCode ? '✓ AST Parsed' : `✓ ${att.chunks || ''} Chunks Ready`)}
                </span>
                <button
                  type="button"
                  className="chip-remove"
                  onClick={() => removeAttachment(att.path)}
                  title="Remove attachment"
                >✕</button>
              </div>
            ))}
          </div>
        )}

        {/* Prompt row */}
        <div className="input-row">
          <div className="input-sparkle">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                 strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2l2.4 7.4H22l-6.2 4.5 2.4 7.4L12 17l-6.2 4.3 2.4-7.4L2 9.4h7.6z"/>
            </svg>
          </div>
          <textarea
            ref={textareaRef}
            id="promptInput"
            rows={1}
            placeholder="Ask me anything..."
            value={text}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
          />
        </div>

        {/* Actions row */}
        <div className="input-actions">
          <div className="input-left">
            {/* 1. Source Pill */}
            <div className="source-pill" onClick={handleSource}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                   strokeLinecap="round" strokeLinejoin="round">
                <ellipse cx="12" cy="5" rx="9" ry="3"/>
                <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/>
                <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
              </svg>
              Select Source
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
                   strokeLinecap="round" strokeLinejoin="round" style={{ width: 11, height: 11 }}>
                <polyline points="6 9 12 15 18 9"/>
              </svg>
            </div>

            {/* 2. Response Time Badge */}
            {responseTime && (
              <div className="response-time-badge" title="Time taken for last response">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 12, height: 12 }}>
                  <circle cx="12" cy="12" r="10" />
                  <polyline points="12 6 12 12 16 14" />
                </svg>
                <span>{responseTime}s</span>
              </div>
            )}
          </div>

          <div className="input-right">
            {/* Attach */}
            <button className="action-btn" onClick={handleAttach}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                   strokeLinecap="round" strokeLinejoin="round">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
              </svg>
              Attach
            </button>

            {/* Voice */}
            <button
              className={`action-btn${recording ? ' recording' : ''}`}
              onClick={() => setRecording(r => !r)}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                   strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                <line x1="12" y1="19" x2="12" y2="23"/>
                <line x1="8" y1="23" x2="16" y2="23"/>
              </svg>
              Voice
            </button>

            {/* Send or Stop */}
            {streaming ? (
              <button
                id="sendBtn"
                className="stop-btn"
                onClick={onStop}
                title="Stop generation & unlock input"
              >
                <svg viewBox="0 0 24 24" fill="currentColor">
                  <rect x="6" y="6" width="12" height="12" rx="2" />
                </svg>
              </button>
            ) : (
              <button id="sendBtn" onClick={submit} disabled={!canSend} title="Send message">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                     strokeLinecap="round" strokeLinejoin="round">
                  <line x1="12" y1="19" x2="12" y2="5"/>
                  <polyline points="5 12 12 5 19 12"/>
                </svg>
              </button>
            )}
          </div>
        </div>
      </div>

      <p className="disclaimer">
        Aurora may display inaccurate info — always verify important decisions.{' '}
        <a href="#" onClick={e => e.preventDefault()}>Your Privacy</a> &amp;{' '}
        <a href="#" onClick={e => e.preventDefault()}>Aurora</a>
      </p>
    </div>
  );
}
