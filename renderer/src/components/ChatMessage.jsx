import React, { useState } from 'react';

/** Format inline markdown elements like **bold**, `code`, *italic*, ~~strikethrough~~, and [links](url). */
function formatInline(text) {
  if (!text) return '';
  const tokens = text.split(/(\*\*.*?\*\*|`.*?`|\*.*?\*|~~.*?~~|\[.*?\]\(.*?\))/g);

  return tokens.map((token, i) => {
    if (!token) return null;
    if (token.startsWith('**') && token.endsWith('**') && token.length >= 4) {
      return <strong key={i}>{token.slice(2, -2)}</strong>;
    }
    if (token.startsWith('`') && token.endsWith('`') && token.length >= 2) {
      return <code key={i} className="inline-code">{token.slice(1, -1)}</code>;
    }
    if (token.startsWith('*') && token.endsWith('*') && token.length >= 2) {
      return <em key={i}>{token.slice(1, -1)}</em>;
    }
    if (token.startsWith('~~') && token.endsWith('~~') && token.length >= 4) {
      return <del key={i}>{token.slice(2, -2)}</del>;
    }
    const linkMatch = token.match(/^\[(.*?)\]\((.*?)\)$/);
    if (linkMatch) {
      return (
        <a key={i} href={linkMatch[2]} target="_blank" rel="noreferrer" className="md-link">
          {linkMatch[1]}
        </a>
      );
    }
    return token;
  });
}

/** Format a bullet list item, detecting key-value specs like "Dimension: Nether" (Image 1 style) */
function formatBulletItem(item) {
  const match = item.match(/^([A-Za-z0-9_\-\s]{1,30}):\s+(.+)$/);
  if (match) {
    return (
      <>
        <strong className="md-spec-key">{match[1]}:</strong>{' '}
        <span className="md-spec-val">{formatInline(match[2])}</span>
      </>
    );
  }
  return formatInline(item);
}

/** Code block component with copy button. */
function CodeBlock({ lang, code }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard?.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="code-block-wrapper">
      <div className="code-block-header">
        <span className="code-lang">{lang || 'plaintext'}</span>
        <button type="button" className="code-copy-btn" onClick={handleCopy}>
          {copied ? '✓ Copied' : 'Copy'}
        </button>
      </div>
      <pre className="code-block">
        <code>{code}</code>
      </pre>
    </div>
  );
}

/** Collapsible Section for Omitted Text, Metrics, or Secondary Data (Image 2 Fix) */
function CollapsibleSection({ icon, title, subtitle, children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className={`md-collapsible-card ${open ? 'is-open' : 'is-collapsed'}`}>
      <div className="md-collapsible-header" onClick={() => setOpen(prev => !prev)}>
        <div className="md-collapsible-header-left">
          {icon && <span className="md-collapsible-icon">{icon}</span>}
          <span className="md-collapsible-title">{title}</span>
          {subtitle && <span className="md-collapsible-subtitle">{subtitle}</span>}
        </div>
        <div className="md-collapsible-header-right">
          <span className="md-collapsible-badge">{open ? 'Hide' : 'Show details'}</span>
          <span className="md-collapsible-arrow">{open ? '▼' : '▶'}</span>
        </div>
      </div>
      {open && <div className="md-collapsible-content">{children}</div>}
    </div>
  );
}

/** ChatGPT-Style Featured Card (Image 1 Feature) */
function FeatureCard({ badge, title, desc, bullets, children }) {
  return (
    <div className="md-feature-card">
      {badge && <div className="md-feature-badge">{badge}</div>}
      {title && <div className="md-feature-title">{formatInline(title)}</div>}
      {desc && <p className="md-feature-desc">{formatInline(desc)}</p>}
      {bullets && bullets.length > 0 && (
        <ul className="md-feature-list">
          {bullets.map((b, i) => (
            <li key={i}>{formatBulletItem(b)}</li>
          ))}
        </ul>
      )}
      {children}
    </div>
  );
}

/** Check if a line looks like a markdown table row (e.g. | col 1 | col 2 | or col 1 | col 2). */
function isTableLine(line) {
  const trimmed = line.trim();
  if (!trimmed) return false;
  return trimmed.startsWith('|') || (trimmed.includes('|') && trimmed.split('|').length >= 3);
}

