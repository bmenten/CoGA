import { Fragment } from 'react';
import type {
  GeneAcmgSecondaryFindingOut,
  GeneDosageSensitivityOut,
  GeneInheritanceOut,
} from '../../lib/apiSchema.generated';
import { summarizeGeneDiseases, type GeneDiseaseLinks, type GeneDiseaseSummaryBasis } from './geneDiseaseLinks';

// Where the names on a card's top line come from, said after them.
const BASIS_NOTES: Record<GeneDiseaseSummaryBasis, string> = {
  causal: 'Monarch, causal',
  omim: 'OMIM',
  associated: 'Monarch, associated only, not causal',
};

// The longest list the expanded detail shows per source; the Gene Explorer has the rest.
const DETAIL_LIST_LENGTH = 8;

interface GeneDiseaseState {
  symbol?: string | null;
  // The gene's links, once the page's lookup answered.
  links?: GeneDiseaseLinks;
  failed: boolean;
  onRetry: () => void;
}

/**
 * The diseases the card's gene is linked to, in one line under the card's heading.
 *
 * A failed lookup says so rather than leaving the line out, which would read as a gene with no
 * known disease.
 */
export const GeneDiseaseSummaryLine = ({
  symbol,
  links,
  failed,
  onRetry,
  onShowAll,
}: GeneDiseaseState & { onShowAll: () => void }) => {
  if (!symbol) return null;
  const summary = links ? summarizeGeneDiseases(links) : null;
  return (
    <div className="variant-card-diseases">
      <span className="variant-card-col-key">Disease</span>
      {summary === null ? (
        failed ? (
          <span className="variant-card-empty-note">
            The gene–disease links could not be loaded.{' '}
            <button type="button" className="button-link" onClick={onRetry}>
              Retry
            </button>
          </span>
        ) : (
          <span className="variant-card-empty-note">Loading…</span>
        )
      ) : summary.basis === null ? (
        <span className="variant-card-empty-note">No gene–disease link cached for {symbol}</span>
      ) : (
        <>
          <span className="variant-card-diseases-names">
            {summary.diseases.map((disease, index) => (
              <Fragment key={disease.label}>
                {index ? ' · ' : null}
                {disease.href ? (
                  <a
                    href={disease.href}
                    target="_blank"
                    rel="noreferrer"
                    className="variant-card-disease-link"
                    title={summary.basis === 'omim' ? 'Open in OMIM' : 'Open in Monarch'}
                  >
                    {disease.label}
                  </a>
                ) : (
                  disease.label
                )}
              </Fragment>
            ))}
          </span>
          {summary.more ? (
            <button
              type="button"
              className="button-link"
              title="Every link is under More annotations & detail"
              onClick={onShowAll}
            >
              +{summary.more} more
            </button>
          ) : null}
          <span className="variant-card-disease-source">{BASIS_NOTES[summary.basis]}</span>
        </>
      )}
    </div>
  );
};

// Which way a mode reads, for its colour: dominant, recessive, or neither said.
const INHERITANCE_TONES: Record<GeneInheritanceOut['mode'], 'dominant' | 'recessive' | 'other'> = {
  AD: 'dominant',
  XLD: 'dominant',
  AR: 'recessive',
  XLR: 'recessive',
  SD: 'other',
  XL: 'other',
  MT: 'other',
  YL: 'other',
};

// ClinGen's dosage scores in a word.
const DOSAGE_SCORE_WORDS: Record<number, string> = {
  3: 'sufficient',
  2: 'emerging',
  1: 'little',
  0: 'no evidence',
  30: 'AR phenotype',
  40: 'unlikely',
};

const dosageTone = (score: number | null) =>
  score === 3 ? 'critical' : score === 2 ? 'warning' : score === 40 ? 'success' : 'neutral';

const DosageChip = ({
  curation,
  score,
  text,
}: {
  curation: 'HI' | 'TS';
  score: number | null;
  text: string | null;
}) => {
  if (score === null && !text) return null;
  const word = score !== null ? DOSAGE_SCORE_WORDS[score] : text;
  return (
    <span
      className={`variant-card-chip variant-card-chip--${dosageTone(score)}`}
      title={`${curation === 'HI' ? 'Haploinsufficiency' : 'Triplosensitivity'}: ${text ?? `score ${score}`}`}
    >
      {curation}
      {score !== null ? ` ${score}` : ''}
      {word ? ` · ${word}` : ''}
    </span>
  );
};

const DosageCuration = ({ dosage }: { dosage: GeneDosageSensitivityOut }) => (
  <span className="variant-card-gene-fact">
    {dosage.report_url ? (
      <a
        href={dosage.report_url}
        target="_blank"
        rel="noreferrer"
        className="variant-card-col-key variant-card-gene-fact-link"
        title="Open the curation in ClinGen"
      >
        ClinGen dosage
      </a>
    ) : (
      <span className="variant-card-col-key">ClinGen dosage</span>
    )}
    <DosageChip curation="HI" score={dosage.haploinsufficiency_score} text={dosage.haploinsufficiency} />
    <DosageChip curation="TS" score={dosage.triplosensitivity_score} text={dosage.triplosensitivity} />
  </span>
);

/**
 * The gene's place on the ACMG secondary-findings list: a chip for a listed gene, with the
 * list's phenotype, category, inheritance and variants to report on hover; a note when the list
 * could not be read, so no chip never stands for a list nobody read. A gene the list leaves
 * out, or that no gene reference sync read it for, shows nothing here; the gene page says which.
 */
