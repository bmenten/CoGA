import React from 'react';
import { Link, useParams } from 'react-router';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

import PageHeader from '../../components/PageHeader';
import PageState from '../../components/PageState';
import { createHeadingSlugger, rehypeHeadingIds } from './headingIds';
import { referenceDocBySlug } from './referenceDocs';

// Reuse the app's prose styles: wrap GFM tables so they scroll, and route
// root-relative links through React Router (external links open in a new tab).
const markdownComponents: Components = {
  table: (props) => (
    <div className="content-table-wrap">
      <table>{props.children}</table>
    </div>
  ),
  a: ({ href = '', children }) => {
    if (href.startsWith('/')) {
      return <Link to={href}>{children}</Link>;
    }
    const external = /^https?:/i.test(href);
    return (
      <a href={href} {...(external ? { target: '_blank', rel: 'noreferrer' } : {})}>
        {children}
      </a>
    );
  },
};

// A reference doc opens with its title, `# … — reference`: the page header shows it, with the id
// GitHub gives that heading, and the rest of the document renders below it.
const TITLE_LINE = /^#[ \t]+(.+?)[ \t]*#*[ \t]*(?:\r?\n|$)/;

const splitTitle = (markdown: string): { title: string | null; body: string } => {
  const match = TITLE_LINE.exec(markdown);
  return match ? { title: match[1], body: markdown.slice(match[0].length) } : { title: null, body: markdown };
};

const ReferenceDocPage: React.FC = () => {
  const { slug = '' } = useParams<{ slug: string }>();
  const doc = referenceDocBySlug.get(slug);

  if (!doc) {
    return (
      <PageState
        kicker="Documentation"
        title="Reference document not found"
        message="This in-depth reference does not exist (or has been renamed)."
        action={
          <Link to="/docs" className="button-secondary">
            Back to the user guide
          </Link>
        }
      />
    );
  }

  const { title, body } = splitTitle(doc.markdown);

  return (
    <div className="page-shell content-shell reference-doc-page">
      <PageHeader
        kicker="In-depth reference"
        title={title ?? 'Reference'}
        titleId={title ? createHeadingSlugger()(title) : undefined}
        actions={
          <Link to="/docs" className="subtle-link">
            ← User guide
          </Link>
        }
      />
      <article className="content-prose reference-doc-prose">
        {/* Each heading gets the id GitHub gives it, which the docs' own in-page links name. */}
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[rehypeHeadingIds]}
          components={markdownComponents}
        >
          {body}
        </ReactMarkdown>
      </article>
    </div>
  );
};

export default ReferenceDocPage;