/** Parse consecutive markdown table lines into headers and body rows. */
function parseTableRows(lines) {
  const parsedRows = lines.map(line => {
    let raw = line.trim();
    if (raw.startsWith('|')) raw = raw.slice(1);
    if (raw.endsWith('|')) raw = raw.slice(0, -1);
    return raw.split('|').map(c => c.trim());
  });

  if (parsedRows.length === 0) return null;

  // Check if second row is markdown delimiter (e.g. |---|---| or |:---|:---:|)
  const isDelimiter = (row) => row.length > 0 && row.every(cell => /^:?-+:?$/.test(cell.trim()));

  let headers = [];
  let bodyRows = [];

  if (parsedRows.length >= 2 && isDelimiter(parsedRows[1])) {
    headers = parsedRows[0];
    bodyRows = parsedRows.slice(2);
  } else if (parsedRows.length >= 2 && !parsedRows[0].some(c => /\d{2,}/.test(c)) && (parsedRows[0].join('').length < parsedRows[1].join('').length)) {
    const looksLikeData = /^Day\s*\d|\b\d+\s*(?:hrs?|mins?|Qs)\b/i.test(parsedRows[0][0]);
    if (!looksLikeData) {
      headers = parsedRows[0];
      bodyRows = parsedRows.slice(1);
    } else {
      headers = [];
      bodyRows = parsedRows;
    }
  } else {
    headers = [];
    bodyRows = parsedRows;
  }

  return { headers, bodyRows };
}