const AcmgSecondaryFinding = ({ entry }: { entry: GeneAcmgSecondaryFindingOut }) => {
  if (entry.status === 'unread') {
    return (
      <span className="variant-card-gene-fact">
        <span className="variant-card-empty-note">The ACMG SF list could not be read</span>
      </span>
    );
  }
  if (entry.status !== 'listed') return null;
  const listName = entry.version ? `ACMG SF v${entry.version}` : 'ACMG SF';
  const detail = [entry.phenotype, entry.category, entry.inheritance, entry.variants_to_report]
    .filter(Boolean)
    .join(' · ');
  const title = `On ${listName}${detail ? `: ${detail}` : ''}`;
  return (
    <span className="variant-card-gene-fact">
      {entry.source_url ? (
        <a
          href={entry.source_url}
          target="_blank"
          rel="noreferrer"
          title={title}
          className="variant-card-chip variant-card-chip--strong variant-card-chip--link"
        >
          {listName}
        </a>
      ) : (
        <span className="variant-card-chip variant-card-chip--strong" title={title}>
          {listName}
        </span>
      )}
    </span>
  );
};

const acmgSfShown = (entry: GeneAcmgSecondaryFindingOut) => entry.status === 'listed' || entry.status === 'unread';

/**
 * The card's gene in a line under its diseases: the modes of inheritance its curated disease
 * relationships name (GenCC, ClinGen), ClinGen's dosage curation where there is one, and its
 * place on the ACMG secondary-findings list.
 *
 * "None curated" is said only for a gene with a disease link, where a missing mode is worth
 * knowing; a gene without any of these shows nothing. While the lookup loads or after it failed,
 * the disease line above says so.
 */
export const GeneCurationLine = ({ links }: Pick<GeneDiseaseState, 'links'>) => {
  if (!links) return null;
  const hasDisease = summarizeGeneDiseases(links).basis !== null;
  const { inheritance, dosage, acmg_secondary_finding: acmgSf } = links;
  if (!inheritance.length && !hasDisease && !dosage && !acmgSfShown(acmgSf)) return null;
  return (
    <div className="variant-card-gene-curation">
      {inheritance.length || hasDisease ? (
        <span className="variant-card-gene-fact">
          <span className="variant-card-col-key">Inheritance</span>
          {inheritance.length ? (
            inheritance.map((entry) => (
              <span
                key={entry.mode}
                className={`variant-card-moi variant-card-moi--${INHERITANCE_TONES[entry.mode]}`}
                title={`${entry.label}: ${entry.evidence.join(', ')}`}
              >
                {entry.label}
              </span>
            ))
          ) : (
            <span className="variant-card-empty-note">None curated in GenCC or ClinGen</span>
          )}
        </span>
      ) : null}
      {dosage ? <DosageCuration dosage={dosage} /> : null}
      <AcmgSecondaryFinding entry={acmgSf} />
    </div>
  );
};

/**
 * Every gene–disease link of the card's gene, for its expanded detail: Monarch's and OMIM's.
 */
export const VariantGeneDiseases = ({ symbol, links, failed, onRetry }: GeneDiseaseState) => {
  if (!symbol) {
    return <p className="variant-card-empty-note">No gene assigned to this variant.</p>;
  }
  if (!links) {
    if (failed) {
      return (
        <p className="variant-card-empty-note" role="alert">
          The gene–disease associations of {symbol} could not be loaded.{' '}
          <button type="button" className="button-link" onClick={onRetry}>
            Retry
          </button>
        </p>
      );
    }
    return <p className="variant-card-empty-note">Loading gene–disease associations…</p>;
  }
  const { monarch, omim } = links;
  if (!monarch.length && !omim.length) {
    return <p className="variant-card-empty-note">No gene–disease associations cached for {symbol}.</p>;
  }

  return (
    <div className="variant-card-disease-groups">
      {monarch.length ? (
        <div>
          <p className="variant-card-disease-heading">Monarch ({monarch.length})</p>
          <ul className="variant-card-disease-list">
            {monarch.slice(0, DETAIL_LIST_LENGTH).map((entry) => (
              <li key={entry.mondo_id}>
                <a href={entry.monarch_url} target="_blank" rel="noreferrer">
                  {entry.disease_label}
                </a>
                {/* Whether the gene causes the disease or is merely associated changes
                    how the association should be read, so it is stated. */}
                {entry.causal ? <span className="variant-card-chip variant-card-chip--strong">causal</span> : null}
                {entry.sources.length ? (
                  <span className="variant-card-disease-source">{entry.sources.join(', ')}</span>
                ) : null}
              </li>
            ))}
          </ul>
          {monarch.length > DETAIL_LIST_LENGTH ? (
            <p className="variant-card-empty-note">
              +{monarch.length - DETAIL_LIST_LENGTH} more in the Gene Explorer.
            </p>
          ) : null}
        </div>
      ) : null}
      {omim.length ? (
        <div>
          <p className="variant-card-disease-heading">OMIM ({omim.length})</p>
          <ul className="variant-card-disease-list">
            {omim.slice(0, DETAIL_LIST_LENGTH).map((entry) => (
              <li key={`${entry.omim_id ?? ''}|${entry.label}`}>
                {entry.href ? (
                  <a href={entry.href} target="_blank" rel="noreferrer">
                    {entry.label}
                  </a>
                ) : (
                  <span>{entry.label}</span>
                )}
                {entry.omim_id ? <span className="variant-card-disease-source">MIM {entry.omim_id}</span> : null}
              </li>
            ))}
          </ul>
          {omim.length > DETAIL_LIST_LENGTH ? (
            <p className="variant-card-empty-note">+{omim.length - DETAIL_LIST_LENGTH} more in the Gene Explorer.</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
};
