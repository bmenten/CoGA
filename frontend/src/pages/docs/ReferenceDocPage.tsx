import React from 'react';
import { Link, useParams } from 'react-router';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

import PageState from '../../components/PageState';
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

  return (
    <div className="page-shell content-shell reference-doc-page">
      <div className="reference-doc-breadcrumb">
        <Link to="/docs" className="subtle-link">
          ← User guide
        </Link>
        <span className="page-kicker">In-depth reference</span>
      </div>
      <article className="content-prose reference-doc-prose">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
          {doc.markdown}
        </ReactMarkdown>
      </article>
    </div>
  );
};

export default ReferenceDocPage;
