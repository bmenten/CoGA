import React from 'react';
import { Link } from 'react-router';
import InfoTip from './InfoTip';
import TruncatedText from './TruncatedText';

export interface GeneDiseaseHeadingDisease {
  name: string;
  /** The disease's OMIM entry or search; none, and the name stands without a link. */
  omimUrl?: string | null;
}

/** Longer disease names are cut on screen, the whole name in their tooltip. */
export const DISEASE_NAME_MAX = 44;

// A disease name that leads to OMIM. A cut name keeps its full text as the link's name and
// in the tooltip, so the link is never announced by its cut half.
const DiseaseName: React.FC<{ disease: GeneDiseaseHeadingDisease; max: number }> = ({
  disease,
  max,
}) => {
  if (!disease.omimUrl) return <TruncatedText text={disease.name} max={max} />;
  const label = `${disease.name} on OMIM (opens in a new tab)`;
  const cut = disease.name.length > max;
  const link = (
    <a
      href={disease.omimUrl}
      target="_blank"
      rel="noopener noreferrer"
      className={`gene-disease-heading-link${cut ? ' truncated-text truncated-text--cut' : ''}`}
      aria-label={label}
    >
      {cut ? (
        <>
          <span aria-hidden="true">{`${disease.name.slice(0, max).trimEnd()}…`}</span>
          <span className="sr-only">{disease.name}</span>
        </>
      ) : (
        disease.name
      )}
    </a>
  );
  return cut ? (
    <InfoTip interactiveChild label={disease.name}>
      {link}
    </InfoTip>
  ) : (
    link
  );
};

/**
 * A finding's heading, the same on every variant card: the gene first, linked to its gene
 * page, then "— disease; disease", each disease leading to OMIM. A long disease name is cut
 * on screen with the full name in a tooltip; the gene is never cut. `children` follow on the
 * same line: what the finding is (its type or status chips).
 */
const GeneDiseaseHeading: React.FC<{
  gene: string;
  /** The gene page; without one the gene stands unlinked (an intergenic or control region). */
  geneHref?: string | null;
  diseases?: GeneDiseaseHeadingDisease[];
  diseaseMax?: number;
  children?: React.ReactNode;
}> = ({ gene, geneHref, diseases = [], diseaseMax = DISEASE_NAME_MAX, children }) => {
  const named = diseases.filter((disease) => disease.name.trim());
  return (
    <div className="variant-card-headline gene-disease-heading">
      {geneHref ? (
        <Link to={geneHref} className="variant-card-gene">
          {gene}
        </Link>
      ) : (
        <span className="variant-card-gene">{gene}</span>
      )}
      {named.length > 0 && (
        <>
          <span className="gene-disease-heading-sep" aria-hidden="true">
            —
          </span>
          <span className="gene-disease-heading-diseases">
            {named.map((disease, index) => (
              <span key={`${disease.name}-${index}`} className="gene-disease-heading-disease">
                <DiseaseName disease={disease} max={diseaseMax} />
                {index < named.length - 1 && <span aria-hidden="true">;</span>}
              </span>
            ))}
          </span>
        </>
      )}
      {children}
    </div>
  );
};

export default GeneDiseaseHeading;
