import React from 'react';
import { Link, useLocation } from 'react-router';
import { useQuery } from '@tanstack/react-query';

import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import type { ReportSignoutDetail } from '../../lib/apiSchema.generated';
import { formatSoftwareVersion, useReportBuild } from '../../lib/appVersion';
import { formatCnvPoints } from '../../lib/cnvAcmg/format';
import { isNotFoundError } from '../../lib/errorMessage';
import PageState from '../../components/PageState';
import FamilyPageHeader from './FamilyPageHeader';
import ReportSoftwareIdentity from './ReportSoftwareIdentity';
import { joinWithAnd } from '../../lib/format';
import {
  failuresByJob,
  pendingDatasets,
  unfinishedWriter,
  WRITER_LABELS,
  type FamilyImportUnfinished,
} from '../../components/ImportIncompleteBanner';
import {
  useReportSignoutCheck,
  useReportSignouts,
  useSignedVersionDownload,
} from './reportSignoutQueries';
import {
  ANALYTICAL_SIGN_OFF_STATEMENT,
  describeBreachedMetric,
  describeCatalogue,
  describeCnvKnowledgebase,
  describeFrozenEvidenceValues,
  describeGenePanel,
  describeGeneReferenceReleases,
  describeGenotype,
  describeModuleVersions,
  describeRepeatCall,
  describeRepeatThresholds,
  describeReportSections,
  describeSignedDrift,
  describeSignedStructuralDrift,
  describeSmallVariantDescription,
  describeSmallVariantLocus,
  describeStructuralVariantDescription,
  formatReportTime,
  geneReferenceSourceLabel,
  paraphaseStatusWords,
  parseSignedReport,
  qcStatusLabel,
  repeatStatusWords,
  reportViewSearch,
  type SignedAcknowledgement,
  type SignedGenotype,
  type SignedRecordSection,
  type SignedReport,
  type SignedSmallVariant,
  type SignedStructuralVariant,
  type SignedVariantDescriptions,
} from './signedReportRecord';

// What the live report shows around each reported variant and the signed record does not
// hold. The signed view leaves these out and says so; it never fills them in from live data.
// A record signed before CoGA froze the variant description lacks that too.
const notInRecord = (descriptions: boolean): string[] => [
  descriptions
    ? 'the population frequency and the in silico predictions the live report shows'
    : 'the variant description: gene, HGVS, consequence, genotypes, population frequency and in silico predictions',
  'the segregation in the family, and the family’s members',
  'the gene description, its associated conditions and its gene panels',
  'the phenotype (HPO) terms and their overlap with the gene',
  'the clinical audit trail and the analysis pipeline settings',
];
const notInRecordPerVariant = (descriptions: boolean): string =>
  descriptions
    ? 'Not in the signed record: the segregation, gene and phenotype.'
    : 'Not in the signed record: the variant description, segregation, gene and phenotype.';

/**
 * A section of what the analysis looked at, in each of its states: not in a record signed
 * before CoGA recorded it, not captured when it could not be read, else as `children` renders
 * it. Every section of signed_record_sections.py is shown through this, so none drops out of
 * the signed view without a trace.
 */
function RecordSectionBlock<T>({
  title,
  what,
  section,
  version,
  children,
}: {
  title: string;
  /** What the section holds, in the sentence that says the record lacks it. */
  what: string;
  section: SignedRecordSection<T>;
  version: number;
  children: (value: T) => React.ReactNode;
}) {
  return (
    <section className="surface-card report-signed-checks">
      <h2 className="report-audit-heading">{title}</h2>
      {section.state === 'absent' ? (
        <p className="report-paragraph">
          Not in the signed record: version {version} was signed before CoGA recorded {what}.
        </p>
      ) : section.state === 'unavailable' ? (
        <p className="report-paragraph" role="note">
          Not captured: {what} could not be read when version {version} was signed ({section.reason}).
        </p>
      ) : (
        children(section.value)
      )}
    </section>
  );
}

const GenotypeList: React.FC<{ genotypes: SignedGenotype[] }> = ({ genotypes }) =>
  genotypes.length ? (
    <ul className="report-signed-list">
      {genotypes.map((genotype, index) => (
        <li key={`${genotype.sample}-${index}`}>{describeGenotype(genotype)}</li>
      ))}
    </ul>
  ) : (
    <p className="report-paragraph">No genotypes are recorded for it.</p>
  );

