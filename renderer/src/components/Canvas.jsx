import React, { useEffect, useRef } from 'react';
import ChatMessage from './ChatMessage';

const TASK_PROMPTS = [
  'Help me answer an RFP documentation.',
  'Conduct a competitor analysis for my product.',
  'Provide feedback on my communication strategy.',
];
const TOOL_PROMPTS = [
  'Connect my calendar and show upcoming events.',
  'Run the demo task for sales workflow.',
  'Show available integrations I can connect.',
  'What notes were shared with me recently?',
];

const TaskIcon = ({ d }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
       strokeLinecap="round" strokeLinejoin="round">{d}</svg>
);

export default function Canvas({ messages, onFillPrompt }) {
  const bottomRef = useRef(null);
  const isEmpty   = messages.length === 0;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  return (
    <div className="canvas" id="canvas">

      {/* ── Welcome state ───────────────────────────────── */}
      {isEmpty && (
        <>
          <div className="welcome">
            <div className="welcome-orb" />
            <div className="welcome-text">
              <h1>Hi, there 👋</h1>
              <p>Tell us what you need, and we'll handle the rest.</p>
            </div>
          </div>

          {/* Feature cards */}
          <div className="cards-row">
            {/* Data Assistant */}
            <div className="card card-assistant"
                 onClick={() => onFillPrompt('Help me manage sales and customer engagement.')}>
              <div className="card-assistant-header">
                <div className="card-person">
                  <span className="card-person-name" style={{ fontSize: '13.5px', fontWeight: 600 }}>Data Assistant</span>
                </div>
                <span className="data-badge">AI Module</span>
              </div>
              <p className="card-assistant-desc">
                Designed to help manage sales processes and maximize customer engagement.
              </p>
            </div>

            {/* Tasks */}
            <div className="card">
              <div className="card-tasks-label">
                Tasks
                <a href="#" onClick={e => e.preventDefault()}>View All</a>
              </div>
              {['Answer RFP documentation', 'Conduct a competitor analysis', 'Provide feedback on communication'].map((t, i) => (
                <div key={i} className="task-row" onClick={() => onFillPrompt(TASK_PROMPTS[i])}>
                  <TaskIcon d={
                    i === 0
                      ? <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></>
                      : i === 1
                        ? <><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></>
                        : <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                  } />
                  <span>{t}</span>
                </div>
              ))}
            </div>

            {/* Suggested prompt */}
            <div className="card" style={{ cursor: 'pointer' }}
                 onClick={() => onFillPrompt('What are the key benefits of Product 1 that I should highlight to potential clients?')}>
              <div className="card-prompt-dots">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                     strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/>
                </svg>
              </div>
              <div className="card-prompt-label">Suggested prompt</div>
              <p className="card-prompt-text">
                What are the key benefits of Product 1 that I should highlight to potential clients?
              </p>
            </div>
          </div>

          {/* Quick tools */}
          <div className="quick-tools">
            {[
              { icon: '📅', cls: 'tp-cal',  label: 'Connect Calendar' },
              { icon: '⚡', cls: 'tp-task', label: 'Demo Task' },
              { icon: '🔌', cls: 'tp-int',  label: 'Browse Integrations' },
              { icon: '📝', cls: 'tp-note', label: 'Shared in Notes' },
            ].map((t, i) => (
              <div key={i} className="tool-pill" onClick={() => onFillPrompt(TOOL_PROMPTS[i])}>
                <div className={`tool-pill-icon ${t.cls}`}>{t.icon}</div>
                {t.label}
              </div>
            ))}
          </div>
        </>
      )}

      {/* ── Chat history ─────────────────────────────────── */}
      {!isEmpty && (
        <div id="chatHistory">
          {messages.map(msg => (
            <ChatMessage
              key={msg.id}
              role={msg.role}
              content={msg.content}
              streaming={msg.streaming}
              attachments={msg.attachments}
            />
          ))}
          <div ref={bottomRef} />
        </div>
      )}

    </div>
  );
}
