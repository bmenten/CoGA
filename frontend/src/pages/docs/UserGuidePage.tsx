import React from 'react';
import { Link } from 'react-router';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { guideSections, type GuideLink } from './userGuideSections';

// Links a guide section to an in-depth, in-app reference document (rendered from a
// bundled Markdown file at /docs/reference/:slug). In a section's Markdown it is a
// paragraph holding one link titled "further-reading".
const FurtherReading: React.FC<{ to: string; label: React.ReactNode }> = ({ to, label }) => (
  <p className="user-guide-further-reading">
    <span className="user-guide-further-reading-label">In-depth reference</span>
    <Link to={to} className="user-guide-doc-link">
      {label} →
    </Link>
  </p>
);

type HastNode = { type: string; tagName?: string; value?: string; properties?: Record<string, unknown>; children?: HastNode[] };

const textOf = (node: HastNode | undefined): string =>
  node?.type === 'text' ? (node.value ?? '') : (node?.children ?? []).map(textOf).join('');

// A ```cards fence: one "Title | Copy" line per card.
const CardGrid: React.FC<{ source: string }> = ({ source }) => (
  <div className="user-guide-mini-grid">
    {source
      .split('\n')
      .filter((line) => line.trim())
      .map((line) => {
        const [title, ...copy] = line.split(' | ');
        return (
          <div key={title} className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">{title.trim()}</p>
            <p className="user-guide-mini-card-copy">{copy.join(' | ').trim()}</p>
          </div>
        );
      })}
  </div>
);

// How the guide's Markdown renders: routes through the router, in-page anchors as plain
// links, the one external link in a new tab; blockquotes as callouts; tables in their
// scroll wrapper; the two conventions above.
const guideComponents: Components = {
  a: ({ href = '', children }) => {
    if (href.startsWith('/')) return <Link to={href}>{children}</Link>;
    if (href.startsWith('#')) return <a href={href}>{children}</a>;
    return (
      <a href={href} target="_blank" rel="noreferrer">
        {children}
      </a>
    );
  },
  p: ({ node, children }) => {
    const only = (node as HastNode | undefined)?.children;
    const link = only?.length === 1 ? only[0] : undefined;
    if (link?.tagName === 'a' && link.properties?.title === 'further-reading') {
      return <FurtherReading to={String(link.properties?.href ?? '')} label={textOf(link)} />;
    }
    return <p>{children}</p>;
  },
  blockquote: ({ children }) => <div className="user-guide-callout">{children}</div>,
  pre: ({ node, children }) => {
    const code = (node as HastNode | undefined)?.children?.[0];
    const className = code?.properties?.className;
    if (Array.isArray(className) && className.includes('language-cards')) {
      return <CardGrid source={textOf(code)} />;
    }
    return <pre>{children}</pre>;
  },
  table: ({ children }) => (
    <div className="content-table-wrap">
      <table>{children}</table>
    </div>
  ),
};

const GuideMarkdown: React.FC<{ markdown: string }> = ({ markdown }) => (
  <ReactMarkdown remarkPlugins={[remarkGfm]} components={guideComponents}>
    {markdown}
  </ReactMarkdown>
);

const formatSectionIndex = (index: number) => String(index + 1).padStart(2, '0');

const WorkspaceLinkRow: React.FC<{ links: GuideLink[] }> = ({ links }) => (
  <div className="user-guide-link-row">
    {links.map((link) => (
      <Link
        key={`${link.to}:${link.label}`}
        to={link.to}
        className="user-guide-link-chip"
        // Spelled out rather than left to be concatenated from the two spans.
        // Accessible-name computation separates descendants with a space only when
        // they are *blockified*, so this chip announces "Dashboard Start here" purely
        // because the stylesheet makes it `inline-flex`. Strip the stylesheet — as
        // jsdom does — and the identical markup announces "DashboardStart here". An
        // accessible name should not depend on whether the CSS loaded.
        aria-label={link.note ? `${link.label} ${link.note}` : link.label}
      >
        <span>{link.label}</span>
        {link.note ? <span className="user-guide-link-note">{link.note}</span> : null}
      </Link>
    ))}
  </div>
);

const UserGuidePage: React.FC = () => (
  <div className="page-shell content-shell user-guide-page">
    <header className="user-guide-hero">
      <p className="page-kicker">Documentation</p>
      <h1 className="user-guide-title">CoGA user guide</h1>
      <p className="user-guide-lede">
        An in-app manual for lab staff and administrators. It follows a case from setup to
        sign-out: each section says what a page is for and how to use it, and links to the
        in-depth reference that holds the rules.
      </p>
    </header>

    <div className="user-guide-layout">
      <aside id="user-guide-contents" className="user-guide-toc">
        <p className="user-guide-eyebrow">On this page</p>
        <nav aria-label="User guide contents">
          <ol className="user-guide-toc-list">
            {guideSections.map((section, index) => (
              <li key={section.id}>
                <a href={`#${section.id}`} className="user-guide-toc-link">
                  <span className="user-guide-toc-index">{formatSectionIndex(index)}</span>
                  <span className="user-guide-toc-title">{section.title}</span>
                </a>
              </li>
            ))}
          </ol>
        </nav>
      </aside>

      <div className="user-guide-content">
        {guideSections.map((section, index) => (
          <section key={section.id} id={section.id} className="user-guide-section">
            <div className="user-guide-section-header">
              <span className="user-guide-section-index">{formatSectionIndex(index)}</span>
              <h2 className="user-guide-section-title">{section.title}</h2>
              <p className="user-guide-section-summary">{section.summary}</p>
            </div>
            {section.quickLinks?.length ? <WorkspaceLinkRow links={section.quickLinks} /> : null}
            <div className="content-prose user-guide-section-prose">
              <GuideMarkdown markdown={section.markdown} />
            </div>
            <a href="#user-guide-contents" className="subtle-link user-guide-backlink">
              Back to top
            </a>
          </section>
        ))}
      </div>
    </div>
  </div>
);

export default UserGuidePage;
