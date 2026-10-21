import React from 'react';
import { Link, useLocation } from 'react-router';
import { useQuery } from '@tanstack/react-query';

import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import type { ReportSignoutDetail } from '../../lib/apiSchema.generated';
import { formatSoftwareVersion, useReportBuild } from '../../lib/appVersion';
import { formatCnvPoints } from '../../lib/cnvAcmg/format';
import { sexUnknownWarning } from '../../lib/embryoSegregation';
import { isNotFoundError } from '../../lib/errorMessage';
import { describePhaseCorrection } from '../../lib/haplotypePhaseCorrections';
import PageState from '../../components/PageState';
import FamilyPageHeader from './FamilyPageHeader';
import ReportSoftwareIdentity from './ReportSoftwareIdentity';
import { formatCount, joinWithAnd } from '../../lib/format';
import { CATEGORY_LABELS, depth, pct } from './niptClassification';
import { NIPT_QUALITY_FAILURE_LABELS } from './NiptQcPanel';
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
  coverageTrackCaller,
  describeBreachedMetric,
  describeCatalogue,
  describeCnvKnowledgebase,
  describeCoverageContent,
  describeCoverageFiles,
  describeCoverageTracks,
  describeCountUnitMargin,
  describeDiseaseHaplotypes,
  describeEmbryoCall,
  describeEmbryoQc,
  describeFrozenEvidenceValues,
  describeGenePanel,
  describeGeneReferenceReleases,
  describeGenotype,
  describeInherited,
  describeModuleVersions,
  describeNiptCoverage,
  describeNiptFetalFraction,
  describeNiptFetalSex,
  describeNiptFunnel,
  describeNiptVariant,
  describeNoCall,
  describePgtInheritance,
  describePgtInputs,
  describePgtMembers,
  describeQcThresholds,
  describeRepeatLocusCalls,
  describeRepeatThresholds,
  describeReportSections,
  describeSignedDrift,
  describeSignedQcVerdict,
  describeSignedStructuralDrift,
  describeSmallVariantDescription,
  describeSmallVariantLocus,
  describeStructuralVariantDescription,
  formatReportTime,
  geneReferenceSourceLabel,
  isNiptRecord,
  NIPT_NOT_CLASSIFIED_REASONS,
  paraphaseStatusWords,
  parseSignedReport,
  qcStatusLabel,
  repeatStatusWords,
  reportViewSearch,
  type SignedAcknowledgement,
  type SignedGenotype,
  type SignedNipt,
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
 * it, said first when part of it cannot be read here (a malformed list, TRACE-17). Every
 * section of signed_record_sections.py is shown through this, so none drops out of the signed
 * view without a trace.
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
        <>
          {section.malformed ? (
            <p className="report-paragraph" role="note">
              Part of {what} cannot be read here ({section.malformed}{' '}
              {section.malformed === 1 ? 'list or entry' : 'lists or entries'}): it is left out below, and
              the downloaded record holds it as stored.
            </p>
          ) : null}
          {children(section.value)}
        </>
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
        descriptions.state === 'recorded' && descriptions.malformed ? (
          // Its entry may be one this view cannot read (TRACE-17): which variant such an entry
          // describes cannot be told, so neither cause is claimed.
          <p className="report-paragraph" role="note">
            The record holds no description of this variant that this view can read: it was not
            in the family’s data when version {version} was signed, or its description is among
            those this view cannot read ({descriptions.malformed}{' '}
            {descriptions.malformed === 1 ? 'list or entry' : 'lists or entries'}; the downloaded
            record holds them as stored).
          </p>
        ) : (
          <p className="report-paragraph">
            The record holds no description of this variant: it was not in the family’s data when
            version {version} was signed.
          </p>
        )
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

/** The coverage tracks as the footer lists them, or why the record has none to list. */
const describeCoverageTracksLine = (record: SignedReport | null): string => {
  const coverage = record?.coverageTracks;
  if (!coverage || coverage.state === 'absent') return 'not in the signed record';
  if (coverage.state === 'unavailable') return `not captured (${coverage.reason})`;
  return describeCoverageTracks(coverage.value);
};

/**
 * What each coverage track of the family's samples held when the version was signed, as the
 * depth readers read it (TRACE-9; owner decision of 2026-10-20), with the files each was loaded
 * from: which kind of coverage a depth check rested on. Every family's record holds it since.
 */