/** A reported variant's description as reviewed, from the record; or why the record has none. */
const VariantDescription: React.FC<{
  variantId: string;
  kind: 'small' | 'structural';
  descriptions: SignedRecordSection<SignedVariantDescriptions>;
  version: number;
}> = ({ variantId, kind, descriptions, version }) => {
  if (descriptions.state === 'absent') return null;
  // One entry per callset or caller that calls the variant: what it is, where, and the
  // genotypes the analyst saw.
  const entries =
    descriptions.state !== 'recorded'
      ? []
      : kind === 'small'
        ? descriptions.value.small
            .filter((entry) => entry.variantId === variantId)
            .map((entry) => ({
              source: entry.source,
              headline: describeSmallVariantDescription(entry),
              locus: describeSmallVariantLocus(entry),
              genotypes: entry.genotypes,
            }))
        : descriptions.value.structural
            .filter((entry) => entry.variantId === variantId)
            .map((entry) => ({
              source: entry.source,
              headline: describeStructuralVariantDescription(entry),
              locus: null,
              genotypes: entry.genotypes,
            }));
  return (
    <div className="report-section">
      <h3 className="report-subheading">Variant description as reviewed</h3>
      {descriptions.state === 'unavailable' ? (
        <p className="report-paragraph" role="note">
          The variant descriptions could not be read when version {version} was signed (
          {descriptions.reason}).
        </p>
      ) : !entries.length ? (
        <p className="report-paragraph">
          The record holds no description of this variant: it was not in the family’s data when
          version {version} was signed.
        </p>
      ) : (
        entries.map((entry, index) => (
          <div key={`${entry.source ?? 'entry'}-${index}`} className="report-signed-description">
            <p className="report-paragraph">
              <strong>{entry.headline}</strong>
            </p>
            {entry.locus ? <p className="report-variant-locus">{entry.locus}</p> : null}
            <GenotypeList genotypes={entry.genotypes} />
          </div>
        ))
      )}
    </div>
  );
};

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`;

const acknowledgementLine = (acknowledgement: SignedAcknowledgement) =>
  acknowledgement.acknowledged
    ? `Signed out over it, with the reason: ${acknowledgement.reason || 'no reason recorded'}.`
    : null;

/**
 * An import that had begun writing the family and not finished when the version was signed;
 * a single-file upload or a deletion that had not finished, named by its frozen key.
 */
const describeSignedUnfinished = (entry: FamilyImportUnfinished): string => {
  const pending = pendingDatasets(entry);
  const writer = unfinishedWriter(entry.key);
  if (writer !== 'import') {
    return `${WRITER_LABELS[writer]}${entry.at ? ` (begun ${formatReportTime(entry.at)})` : ''} had not finished${
      pending.length ? `: ${joinWithAnd(pending)} may have been partly written or missing` : ''
    }.`;
  }
  return `An import${entry.at ? ` (begun ${formatReportTime(entry.at)})` : ''}${
    entry.jobId ? `, import job ${entry.jobId},` : ''
  } had begun writing the family’s data and had not finished${
    pending.length
      ? `: ${joinWithAnd(pending)} may have been partly written or missing${
          entry.finishedDatasets.length ? ` (${joinWithAnd(entry.finishedDatasets)} had finished)` : ''
        }`
      : ''
  }.`;
};

type CardContext = {
  descriptions: SignedRecordSection<SignedVariantDescriptions>;
  version: number;
};

const SmallVariantCard: React.FC<{ variant: SignedSmallVariant } & CardContext> = ({
  variant,
  descriptions,
  version,
}) => (
  <article className="surface-card report-variant">
    <div className="report-variant-head">
      <h2 className="section-title">{variant.variantId ?? 'An entry that cannot be read'}</h2>
      {variant.classification ? (
        <span className="table-chip report-classification-chip">
          {variant.classification}
          {variant.pointTotal !== null ? ` · ${variant.pointTotal} pts` : ''}
        </span>
      ) : (
        <span className="table-chip report-classification-chip report-classification-chip--none">
          Not classified
        </span>
      )}
    </div>
    {variant.variantId === null ? (
      <p className="report-paragraph" role="alert">
        This entry of the signed record cannot be read here. The downloaded record holds it as
        stored.
      </p>
    ) : (
      <>
        <VariantDescription
          variantId={variant.variantId}
          kind="small"
          descriptions={descriptions}
          version={version}
        />
        <div className="report-section">
          <h3 className="report-subheading">Classification motivation</h3>
          {variant.criteria.length ? (
            <>
              <p className="report-paragraph">
                This variant was classified as{' '}
                <strong>{variant.classification || 'uncertain significance'}</strong> based on{' '}
                {joinWithAnd(variant.criteria.map((criterion) => criterion.code))}.
              </p>
              <ul className="report-criteria-list">
                {variant.criteria.map((criterion) => (
                  <li
                    key={criterion.code}
                    className={`report-criterion report-criterion--${criterion.direction}`}
                  >
                    <span className="report-criterion-code">{criterion.code}</span>
                    <span className="report-criterion-strength">{criterion.strengthLabel}</span>
                    <span className="report-criterion-text">
                      <strong>{criterion.name}.</strong> {criterion.description}
                      {criterion.evidence ? ` Evidence: ${criterion.evidence}` : ''}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="report-paragraph">
              The signed record holds no ACMG/AMP criteria for this variant.
            </p>
          )}
          {variant.unreadableCriteria ? (
            <p className="report-paragraph" role="note">
              {plural(variant.unreadableCriteria, 'criterion')} of this variant’s record cannot be
              read here. The downloaded record holds them as stored.
            </p>
          ) : null}
        </div>
        <div className="report-section">
          <h3 className="report-subheading">Evidence when classified</h3>
          {variant.evidence ? (
            <p className="report-paragraph">
              When the variant was classified
              {variant.evidence.capturedAt ? ` (${formatReportTime(variant.evidence.capturedAt)})` : ''},
              ClinVar reported{' '}
              {variant.evidence.clinvar
                ? variant.evidence.clinvar.replace(/_/g, ' ')
                : 'no significance for it'}
              {variant.evidence.annotationVersion
                ? `, with annotation version ${variant.evidence.annotationVersion}`
                : ''}
              .
            </p>
          ) : (
            <p className="report-paragraph">
              No evidence was frozen for this classification: it was not saved through ACMG
              classify.
            </p>
          )}
          {variant.evidence && describeFrozenEvidenceValues(variant.evidence) ? (
            <p className="report-paragraph">{describeFrozenEvidenceValues(variant.evidence)}</p>
          ) : null}
        </div>
        {variant.note ? (
          <div className="report-section">
            <h3 className="report-subheading">Analyst note</h3>
            <p className="report-paragraph report-note">{variant.note}</p>
          </div>
        ) : null}
        <p className="report-variant-locus">Tags: {variant.tags.join(', ') || 'none'}</p>
        <p className="report-signed-absent-line">
          {notInRecordPerVariant(descriptions.state !== 'absent')}
        </p>
      </>
    )}
  </article>
);

const StructuralVariantCard: React.FC<{ variant: SignedStructuralVariant } & CardContext> = ({
  variant,
  descriptions,
  version,
}) => {
  const title = variant.variantId ?? variant.variantKey;
  return (
    <article className="surface-card report-variant">
      <div className="report-variant-head">
        <h2 className="section-title">
          {title ? `Structural variant ${title}` : 'An entry that cannot be read'}
        </h2>
        {variant.classification ? (
          <span className="table-chip report-classification-chip">
            {variant.classification}
            {variant.pointTotal !== null ? ` · ${formatCnvPoints(variant.pointTotal)} pts` : ''}
          </span>
        ) : (
          <span className="table-chip report-classification-chip report-classification-chip--none">
            Not classified
          </span>
        )}
      </div>
      {title === null ? (
        <p className="report-paragraph" role="alert">
          This entry of the signed record cannot be read here. The downloaded record holds it as
          stored.
        </p>
      ) : (
        <>
          {variant.variantId ? (
            <VariantDescription
              variantId={variant.variantId}
              kind="structural"
              descriptions={descriptions}
              version={version}
            />
          ) : null}
          <div className="report-section">
            <h3 className="report-subheading">Classification motivation</h3>
            {variant.criteria.length ? (
              <>
                <p className="report-paragraph">
                  This variant was classified as{' '}
                  <strong>{variant.classification || 'uncertain significance'}</strong> by the
                  ClinGen CNV criteria {joinWithAnd(variant.criteria.map((criterion) => criterion.code))}.
                </p>
                <ul className="report-criteria-list">
                  {variant.criteria.map((criterion) => (
                    <li key={criterion.code} className="report-criterion">
                      <span className="report-criterion-code">{criterion.code}</span>
                      <span className="report-criterion-strength">
                        {criterion.points !== null ? `${formatCnvPoints(criterion.points)} pts` : 'points not recorded'}
                      </span>
                      <span className="report-criterion-text">
                        <strong>{criterion.name}.</strong>
                        {criterion.evidence ? ` Evidence: ${criterion.evidence}` : ''}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <p className="report-paragraph">
                The signed record holds no ClinGen CNV criteria for this variant.
              </p>
            )}
            {variant.unreadableCriteria ? (
              <p className="report-paragraph" role="note">
                {plural(variant.unreadableCriteria, 'criterion')} of this variant’s record cannot be
                read here. The downloaded record holds them as stored.
              </p>
            ) : null}
          </div>
          {variant.note ? (
            <div className="report-section">
              <h3 className="report-subheading">Analyst note</h3>
              <p className="report-paragraph report-note">{variant.note}</p>
            </div>
          ) : null}
          <p className="report-variant-locus">Tags: {variant.tags.join(', ') || 'none'}</p>
          <p className="report-signed-absent-line">
            {notInRecordPerVariant(descriptions.state !== 'absent')}
          </p>
        </>
      )}
    </article>
  );
};

/** The reference-data releases in one line, beside the modules in the footer. */
const describeReferenceDataLine = (record: SignedReport | null): string => {
  const reference = record?.referenceData;
  if (!reference || reference.state === 'absent') return 'not in the signed record';
  if (reference.state === 'unavailable') return `not captured (${reference.reason})`;
  const { geneReference, clinicalCnvs } = reference.value;
  const genes =
    geneReference.state === 'recorded'
      ? `gene reference ${describeGeneReferenceReleases(geneReference.value) ?? 'with no release recorded'}`
      : geneReference.state === 'unavailable'
        ? 'gene reference not captured'
        : 'gene reference not in the record';
  const cnvs =
    clinicalCnvs.state === 'recorded'
      ? `clinical CNV knowledgebase ${describeCnvKnowledgebase(clinicalCnvs.value)}`
      : clinicalCnvs.state === 'unavailable'
        ? 'clinical CNV knowledgebase not captured'
        : 'clinical CNV knowledgebase not in the record';
  const panelApp =
    record.genePanels.state === 'recorded'
      ? record.genePanels.value
          .filter((panel) => panel.source === 'panelapp')
          .map((panel) => `${panel.name ?? panel.externalId} ${panel.externalVersion ?? 'version not stated'}`)
      : [];
  return [genes, cnvs, panelApp.length ? `PanelApp ${panelApp.join(', ')}` : null]
    .filter((part): part is string => Boolean(part))
    .join('; ');
};

/**
 * What the analysis looked at beyond the reported variants, as the record froze it: the gene
 * panels its searches applied, the repeat expansions and Paraphase results of the family's
 * samples, and the reference-data releases. A section the record predates says so.
 */
const AnalysisSections: React.FC<{ record: SignedReport; version: number }> = ({ record, version }) => (
  <>
    <RecordSectionBlock
      title="Gene panels applied"
      what="the gene panels the analysis applied"
      section={record.genePanels}
      version={version}
    >
      {(panels) =>
        panels.length ? (
          <>
            <p className="report-paragraph">
              The family’s searches applied {panels.length === 1 ? 'this gene-panel version' : 'these gene-panel versions'}:
            </p>
            <ul className="report-signed-list">
              {panels.map((panel, index) => (
                <li key={`${panel.panelId ?? 'panel'}-${panel.version ?? index}-${panel.externalVersion ?? ''}`}>
                  {describeGenePanel(panel)}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="report-paragraph">No search of the family applied a gene panel.</p>
        )
      }
    </RecordSectionBlock>

    <RecordSectionBlock
      title="Repeat expansions (TRGT)"
      what="the repeat-expansion calls"
      section={record.repeatExpansions}
      version={version}
    >
      {(repeats) => (
        <>
          <p className="report-paragraph">
            Each call’s status as CoGA classified it when this version was signed, against the
            repeat catalogue of {describeCatalogue(repeats.catalogue, 'loci')}.
          </p>
          {repeats.loci.length ? (
            <ul className="report-signed-list">
              {repeats.loci.map((locus, index) => (
                <li key={`${locus.locusId ?? 'locus'}-${index}`}>
                  <strong>
                    {locus.gene ?? locus.locusId ?? 'A locus'}
                    {locus.locusId && locus.locusId !== locus.gene ? ` (${locus.locusId})` : ''}
                  </strong>
                  {locus.disease ? `, ${locus.disease}` : ''}: {repeatStatusWords(locus.status)} ({describeRepeatThresholds(locus)}
                  {locus.motif ? `; motif ${locus.motif}` : ''}).{' '}
                  {locus.calls.map(describeRepeatCall).join('; ')}
                </li>
              ))}
            </ul>
          ) : (
            <p className="report-paragraph">The family’s samples had no repeat-expansion calls.</p>
          )}
        </>
      )}
    </RecordSectionBlock>

    <RecordSectionBlock
      title="Paraphase"
      what="the Paraphase results"
      section={record.paraphase}
      version={version}
    >
      {(paraphase) => (
        <>
          <p className="report-paragraph">
            Each result’s status as CoGA read it when this version was signed, by the catalogue of{' '}
            {describeCatalogue(paraphase.catalogue, 'regions')}.
          </p>
          {paraphase.genes.length ? (
            <ul className="report-signed-list">
              {paraphase.genes.map((gene, index) => (
                <li key={`${gene.gene ?? 'gene'}-${index}`}>
                  <strong>{gene.gene ?? 'A gene'}</strong>
                  {gene.region ? ` (${gene.region})` : ''}
                  {gene.statusRule ? ` — ${gene.statusRule}` : ' — no automatic call'}.{' '}
                  {gene.results
                    .map(
                      (result) =>
                        `${result.sample}: ${paraphaseStatusWords(result.status)}${
                          result.copyNumber.length
                            ? ` (${result.copyNumber
                                .map((metric) => `${metric.label} ${metric.value ?? 'no call'}`)
                                .join(', ')})`
                            : ''
                        }${result.copyNumberSignal ? ', copy-number change' : ''}`,
                    )
                    .join('; ') || 'No sample has a result.'}
                </li>
              ))}
            </ul>
          ) : (
            <p className="report-paragraph">The family’s samples had no Paraphase results.</p>
          )}
        </>
      )}
    </RecordSectionBlock>

    <RecordSectionBlock
      title="Reference-data releases"
      what="the reference-data releases"
      section={record.referenceData}
      version={version}
    >
      {(reference) => (
        <ul className="report-signed-list">
          <li>
            Gene reference, for the reported variants’ genes:{' '}
            {reference.geneReference.state === 'recorded' ? (
              <>
                {describeGeneReferenceReleases(reference.geneReference.value) ?? 'no release recorded'}
                {reference.geneReference.value.sources.length ? (
                  <ul className="report-signed-list">
                    {reference.geneReference.value.sources.map((source, index) => (
                      <li key={`${source.source}-${index}`}>
                        {geneReferenceSourceLabel(source.source)} {source.release ?? '(no release)'}
                        {source.status ? ` — ${source.status}` : ''}: {source.genes.join(', ')}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {reference.geneReference.value.notInReference.length
                  ? ` Not in the gene reference: ${reference.geneReference.value.notInReference.join(', ')}.`
                  : ''}
              </>
            ) : reference.geneReference.state === 'unavailable' ? (
              `not captured (${reference.geneReference.reason})`
            ) : (
              'not in the signed record'
            )}
          </li>
          <li>
            Clinical CNV knowledgebase:{' '}
            {reference.clinicalCnvs.state === 'recorded'
              ? describeCnvKnowledgebase(reference.clinicalCnvs.value)
              : reference.clinicalCnvs.state === 'unavailable'
                ? `not captured (${reference.clinicalCnvs.reason})`
                : 'not in the signed record'}
          </li>
          <li>
            PanelApp: each PanelApp panel’s version is listed with the panel, under Gene panels
            applied.
          </li>
        </ul>
      )}
    </RecordSectionBlock>
  </>
);

/** The checks frozen at sign-out: Sample QC, sequencing QC and the import's completeness. */
const SignedChecks: React.FC<{ record: SignedReport }> = ({ record }) => {
  const { sampleQc, sequencingQc, importIncomplete, importUnfinished } = record;
  const failedImport = importIncomplete === 'absent' ? null : importIncomplete;
  const unfinished = importUnfinished === 'absent' ? [] : importUnfinished;
  return (
    <section className="surface-card report-signed-checks">
      <h2 className="report-audit-heading">Checks at sign-out</h2>
      <div className="report-section">
        <h3 className="report-subheading">Sample QC</h3>
        {sampleQc ? (
          <>
            <p className="report-paragraph">
              Overall: <strong>{qcStatusLabel(sampleQc.overallStatus)}</strong>
              {sampleQc.applicationLabel ? ` (${sampleQc.applicationLabel})` : ''}.
            </p>
            {sampleQc.checks.length ? (
              <ul className="report-signed-list">
                {sampleQc.checks.map((check, index) => (
                  <li key={`${check.label}-${index}`}>
                    {check.label}: {qcStatusLabel(check.status)}
                    {check.message ? ` — ${check.message}` : ''}
                  </li>
                ))}
              </ul>
            ) : null}
            {sampleQc.notes.map((note, index) => (
              <p key={index} className="report-paragraph">
                {note}
              </p>
            ))}
            {acknowledgementLine(record.qcAcknowledgement) ? (
              <p className="report-paragraph">{acknowledgementLine(record.qcAcknowledgement)}</p>
            ) : null}
          </>
        ) : (
          <p className="report-paragraph">Not in the signed record.</p>
        )}
      </div>
      <div className="report-section">
        <h3 className="report-subheading">Sequencing QC</h3>
        {!sequencingQc ? (
          <p className="report-paragraph">Not in the signed record.</p>
        ) : sequencingQc.unavailable ? (
          <p className="report-paragraph">
            The QC cut-offs could not be resolved when this version was signed (
            {sequencingQc.unavailable}), so the record holds no sequencing-QC verdicts.
          </p>
        ) : (
          <>
            <p className="report-paragraph">
              Judged against the cut-offs of {sequencingQc.profileLabel ?? 'an unnamed profile'}.
            </p>
            {sequencingQc.samples.length ? (
              <ul className="report-signed-list">
                {sequencingQc.samples.map((sample) => (
                  <li key={sample.sampleId}>
                    {sample.sampleId}: {qcStatusLabel(sample.verdict)}
                    {sample.breached.length
                      ? ` — ${sample.breached.map(describeBreachedMetric).join('; ')}`
                      : ''}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="report-paragraph">
                No sequencing-QC values were recorded for the family’s samples.
              </p>
            )}
          </>
        )}
      </div>
      <div className="report-section">
        <h3 className="report-subheading">Data import</h3>
        {importIncomplete === 'absent' && importUnfinished === 'absent' ? (
          <p className="report-paragraph">Not in the signed record.</p>
        ) : !failedImport && !unfinished.length ? (
          <p className="report-paragraph">The family’s data had imported completely.</p>
        ) : (
          <p className="report-paragraph">
            {failedImport ? (
              <>
                The family’s data was incomplete:{' '}
                {failedImport.failedDatasets.length
                  ? `${joinWithAnd(failedImport.failedDatasets)} failed to import`
                  : 'which datasets failed was not recorded'}
                {failedImport.importedDatasets.length
                  ? `; ${joinWithAnd(failedImport.importedDatasets)} did import`
                  : ''}
                {failedImport.at ? ` (import of ${formatReportTime(failedImport.at)})` : ''}.
                {failuresByJob(failedImport)
                  ? ` Import jobs: ${failuresByJob(failedImport)!
                      .map(([job, names]) => `${joinWithAnd(names)} in ${job}`)
                      .join('; ')}.`
                  : failedImport.jobId
                    ? ` Import job ${failedImport.jobId}.`
                    : ''}{' '}
              </>
            ) : null}
            {unfinished.map((entry) => (
              <React.Fragment key={entry.key}>{describeSignedUnfinished(entry)} </React.Fragment>
            ))}
            {acknowledgementLine(record.importAcknowledgement) ?? 'No acknowledgement is recorded.'}
          </p>
        )}
      </div>
    </section>
  );
};

/**
 * A signed version of the family report, rendered from its frozen record alone (REQ-TRACE-007,
 * TF-06 H9): what was signed, with its version, date, signer and content hash. Where the
 * record lacks something the live report shows, the page says so; nothing is read from the
 * family's current data, and Print prints this view.
 */
const SignedFamilyReport: React.FC<{ familyId: string; version: number; projectId?: string }> = ({
  familyId,
  version,
  projectId,
}) => {
  const location = useLocation();
  const signouts = useReportSignouts(familyId);
  const detailQuery = useQuery<ReportSignoutDetail>({
    queryKey: ['family', familyId, 'report-signout', version],
    queryFn: async () =>
      (await api.get(apiPath`/families/${familyId}/report/sign-outs/${version}`))
        .data as ReportSignoutDetail,
  });
  const latest = signouts.data?.latest ?? null;
  const isLatest = latest ? latest.version === version : null;
  // Only the latest version can be compared with the family's current data.
  const check = useReportSignoutCheck(familyId, isLatest ? version : null);
  const reportBuild = useReportBuild();
  const { download, error: downloadError } = useSignedVersionDownload(familyId);
  const liveLink = { search: reportViewSearch(location.search, 'live') };
  const versionLink = (target: number) => ({
    search: reportViewSearch(location.search, { version: target }),
  });

  if (detailQuery.isLoading) {
    return (
      <PageState
        kicker="Signed report"
        title={`Loading signed version ${version}`}
        message="Reading the record frozen at sign-out."
        loading
      />
    );
  }

  const detail = detailQuery.data;
  const readable =
    !detailQuery.isError &&
    typeof detail === 'object' &&
    detail !== null &&
    detail.version === version &&
    typeof detail.signed_out_at === 'string';
  if (!readable) {
    const missing = detailQuery.isError && isNotFoundError(detailQuery.error);
    return (
      <PageState
        kicker="Signed report"
        title={
          missing
            ? `There is no signed version ${version}`
            : `Signed version ${version} could not be loaded`
        }
        message={
          missing
            ? 'This family has no signed version with that number.'
            : 'The signed record could not be retrieved or read, so it is not shown. Nothing is shown in its place.'
        }
        action={
          <div className="inline-actions">
            {missing ? null : (
              <button
                type="button"
                className="button-secondary"
                onClick={() => void detailQuery.refetch()}
              >
                Retry
              </button>
            )}
            {latest && latest.version !== version ? (
              <Link to={versionLink(latest.version)} className="button-secondary">
                View signed version {latest.version}
              </Link>
            ) : null}
            <Link to={liveLink} className="button-secondary">
              Open the live report
            </Link>
          </div>
        }
      />
    );
  }

  const record = parseSignedReport(detail.snapshot);
  const reportCode = detail.report_code ?? null;
  // Straight after the sign-out: the page that signed it says so, with the code (once).
  const justSigned = (location.state as { signedOut?: { version?: unknown } } | null)?.signedOut;
  const confirmation = justSigned?.version === version;
  const tampered = detail.verified === false;
  const superseded = latest && latest.version > version ? latest : null;
  const signedWith = detail.software_version
    ? formatSoftwareVersion(detail.software_version, detail.git_sha)
    : null;
  const gaps = detail.not_captured ?? [];
  const versions = signouts.data?.signouts ?? [];
  const checkState = !isLatest
    ? null
    : check.isError
      ? 'unverified'
      : !check.data
        ? 'checking'
        : check.data.version !== version || typeof check.data.matches !== 'boolean'
          ? 'unverified'
          : check.data.matches
            ? 'matches'
            : 'changed';
  // Heads the printout whenever it is not simply the latest, intact signed record.
  const printNotice =
    [
      tampered
        ? `Do not use — the stored record of signed version ${version} does not match its content hash.`
        : null,
      superseded
        ? `Superseded — signed version ${superseded.version} replaces signed version ${version}.`
        : null,
      signouts.isError
        ? 'Not confirmed as the latest signed version — the signed versions could not be listed.'
        : null,
      record ? null : `The record of signed version ${version} could not be read.`,
      reportBuild.failed
        ? 'Incomplete — the software version that rendered this printout could not be loaded.'
        : null,
    ]
      .filter(Boolean)
      .join(' ') || null;
  const smallVariants = record?.reportedVariants ?? null;
  const structuralVariants = record?.reportedStructuralVariants ?? null;
  // The drift at sign-out of the small variants' and the SV/CNV classifications, counted as one.
  const driftedAtSignout =
    (record?.drift?.driftedCount ?? 0) + (record?.structuralDrift?.driftedCount ?? 0);
  const checkedCounts = [record?.drift?.checked, record?.structuralDrift?.checked].filter(
    (value): value is number => typeof value === 'number',
  );
  const checkedAtSignout = checkedCounts.length ? checkedCounts.reduce((sum, value) => sum + value, 0) : null;

  return (
    <div className="page-shell report-page report-page--signed space-y-6">
      {printNotice ? <p className="report-print-notice print-only">{printNotice}</p> : null}
      <FamilyPageHeader
        kicker={`Clinical report — signed version ${version}`}
        familyId={familyId}
        projectId={projectId}
        className="report-header"
        actions={
          <div className="report-header-actions no-print">
            <button type="button" className="form-button" onClick={() => window.print()}>
              Print signed version {version}
            </button>
            <Link to={liveLink} className="button-secondary">
              Open the live report
            </Link>
          </div>
        }
      >
        <p className="report-header-meta">
          Reference assembly as signed: {record?.assembly ?? 'not in the signed record'}
        </p>
        {versions.length > 1 ? (
          <nav className="report-version-nav no-print" aria-label="Signed versions">
            <span className="report-footer-label">Signed versions</span>{' '}
            {versions.map((entry, index) => (
              <React.Fragment key={entry.version}>
                {index ? ' · ' : null}
                {entry.version === version ? (
                  <strong aria-current="page">version {entry.version}</strong>
                ) : (
                  <Link to={versionLink(entry.version)}>version {entry.version}</Link>
                )}
                {entry.version === latest?.version ? ' (latest)' : ''}
              </React.Fragment>
            ))}
          </nav>
        ) : null}
      </FamilyPageHeader>

      <section
        className={`surface-card report-signout report-signed-record${
          tampered ? ' report-signed-record--tampered' : superseded ? ' report-signed-record--superseded' : ''
        }`}
        aria-label={`Signed version ${version}`}
      >
        <p className="report-signout-line">
          Signed version {version} — signed out by <strong>{detail.signed_out_by}</strong> on{' '}
          {formatReportTime(detail.signed_out_at)}
        </p>
        {confirmation ? (
          <p className="report-signout-confirmation no-print" role="status">
            <strong>Signed out as version {version}.</strong>{' '}
            {reportCode
              ? `Report code ${reportCode}: the EPD report refers to this record by it.`
              : 'This record has no report code.'}
          </p>
        ) : null}
        <p className="report-signout-code">
          <span className="report-footer-label">Report code</span>{' '}
          <strong>{reportCode ?? 'none: the record’s fields cannot make one'}</strong>
        </p>
        <p className="report-signout-status">
          {record?.statement ?? ANALYTICAL_SIGN_OFF_STATEMENT}
        </p>
        <p className="report-signout-status">
          This is the record frozen when version {version} was signed. Nothing on this page is read
          from the family’s current data.
        </p>
        {tampered ? (
          <p className="report-signed-warning" role="alert">
            <strong>⚠ The stored record does not match its content hash.</strong> It may have been
            changed after it was signed. Do not use it as the signed report, and report it.
          </p>
        ) : detail.verified === true ? (
          <p className="report-signout-status">The stored record matches its content hash.</p>
        ) : (
          <p className="report-signout-status">
            The stored record was not checked against its content hash.
          </p>
        )}
        <p className="report-signout-hash">
          <span className="report-footer-label">Content hash</span> {detail.content_hash}
        </p>
        <p className="report-signout-software">
          <span className="report-footer-label">Signed with</span>{' '}
          {signedWith ?? 'not in the signed record'}
        </p>
        {detail.qc_status ? (
          <p className="report-signout-qc">
            <span className="report-footer-label">Sample QC</span> {qcStatusLabel(detail.qc_status)}
            {detail.qc_acknowledged ? (
              <> — override acknowledged: {detail.qc_acknowledgement_reason}</>
            ) : null}
          </p>
        ) : null}
        {detail.drift_acknowledged ? (
          <p className="report-signout-qc">
            <span className="report-footer-label">Evidence drift</span> override acknowledged:{' '}
            {detail.drift_acknowledgement_reason || 'no reason recorded'}
          </p>
        ) : null}
        {detail.import_incomplete_acknowledged ? (
          <p className="report-signout-qc">
            <span className="report-footer-label">Incomplete import</span>{' '}
            {detail.import_incomplete_failed_datasets?.length
              ? `${joinWithAnd(detail.import_incomplete_failed_datasets)} not imported `
              : ''}
            {detail.import_incomplete_job_id ? `(import job ${detail.import_incomplete_job_id}) ` : ''}
            {detail.import_incomplete_failed_datasets?.length || detail.import_incomplete_job_id
              ? '— '
              : ''}
            override acknowledged:{' '}
            {detail.import_incomplete_acknowledgement_reason || 'no reason recorded'}
          </p>
        ) : null}
        {superseded ? (
          <p className="report-signed-warning" role="note">
            <strong>Superseded.</strong> Signed version {superseded.version}, signed out by{' '}
            {superseded.signed_out_by} on {formatReportTime(superseded.signed_out_at)}, is the latest.{' '}
            <Link to={versionLink(superseded.version)} className="no-print">
              View signed version {superseded.version}
            </Link>
          </p>
        ) : isLatest ? (
          <p className="report-signout-status">This is the latest signed version.</p>
        ) : signouts.isError ? (
          <p className="report-signed-warning" role="note">
            Whether a later signed version exists could not be checked: the signed versions could
            not be listed.
          </p>
        ) : null}
        {gaps.length > 0 ? (
          <p className="report-signout-gaps" role="note">
            <strong>Not captured in signed version {version}:</strong>{' '}
            {gaps.map((gap) => `${gap.item} (${gap.reason})`).join('; ')}.
          </p>
        ) : null}
        {checkState ? (
          // About the present, not the record, so it stays off the printout.
          <p
            className={`report-signed-current report-signed-current--${checkState} no-print`}
            role={checkState === 'changed' ? 'alert' : undefined}
          >
            {checkState === 'checking' ? 'Checking the family’s current data against this version…' : null}
            {checkState === 'matches'
              ? 'The family’s current data still matches this signed version.'
              : null}
            {checkState === 'changed' ? (
              <>
                <strong>⚠ Changed since this version was signed:</strong>{' '}
                {describeReportSections(check.data?.changed_sections ?? [])}. This page still shows
                version {version} as it was signed. Open the live report to review the changes, and
                sign out again to issue a new version.
              </>
            ) : null}
            {checkState === 'unverified'
              ? 'Whether the family’s current data still matches this version could not be checked.'
              : null}
          </p>
        ) : null}
        <p className="report-signout-actions no-print">
          <button type="button" className="button-secondary" onClick={() => void download(version)}>
            Download signed version {version} (JSON)
          </button>
          {downloadError ? (
            <span className="report-signout-download-error" role="alert">
              {' '}
              {downloadError.message}
            </span>
          ) : null}
        </p>
      </section>

      {!record ? (
        <section className="surface-card report-incomplete" role="alert">
          <p className="report-paragraph">
            <strong>The signed record of version {version} cannot be read here.</strong> Download it
            to see the record as it is stored.
          </p>
        </section>
      ) : (
        <>
          <section className="surface-card report-intro">
            <p className="report-paragraph">
              Signed version {version} records{' '}
              {smallVariants ? plural(smallVariants.length, 'reported small variant') : 'no list of reported small variants'}{' '}
              and{' '}
              {structuralVariants
                ? plural(structuralVariants.length, 'reported structural variant')
                : 'no list of reported structural variants'}{' '}
              in family <strong>{record.familyId ?? familyId}</strong>.
            </p>
            <p className="report-disclaimer">
              ACMG/AMP classifications are decision support and must be confirmed by a qualified
              clinical scientist before clinical use.
            </p>
          </section>

          <section className="surface-card report-signed-absent" role="note">
            <h2 className="report-audit-heading">Not in the signed record</h2>
            <p className="report-paragraph">
              The record holds each reported variant’s classification, criteria, tags and note
              {record.variantDescriptions.state !== 'absent'
                ? ', its description as the analyst reviewed it'
                : ''}
              , and the evidence frozen when it was classified. It does not hold the rest of what
              the live report shows, so this page leaves it out:
            </p>
            <ul className="report-signed-list">
              {notInRecord(record.variantDescriptions.state !== 'absent').map((item, index, items) => (
                <li key={item}>
                  {item}
                  {index === items.length - 1 ? '.' : ';'}
                </li>
              ))}
            </ul>
            <p className="report-paragraph no-print">
              The <Link to={liveLink}>live report</Link> shows them from current data. It is not the
              signed version.
            </p>
          </section>

          <section className="surface-card report-signed-drift">
            <h2 className="report-audit-heading">Evidence drift at sign-out</h2>
            {!record.drift && !record.structuralDrift ? (
              <p className="report-paragraph">Not in the signed record.</p>
            ) : driftedAtSignout === 0 ? (
              <p className="report-paragraph">
                No reported classification had changed evidence when this version was signed
                {checkedAtSignout !== null ? ` (${checkedAtSignout} checked)` : ''}.
              </p>
            ) : (
              <>
                <p className="report-paragraph">
                  {plural(driftedAtSignout, 'classification')} had evidence that changed, or could not
                  be verified, when this version was signed:
                </p>
                <ul className="report-drift-list">
                  {(record.drift?.drifted ?? []).map((item, index) => (
                    <li key={`${item.variantId}-${index}`}>
                      <strong>{item.variantId}</strong> — {describeSignedDrift(item)}
                      {item.classifiedBy ? (
                        <span className="report-drift-meta"> (classified by {item.classifiedBy})</span>
                      ) : null}
                    </li>
                  ))}
                  {(record.structuralDrift?.drifted ?? []).map((item, index) => (
                    <li key={`sv:${item.variantId}-${index}`}>
                      <strong>{item.variantId}</strong> (structural variant) —{' '}
                      {describeSignedStructuralDrift(item)}
                      {item.classifiedBy ? (
                        <span className="report-drift-meta"> (classified by {item.classifiedBy})</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
                <p className="report-paragraph">
                  {acknowledgementLine(record.driftAcknowledgement) ?? 'No acknowledgement is recorded.'}
                </p>
              </>
            )}
            {record.drift && !record.structuralDrift ? (
              <p className="report-paragraph">
                The signed record holds no drift check of the structural-variant and CNV
                classifications.
              </p>
            ) : null}
          </section>

          {smallVariants === null ? (
            <section className="surface-card report-empty">
              <p className="report-paragraph">
                The signed record holds no list of reported small variants.
              </p>
            </section>
          ) : (
            smallVariants.map((variant, index) => (
              <SmallVariantCard
                key={`${variant.variantId ?? 'entry'}-${index}`}
                variant={variant}
                descriptions={record.variantDescriptions}
                version={version}
              />
            ))
          )}
          {(structuralVariants ?? []).map((variant, index) => (
            <StructuralVariantCard
              key={`sv-${variant.variantId ?? variant.variantKey ?? 'entry'}-${index}`}
              variant={variant}
              descriptions={record.variantDescriptions}
              version={version}
            />
          ))}
          {smallVariants?.length === 0 && (structuralVariants?.length ?? 0) === 0 ? (
            <section className="surface-card report-empty">
              <p className="report-paragraph">No variants were tagged for reporting in this version.</p>
            </section>
          ) : null}

          <AnalysisSections record={record} version={version} />

          <SignedChecks record={record} />
        </>
      )}

      <footer className="surface-card report-footer">
        <p className="report-footer-timestamp">
          Signed version {version} · signed out {formatReportTime(detail.signed_out_at)}
          {reportCode ? ` · report code ${reportCode}` : ''}
        </p>
        <p className="report-footer-software">
          <span className="report-footer-label">Signed with:</span>{' '}
          {signedWith ?? 'not in the signed record'}
        </p>
        <ReportSoftwareIdentity reportBuild={reportBuild} label="Rendered by:" />
        <p className="report-footer-versions">
          <span className="report-footer-label">Modules &amp; versions as signed:</span>{' '}
          {!record?.modules
            ? 'not in the signed record'
            : (describeModuleVersions(record.modules) ?? 'none recorded')}
        </p>
        <p className="report-footer-versions">
          <span className="report-footer-label">Reference data as signed:</span>{' '}
          {describeReferenceDataLine(record)}
        </p>
      </footer>
    </div>
  );
};

export default SignedFamilyReport;