/** Render parsed markdown blocks */
function renderBlockElements(lines) {
  const elements = [];
  let listItems = [];
  let orderedItems = [];

  const flushList = () => {
    if (listItems.length > 0) {
      elements.push(
        <ul key={`ul-${elements.length}`} className="md-list">
          {listItems.map((item, i) => (
            <li key={i}>{formatBulletItem(item)}</li>
          ))}
        </ul>
      );
      listItems = [];
    }
    if (orderedItems.length > 0) {
      elements.push(
        <ol key={`ol-${elements.length}`} className="md-ol">
          {orderedItems.map((item, i) => {
            const dashIdx = item.indexOf(' — ');
            if (dashIdx !== -1) {
              const head = item.slice(0, dashIdx);
              const rest = item.slice(dashIdx + 3);
              return (
                <li key={i}>
                  <strong className="md-ol-title">{head}</strong>
                  <span className="md-ol-sub"> — {formatInline(rest)}</span>
                </li>
              );
            }
            return <li key={i}>{formatInline(item)}</li>;
          })}
        </ol>
      );
      orderedItems = [];
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    // 1. Table Detection (Image 2 Fix)
    if (isTableLine(line)) {
      flushList();
      const tableLines = [line];
      while (i + 1 < lines.length && isTableLine(lines[i + 1])) {
        i++;
        tableLines.push(lines[i]);
      }
      const parsed = parseTableRows(tableLines);
      if (parsed) {
        elements.push(
          <div key={`tbl-${elements.length}`} className="md-table-wrapper">
            <table className="md-table">
              {parsed.headers.length > 0 && (
                <thead>
                  <tr>
                    {parsed.headers.map((h, hIdx) => (
                      <th key={hIdx}>{formatInline(h)}</th>
                    ))}
                  </tr>
                </thead>
              )}
              <tbody>
                {parsed.bodyRows.map((row, rIdx) => (
                  <tr key={rIdx}>
                    {row.map((cell, cIdx) => (
                      <td key={cIdx} className={cIdx === 0 ? 'md-col-lead' : ''}>
                        {formatInline(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      }
      continue;
    }

    // 2. Horizontal Rule (---, ***, ___)
    if (/^(\s*[-*_]\s*){3,}$/.test(trimmed)) {
      flushList();
      elements.push(<hr key={`hr-${elements.length}`} className="md-hr" />);
      continue;
    }

    // 3. Collapsible Metrics & Omitted Sections (Image 2 Fix: 🔄 Completion Metrics)
    const metricMatch = trimmed.match(/^(?:#{1,4}\s*)?(🔄|⏱️|📊|📈|📉|📋|📁|📌)\s+(.+)$/);
    if (metricMatch) {
      flushList();
      const icon = metricMatch[1];
      const title = metricMatch[2];
      const sectionLines = [];
      while (i + 1 < lines.length) {
        const nextTrim = lines[i + 1].trim();
        if (/^(\s*[-*_]\s*){3,}$/.test(nextTrim) || /^#{1,3}\s+/.test(nextTrim)) break;
        i++;
        sectionLines.push(lines[i]);
      }
      elements.push(
        <CollapsibleSection key={`sec-${elements.length}`} icon={icon} title={title}>
          {sectionLines.length > 0 ? (
            renderBlockElements(sectionLines)
          ) : (
            <p className="md-metric-empty">Section metrics recorded.</p>
          )}
        </CollapsibleSection>
      );
      continue;
    }

    // 4. Section Heading with Icon (Image 1 Feature: ⛏️ Best mining methods)
    const iconHeaderMatch = trimmed.match(/^([\uD800-\uDBFF][\uDC00-\uDFFF]|[\u2600-\u27BF]|\p{Emoji})\s+(.+)$/u);
    if (iconHeaderMatch && !trimmed.startsWith('#') && !trimmed.startsWith('•') && !trimmed.startsWith('-')) {
      flushList();
      elements.push(
        <div key={`shead-${elements.length}`} className="md-section-header">
          <span className="md-section-icon">{iconHeaderMatch[1]}</span>
          <span className="md-section-title">{formatInline(iconHeaderMatch[2])}</span>
        </div>
      );
      continue;
    }

    // 5. Unordered list item (- , * , • )
    if (trimmed.startsWith('- ') || trimmed.startsWith('* ') || trimmed.startsWith('• ')) {
      if (orderedItems.length > 0) flushList();
      listItems.push(trimmed.slice(2).trim());
      continue;
    }

    // 6. Ordered list item (1. , 2. )
    const numMatch = trimmed.match(/^(\d+)\.\s+(.*)$/);
    if (numMatch) {
      if (listItems.length > 0) flushList();
      orderedItems.push(numMatch[2]);
      continue;
    }

    flushList();

    // 7. Empty spacer
    if (!trimmed) {
      elements.push(<div key={`spacer-${elements.length}`} className="md-spacer" />);
      continue;
    }

    // 8. Blockquote / ChatGPT Featured Card (Image 1 Feature)
    if (trimmed.startsWith('>')) {
      const quoteLines = [trimmed.replace(/^>\s?/, '')];
      while (i + 1 < lines.length && lines[i + 1].trim().startsWith('>')) {
        i++;
        quoteLines.push(lines[i].trim().replace(/^>\s?/, ''));
      }

      // Check if this matches a ChatGPT feature card (badge + big title + description + bullets)
      const firstLine = quoteLines[0]?.trim();
      const isFeatureCard = firstLine && firstLine.length < 40 && (
        /^[A-Z0-9_\-\s]{4,35}$/.test(firstLine) ||
        firstLine.startsWith('[!') ||
        quoteLines.some(ql => ql.startsWith('# ') || ql.startsWith('## ') || /^\*\*[^*]+\*\*$/.test(ql))
      );

      if (isFeatureCard && quoteLines.length >= 2) {
        const badge = firstLine.replace(/^\[!.*?\]\s*/, '');
        let title = '';
        let desc = '';
        const cardBullets = [];

        for (let q = 1; q < quoteLines.length; q++) {
          const ql = quoteLines[q].trim();
          if (!ql) continue;
          if (!title && (ql.startsWith('# ') || ql.startsWith('## ') || /^\*\*[^*]+\*\*$/.test(ql))) {
            title = ql.replace(/^#+\s*/, '').replace(/^\*\*|\*\*$/g, '');
          } else if (ql.startsWith('- ') || ql.startsWith('• ') || ql.startsWith('* ')) {
            cardBullets.push(ql.slice(2).trim());
          } else if (!desc) {
            desc = ql;
          } else {
            cardBullets.push(ql);
          }
        }

        elements.push(
          <FeatureCard
            key={`feat-${elements.length}`}
            badge={badge}
            title={title}
            desc={desc}
            bullets={cardBullets}
          />
        );
        continue;
      }

      // Standard callout card
      elements.push(
        <div key={`callout-${elements.length}`} className="md-callout-card">
          {quoteLines.map((ql, qIdx) => (
            <p key={qIdx} className="md-callout-text">{formatInline(ql)}</p>
          ))}
        </div>
      );
      continue;
    }

    // 9. Headings
    if (trimmed.startsWith('#### ')) {
      elements.push(<h4 key={`h4-${elements.length}`} className="md-h4">{formatInline(trimmed.slice(5))}</h4>);
    } else if (trimmed.startsWith('### ')) {
      elements.push(<h3 key={`h3-${elements.length}`} className="md-h3">{formatInline(trimmed.slice(4))}</h3>);
    } else if (trimmed.startsWith('## ')) {
      elements.push(<h2 key={`h2-${elements.length}`} className="md-h2">{formatInline(trimmed.slice(3))}</h2>);
    } else if (trimmed.startsWith('# ')) {
      elements.push(<h1 key={`h1-${elements.length}`} className="md-h1">{formatInline(trimmed.slice(2))}</h1>);
    } else {
      elements.push(<p key={`p-${elements.length}`} className="md-p">{formatInline(line)}</p>);
    }
  }

  flushList();
  return elements;
}

/** Render full markdown blocks (headings, code blocks, tables, lists, quotes, paragraphs). */
function renderMarkdown(text) {
  if (!text) return null;

  // Split text by triple-backtick code blocks
  const parts = text.split(/(```[\s\S]*?```)/g);

  return parts.map((part, index) => {
    if (part.startsWith('```') && part.endsWith('```')) {
      const firstLineEnd = part.indexOf('\n');
      const lang = firstLineEnd !== -1 ? part.slice(3, firstLineEnd).trim() : '';
      const code = firstLineEnd !== -1 ? part.slice(firstLineEnd + 1, -3) : part.slice(3, -3);
      return <CodeBlock key={index} lang={lang} code={code} />;
    }

    const lines = part.split('\n');
    return <React.Fragment key={index}>{renderBlockElements(lines)}</React.Fragment>;
  });
}

/** Extract <think>...</think> content and final answer text. */
function extractThinkingAndAnswer(content) {
  if (!content) return { thinking: null, isThinking: false, answer: '' };

  const thinkStart = content.indexOf('<think>');
  if (thinkStart === -1) {
    return { thinking: null, isThinking: false, answer: content };
  }

  const thinkEnd = content.indexOf('</think>');
  if (thinkEnd === -1) {
    // Currently generating inside thinking tags
    const thinking = content.slice(thinkStart + 7);
    return { thinking, isThinking: true, answer: '' };
  }

  // Thinking completed
  const thinking = content.slice(thinkStart + 7, thinkEnd).trim();
  const answer = (content.slice(0, thinkStart) + content.slice(thinkEnd + 8)).trimStart();
  return { thinking, isThinking: false, answer };
}

/** Mini Collapsible Thinking Box Component */
function ThinkingBox({ thinking, isThinking }) {
  const [expanded, setExpanded] = useState(true);

  if (!thinking && !isThinking) return null;

  return (
    <div className={`thinking-box ${isThinking ? 'thinking-active' : 'thinking-done'}`}>
      <div className="thinking-header" onClick={() => setExpanded(prev => !prev)}>
        <div className="thinking-title">
          <span className="thinking-icon">🧠</span>
          <span>{isThinking ? 'Thinking Process...' : 'Thought Process'}</span>
          {isThinking && <span className="thinking-pulse" />}
        </div>
        <span className="thinking-toggle">{expanded ? '▼' : '▶'}</span>
      </div>
      {expanded && (
        <div className="thinking-body">
          <pre className="thinking-content">{thinking}</pre>
        </div>
      )}
    </div>
  );
}

/** Extract generated image URLs/paths from message content. */
function extractGeneratedImages(text) {
  if (!text) return [];
  const images = [];

  // 1. Detect markdown image tags: ![alt](url)
  const mdImgRegex = /!\[(.*?)\]\(((?:http:\/\/127\.0\.0\.1:8765)?\/generated\/[^\s)]+)\)/gi;
  let match;
  while ((match = mdImgRegex.exec(text)) !== null) {
    const url = match[2].startsWith('http') ? match[2] : `http://127.0.0.1:8765${match[2]}`;
    const filename = url.split('/').pop();
    if (!images.some(img => img.filename === filename)) {
      images.push({ alt: match[1] || 'Generated Visual', url, filename });
    }
  }

  // 2. Detect JSON or tool result strings: "url": "/generated/..."
  const jsonUrlRegex = /"(?:url|image_url)":\s*"(\/generated\/[^"]+)"/gi;
  while ((match = jsonUrlRegex.exec(text)) !== null) {
    const url = `http://127.0.0.1:8765${match[1]}`;
    const filename = url.split('/').pop();
    if (!images.some(img => img.filename === filename)) {
      images.push({ alt: 'Generated Image', url, filename });
    }
  }

  // 3. Detect raw file paths like data/generated/img_xxx.png or full paths
  const pathRegex = /(?:[A-Za-z]:[\\/][^"'\s\n]+[\\/]data[\\/]generated[\\/]|data[\\/]generated[\\/]|data[\\]generated[\\])(img_[A-Za-z0-9_]+\.png)/gi;
  while ((match = pathRegex.exec(text)) !== null) {
    const filename = match[1];
    const url = `http://127.0.0.1:8765/generated/${filename}`;
    if (!images.some(img => img.filename === filename)) {
      images.push({ alt: 'Generated Image', url, filename });
    }
  }

  return images;
}

/** Sub Image Box Component for Assistant Messages */
function GeneratedImageBox({ image }) {
  const [modalOpen, setModalOpen] = useState(false);
  const [hasError, setHasError] = useState(false);
  const [isLoaded, setIsLoaded] = useState(false);

  return (
    <>
      <div className="generated-image-card">
        <div className="generated-image-badge">
          <span>🎨 Generated Image</span>
          <span className="generated-image-filename">{image.filename}</span>
        </div>
        <div
          className={`generated-image-preview-wrapper ${hasError ? 'error-state' : ''}`}
          onClick={() => !hasError && isLoaded && setModalOpen(true)}
        >
          {hasError ? (
            <div className="generated-image-error-box">
              <span className="error-icon">⚠️</span>
              <p className="error-title">Image preview unavailable</p>
              <p className="error-sub">Could not load preview for {image.filename}</p>
              <a
                href={image.url}
                target="_blank"
                rel="noreferrer"
                className="generated-image-retry-btn"
                onClick={e => e.stopPropagation()}
              >
                Open directly in browser ↗
              </a>
            </div>
          ) : (
            <>
              {!isLoaded && (
                <div className="generated-image-skeleton">
                  <div className="skeleton-spinner" />
                  <span>Loading visual preview...</span>
                </div>
              )}
              <img
                src={image.url}
                alt={image.alt}
                className={`generated-image-img ${isLoaded ? 'is-loaded' : 'is-loading'}`}
                loading="lazy"
                onLoad={() => setIsLoaded(true)}
                onError={() => setHasError(true)}
              />
              {isLoaded && (
                <div className="generated-image-overlay">
                  <span>🔍 Click to expand</span>
                </div>
              )}
            </>
          )}
        </div>
        <div className="generated-image-footer">
          <span className="generated-image-model">● Fooocus Engine (0 MB VRAM)</span>
          <a
            href={image.url}
            target="_blank"
            rel="noreferrer"
            className="generated-image-btn"
          >
            ↗ Open in browser
          </a>
        </div>
      </div>

      {modalOpen && !hasError && (
        <div className="image-modal-overlay" onClick={() => setModalOpen(false)}>
          <div className="image-modal-content" onClick={e => e.stopPropagation()}>
            <img src={image.url} alt={image.alt} className="image-modal-img" />
            <button className="image-modal-close" onClick={() => setModalOpen(false)}>✕</button>
          </div>
        </div>
      )}
    </>
  );
}

/** Single Chat Message Component — renders Thinking Box, Markdown for Assistant, text and attachments for User. */
export default function ChatMessage({ role, content, streaming, attachments }) {
  if (role === 'user') {
    let atts = attachments || [];
    let displayText = content;

    // Detect if content contains "Attach and summarise: <path>" and convert to clean chip
    const match = content && content.match(/^Attach and summarise:\s*(.+)$/i);
    if (match && atts.length === 0) {
      const rawPath = match[1].trim();
      const filename = rawPath.split(/[\\/]/).pop();
      atts = [{
        name: filename,
        path: rawPath,
        isCode: /\.(py|js|ts|jsx|tsx)$/i.test(rawPath),
        chunks: null,
      }];
      displayText = 'Please summarise the attached document and outline key takeaways.';
    }

    return (
      <div className="msg-user">
        <div className="bubble">
          {atts.length > 0 && (
            <div className="msg-attachments-container">
              {atts.map((att, i) => (
                <div key={i} className="msg-attachment-pill">
                  <span className="msg-att-icon">{att.isCode ? '💻' : '📄'}</span>
                  <div className="msg-att-info">
                    <span className="msg-att-name">{att.name || att.path?.split(/[\\/]/).pop()}</span>
                    <span className="msg-att-sub">
                      {att.chunks
                        ? `${att.chunks} chunks loaded`
                        : (att.isCode ? 'AST Parsed' : 'RAG Document')}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
          <p className="user-msg-text">{displayText}</p>
        </div>
      </div>
    );
  }

  const { thinking, isThinking, answer } = extractThinkingAndAnswer(content);
  const generatedImages = extractGeneratedImages(content);

  return (
    <div className="msg-assistant">
      <div className="msg-assistant-avatar" />
      <div className="bubble">
        {thinking !== null && (
          <ThinkingBox thinking={thinking} isThinking={isThinking} />
        )}
        {answer ? renderMarkdown(answer) : null}
        {generatedImages.length > 0 && (
          <div className="generated-images-gallery">
            {generatedImages.map((img, idx) => (
              <GeneratedImageBox key={idx} image={img} />
            ))}
          </div>
        )}
        {streaming && (!isThinking || !thinking) && <span className="cursor" />}
      </div>
    </div>
  );
}