const CoverageTracksSection: React.FC<{ record: SignedReport; version: number }> = ({ record, version }) => {
  // The checks that read a coverage track as depth; a family that is no NIPT family hears nothing
  // of NIPT.
  const depthReaders = isNiptRecord(record)
    ? 'by the mtDNA coverage and by the NIPT coverage check without a per-target table'
    : 'by the mtDNA coverage and its depth verdict';
  return (
    <RecordSectionBlock
      title="Coverage tracks"
      what="what each coverage track holds"
      section={record.coverageTracks}
      version={version}
    >
      {(tracks) =>
        tracks.length ? (
          <>
            <p className="report-paragraph">
              What each coverage track of the family’s samples held when this version was signed, as CoGA
              read it: read depth, a log2 ratio, or unknown. A coverage BED holds what it declared when it
              was loaded; another caller’s track what its import recorded. Only a track of read depth is
              read as depth, {depthReaders}.
            </p>
            <ul className="report-signed-list">
              {tracks.map((track, index) => (
                <li key={`${track.sample}-${track.source}-${index}`}>
                  <strong>{track.sample}</strong> {coverageTrackCaller(track.source)}:{' '}
                  {describeCoverageContent(track)}. {describeCoverageFiles(track)}.
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="report-paragraph">The family’s samples had no coverage track.</p>
        )
      }
    </RecordSectionBlock>
  );
};

// A NIPT check that could not run reads "Not run", as on the NIPT page.
const niptStatusLabel = (status: string | null): string =>
  qcStatusLabel(status === 'unknown' ? 'skip' : status);

const NiptResultsBody: React.FC<{ nipt: SignedNipt }> = ({ nipt }) => {
  const { fetalFraction: ff, paternity, fetalSex: sex, plasmaSample: plasma, targetCoverage: coverage } = nipt;
  return (
    <>
      <p className="report-paragraph">
        The monogenic NIPT analysis of the maternal plasma {nipt.samples.maternalPlasma ?? 'sample'} against the
        father {nipt.samples.father ?? 'sample'}
        {nipt.samples.fetus ? `, for fetus ${nipt.samples.fetus}` : ''}, as CoGA computed it when this version was
        signed.
      </p>
      <div className="report-section">
        <h3 className="report-subheading">Fetal fraction and quality checks</h3>
        <ul className="report-signed-list">
          <li>
            Fetal fraction: {ff ? describeNiptFetalFraction(ff) : 'not in the record'} — {niptStatusLabel(ff?.status ?? null)}
            {ff?.lowConfidence && ff.ffComputed !== null ? ' (low confidence: too few sites or a wide interval)' : ''}
            {nipt.deNovoWindow && nipt.deNovoWindow.strictMin !== null && nipt.deNovoWindow.strictMax !== null
              ? `; fetal window for de novo alleles ${pct(nipt.deNovoWindow.strictMin)}–${pct(nipt.deNovoWindow.strictMax)}`
              : ''}
          </li>
          <li>
            Paternity: {niptStatusLabel(paternity?.status ?? null)}
            {paternity?.message ? ` — ${paternity.message}` : ''}
          </li>
          <li>
            Fetal sex: {sex ? describeNiptFetalSex(sex) : 'not in the record'} — {niptStatusLabel(sex?.status ?? null)}
            {sex?.status === 'fail' && sex.message ? ` — ${sex.message}` : ''}
          </li>
          <li>
            Maternal plasma sample: {niptStatusLabel(plasma?.status ?? null)}
            {plasma?.message ? ` — ${plasma.message}` : ''}
          </li>
          <li>
            Target coverage:{' '}
            {coverage
              ? `${describeNiptCoverage(coverage)}${
                  coverage.weakGenes.length
                    ? `; genes with a weak target: ${coverage.weakGenes
                        .map((gene) => `${gene.gene} (${gene.weakTargets ?? '?'} of ${gene.targets ?? '?'}, lowest mean ${depth(gene.minMean)})`)
                        .join(', ')}`
                    : ''
                }`
              : 'no per-target coverage table'}
          </li>
          <li>
            Filters: {describeNiptFunnel(nipt.filterCounts)}
            {nipt.qualityFailures.length
              ? `; quality-filter failures: ${nipt.qualityFailures
                  .map(([reason, count]) => `${count} ${NIPT_QUALITY_FAILURE_LABELS[reason] ?? reason}`)
                  .join(', ')}`
              : ''}
          </li>
          <li>
            Categories:{' '}
            {nipt.categoryCounts.length
              ? nipt.categoryCounts
                  .map(([category, count]) => `${CATEGORY_LABELS[category] ?? `Category ${category}`}: ${count}`)
                  .join('; ')
              : 'none recorded'}
          </li>
        </ul>
      </div>
      <div className="report-section">
        <h3 className="report-subheading">Reported variants in the NIPT analysis</h3>
        {nipt.variants.length || nipt.notClassified.length ? (
          <ul className="report-signed-list">
            {nipt.variants.map((variant, index) => (
              <li key={`${variant.variantId ?? 'variant'}-${variant.source ?? ''}-${index}`}>
                <strong>{variant.variantId ?? 'An entry that cannot be read'}</strong>
                {variant.genes.length ? ` (${variant.genes.join(', ')})` : ''}: {describeNiptVariant(variant)}
                {variant.flags.length ? ` Flags: ${variant.flags.join(', ')}.` : ''}
              </li>
            ))}
            {nipt.notClassified.map((entry) => (
              <li key={`not-classified-${entry.variantId}`}>
                <strong>{entry.variantId}</strong>: not classified — {NIPT_NOT_CLASSIFIED_REASONS[entry.reason] ?? entry.reason}.
              </li>
            ))}
          </ul>
        ) : (
          <p className="report-paragraph">No variant was tagged for reporting.</p>
        )}
        {nipt.recessiveGenes.length ? (
          <>
            <p className="report-paragraph">
              Recessive fetal risk of the genes in which the reported variants make both parents carriers:
            </p>
            <ul className="report-signed-list">
              {nipt.recessiveGenes.map((gene, index) => (
                <li key={`${gene.gene ?? 'gene'}-${index}`}>
                  <strong>{gene.gene ?? 'A gene'}</strong>: {pct(gene.risk)}
                  {gene.maternalVariantId && gene.paternalVariantId
                    ? gene.maternalVariantId === gene.paternalVariantId
                      ? ` (the fetus homozygous for ${gene.maternalVariantId})`
                      : ` (maternal ${gene.maternalVariantId}, paternal ${gene.paternalVariantId})`
                    : ''}
                  {gene.riskUsesPrior ? '; an inheritance the cfDNA did not tell is read at its Mendelian prior' : ''}
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </div>
      <div className="report-section">
        <h3 className="report-subheading">What the analysis ran with</h3>
        <ul className="report-signed-list">
          <li>
            Per-target coverage:{' '}
            {nipt.targetTables.length
              ? nipt.targetTables
                  .map(
                    (table) =>
                      `${table.sample}: ${table.file ?? 'file not named'}${
                        table.targets !== null ? `, ${formatCount(table.targets)} targets` : ''
                      }${table.loadedAt ? `, loaded ${formatReportTime(table.loadedAt)}` : ''}`,
                  )
                  .join('; ')
              : 'no table was loaded'}
          </li>
          <li>
            Artifact list:{' '}
            {nipt.artifactList
              ? `${nipt.artifactList.assayKey ?? 'scope not named'}, ${formatCount(nipt.artifactList.entries ?? 0)} alleles${
                  nipt.artifactList.digest ? `, digest ${nipt.artifactList.digest.slice(0, 12)}` : ''
                }; ${formatCount(nipt.artifactList.removed ?? 0)} left out of this analysis${
                  nipt.artifactList.kept.length
                    ? `; kept in by the family’s annotation: ${nipt.artifactList.kept
                        .map((entry) => `${entry.variantId} (${entry.reason})`)
                        .join(', ')}`
                    : ''
                }`
              : 'not in the record'}
          </li>
          <li>Model: {nipt.modelReference ?? 'not in the record'}.</li>
        </ul>
      </div>
    </>
  );
};

/**
 * A monogenic NIPT family's NIPT results, as its record froze them. Any other family's record
 * holds none, and nothing is said of them.
 */
const NiptResults: React.FC<{ record: SignedReport; version: number }> = ({ record, version }) =>
  isNiptRecord(record) ? (
    <RecordSectionBlock title="NIPT results" what="the NIPT results" section={record.nipt} version={version}>
      {(nipt) => <NiptResultsBody nipt={nipt} />}
    </RecordSectionBlock>
  ) : null;

/**
 * A PGT family's embryo calls at the ROI, as the record froze them: the calls the family page
 * showed, each with what it rests on. A PGT family's record signed before CoGA froze them
 * says it does not hold them; the record of any other family has no such section.
 */
const PgtAnalysisSection: React.FC<{ record: SignedReport; version: number }> = ({ record, version }) => {
  if (record.pgtAnalysis.state === 'absent' && record.sampleQc?.application !== 'pgt') return null;
  return (
    <RecordSectionBlock
      title="PGT embryo results"
      what="the PGT embryo results"
      section={record.pgtAnalysis}
      version={version}
    >
      {(analysis) => {
        const roi = analysis.roi;
        const disease = describeDiseaseHaplotypes(analysis);
        const members = describePgtMembers(analysis.members);
        return (
          <>
            {roi ? (
              <p className="report-paragraph">
                Each embryo’s call at the region of interest <strong>{roi.label ?? roi.query ?? 'not named'}</strong>{' '}
                ({roi.chrom ?? '?'}:{roi.start?.toLocaleString() ?? '?'}–{roi.end?.toLocaleString() ?? '?'}), as the
                family page showed it when version {version} was signed. Inheritance model:{' '}
                {describePgtInheritance(analysis)}.{' '}
                {disease
                  ? `Disease haplotype at the ROI: ${disease}.`
                  : 'No disease haplotype could be resolved at the ROI, so no embryo could be called.'}
              </p>
            ) : (
              <p className="report-paragraph">
                The family had no region of interest when version {version} was signed, so no embryo was called.
              </p>
            )}
            {analysis.phaseCorrections.length ? (
              <p className="report-paragraph" role="note">
                ⚠ {analysis.phaseCorrections.map(describePhaseCorrection).join(' ')} This is inside or close to the
                ROI: where the phase switched is known only to within the children’s switches, so each call across the
                locus is less certain, and an embryo’s own crossover there may not show.
              </p>
            ) : null}
            {analysis.qcThresholds && analysis.embryos.some((embryo) => embryo.qcVerdict) ? (
              <p className="report-paragraph">
                Each embryo’s phasing QC at the ROI was checked against these thresholds:{' '}
                {describeQcThresholds(analysis.qcThresholds)}. A call that did not meet them carries a warning and
                stands as called.
              </p>
            ) : null}
            {analysis.embryos.length ? (
              <ul className="report-signed-list">
                {analysis.embryos.map((embryo, index) => {
                  const inherited = describeInherited(embryo.inherited);
                  const warning = embryo.sexDependent
                    ? sexUnknownWarning(embryo.sexDependent)
                    : describeNoCall(embryo, analysis);
                  return (
                    <li key={`${embryo.sample}-${index}`}>
                      <strong>{embryo.sample}</strong>
                      {embryo.sex ? ` (${embryo.sex})` : ''}: <strong>{describeEmbryoCall(embryo)}</strong>.
                      {inherited ? ` Inherited at the ROI: ${inherited}.` : ''}
                      {warning ? ` ${warning}` : ''}
                      {embryo.recombinations.length
                        ? ` ⚠ Recombination inside the ROI or within ${(
                            analysis.recombinationFlank ?? 0
                          ).toLocaleString()} bp of it, at ${embryo.recombinations
                            .map((at) => at.toLocaleString())
                            .join(', ')}.`
                        : ''}
                      {embryo.qc
                        ? ` QC at the ROI: ${describeEmbryoQc(embryo.qc)}.`
                        : embryo.call
                          ? ` QC at the ROI: not counted (${
                              analysis.markersTruncated
                                ? 'the ROI holds more sites than the marker review reads'
                                : 'the embryo is not recorded as a child of the couple'
                            }).`
                          : ''}
                      {embryo.qcVerdict ? ` ${describeSignedQcVerdict(embryo.qcVerdict)}` : ''}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="report-paragraph">The record lists no embryo.</p>
            )}
            {members ? (
              <p className="report-paragraph">The members’ roles and statuses as the analysis read them: {members}.</p>
            ) : null}
            <p className="report-paragraph">Read from {describePgtInputs(analysis)}.</p>
          </>
        );
      }}
    </RecordSectionBlock>
  );
};

/**
 * What the analysis looked at beyond the reported variants, as the record froze it: a PGT
 * family's embryo calls, the gene panels its searches applied, the repeat expansions and
 * Paraphase results of the family's samples, what each of their coverage tracks held, and the
 * reference-data releases. A section the record predates says so.
 */
const AnalysisSections: React.FC<{ record: SignedReport; version: number }> = ({ record, version }) => (
  <>
    <PgtAnalysisSection record={record} version={version} />

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
          {/* A record signed before CoGA froze the near-boundary warnings reads as it was signed. */}
          {repeats.countUnitMargin !== null ? (
            <p className="report-paragraph">{describeCountUnitMargin(repeats.countUnitMargin)}</p>
          ) : null}
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
                  {describeRepeatLocusCalls(locus)}
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

    <CoverageTracksSection record={record} version={version} />

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

          <NiptResults record={record} version={version} />

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
        <p className="report-footer-versions">
          <span className="report-footer-label">Coverage tracks as signed:</span>{' '}
          {describeCoverageTracksLine(record)}
        </p>
      </footer>
    </div>
  );
};

export default SignedFamilyReport;
