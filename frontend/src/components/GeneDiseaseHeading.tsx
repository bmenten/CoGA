import React from 'react';
import OmimLink from './OmimLink';
import TruncatedText from './TruncatedText';

export interface GeneDiseaseHeadingDisease {
  name: string;
  /** The disease's OMIM entry or search; none, and the name stands without a link. */
  omimUrl?: string | null;
}

/** Longer disease names are cut on screen, the whole name in their tooltip. */
export const DISEASE_NAME_MAX = 44;

/**
 * A finding's heading as the repeat, Paraphase and mtDNA pages write it:
 * "GENE — Disease name (OMIM)", the gene first and prominent, each disease after it with its
 * OMIM link. A long disease name is cut on screen with the full name in a tooltip
 * (TruncatedText); the gene is never cut. `children` follow on the same line: status chips.
 */
const GeneDiseaseHeading: React.FC<{
  gene: React.ReactNode;
  diseases?: GeneDiseaseHeadingDisease[];
  diseaseMax?: number;
  className?: string;
  children?: React.ReactNode;
}> = ({ gene, diseases = [], diseaseMax = DISEASE_NAME_MAX, className, children }) => {
  const named = diseases.filter((disease) => disease.name.trim());
  return (
    <div className={`gene-disease-heading${className ? ` ${className}` : ''}`}>
      <span className="gene-disease-heading-gene">{gene}</span>
      {named.length > 0 && (
        <>
          <span className="gene-disease-heading-sep" aria-hidden="true">
            —
          </span>
          <span className="gene-disease-heading-diseases">
            {named.map((disease, index) => (
              <span key={`${disease.name}-${index}`} className="gene-disease-heading-disease">
                <TruncatedText text={disease.name} max={diseaseMax} />
                {disease.omimUrl && (
                  <>
                    {' '}
                    <OmimLink href={disease.omimUrl} subject={disease.name} />
                  </>
                )}
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
