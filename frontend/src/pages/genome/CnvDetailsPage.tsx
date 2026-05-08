import React, { useMemo } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import PageState from '../../components/PageState';

const CnvDetailsPage: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const html = (location.state as { html?: string } | null)?.html;

  const bodyContent = useMemo(() => {
    if (!html) return '';
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, 'text/html');
    return doc.body.innerHTML;
  }, [html]);

  return (
    <div className="page-shell content-shell">
      {bodyContent ? (
        <section className="surface-card">
          <div className="content-html" dangerouslySetInnerHTML={{ __html: bodyContent }} />
          <div className="mt-6">
            <button className="form-button" onClick={() => navigate(-1)}>
              Back
            </button>
          </div>
        </section>
      ) : (
        <PageState
          kicker="Details"
          title="No details available."
          message="This view did not receive any rendered detail content."
          action={<button onClick={() => navigate(-1)}>Back</button>}
        />
      )}
    </div>
  );
};

export default CnvDetailsPage;
