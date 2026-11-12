import React from 'react';

const navItems = [
  { id: 'chat',  title: 'Chat',  path: <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/> },
  { id: 'files', title: 'Files', path: <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/> },
  { id: 'code',  title: 'Code',  path: <><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></> },
];

export default function GlobalNav({ dark, onToggleTheme }) {
  const [active, setActive] = React.useState('chat');

  return (
    <nav className="global-nav">
      {/* Logo orb */}
      <div className="nav-logo" title="Aurora" />

      {/* Nav icons */}
      <div className="nav-icons">
        {navItems.map(item => (
          <button
            key={item.id}
            className={`nav-icon${active === item.id ? ' active' : ''}`}
            title={item.title}
            onClick={() => setActive(item.id)}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                 strokeLinecap="round" strokeLinejoin="round">
              {item.path}
            </svg>
          </button>
        ))}
      </div>

      {/* Bottom controls */}
      <div className="nav-bottom">
        <button className="nav-theme-btn" title="Toggle dark mode" onClick={onToggleTheme}>
          {dark ? (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="5"/>
              <line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/>
              <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
              <line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/>
              <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
            </svg>
          )}
        </button>
        <div className="nav-avatar" title="Profile" style={{ position: 'relative' }}>
          A
          <span className="nav-status" />
        </div>
      </div>
    </nav>
  );
}
