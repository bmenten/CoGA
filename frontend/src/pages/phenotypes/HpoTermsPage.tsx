import React from 'react';
import { Link, useSearchParams } from 'react-router-dom';

const normalizeTerms = (values: string[]) =>
  Array.from(
    new Set(
      values
        .flatMap((value) => value.split(/[;,|]/))
        .map((value) => value.trim())
        .filter(Boolean),
    ),
  ).sort((left, right) => left.localeCompare(right));

const hpoBrowserHref = (term: string) =>
  `https://hpo.jax.org/browse/term/${encodeURIComponent(term)}`;

const HpoTermsPage: React.FC = () => {
  const [searchParams] = useSearchParams();
  const terms = normalizeTerms(searchParams.getAll('term'));
  const familyId = searchParams.get('family_id') || '';

  return (
    <div className="page-shell analysis-shell">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">HPO</p>
            <h1 className="catalog-card-title">Phenotype terms</h1>
            <p className="catalog-card-copy">
              {terms.length
                ? `${terms.length} HPO ${terms.length === 1 ? 'term' : 'terms'} selected from structural variant annotations.`
                : 'No HPO terms were provided.'}
            </p>
          </div>
          {familyId ? (
            <Link to={`/families/${familyId}/structural-variants`} className="button-secondary hover:no-underline">
              Back to SVs
            </Link>
          ) : null}
        </div>
      </section>

      <section className="surface-card space-y-4">
        <div className="variant-results-toolbar">
          <div className="space-y-1">
            <h2 className="section-title">Selected terms</h2>
            <p className="table-subtle">Open a term in the Human Phenotype Ontology browser.</p>
          </div>
        </div>
        {terms.length ? (
          <div className="hpo-term-grid">
            {terms.map((term) => (
              <a
                key={term}
                href={hpoBrowserHref(term)}
                target="_blank"
                rel="noreferrer"
                className="hpo-term-card"
              >
                <span className="table-mono">{term}</span>
                <span className="table-subtle">Open HPO</span>
              </a>
            ))}
          </div>
        ) : (
          <p className="table-empty">No HPO terms to display.</p>
        )}
      </section>
    </div>
  );
};

export default HpoTermsPage;
