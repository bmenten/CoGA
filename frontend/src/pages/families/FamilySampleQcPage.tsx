import React from 'react';
import { Link, useParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';

import api from '../../lib/api';
import { QC_STATUS_CHIP, QC_STATUS_LABEL, worstQcStatus } from '../../lib/qcStatus';
import PageState from '../../components/PageState';
import FamilyPageHeader from './FamilyPageHeader';
import InfoTip from '../../components/InfoTip';
import Pedigree, { type PedigreeQcStatus } from '../../components/visualizations/Pedigree';
import type {
  ApiFamilyRecord,
  ApiSampleIntegrityMendelianCheck,
  ApiSampleIntegrityPaternityCheck,
  ApiSampleIntegrityQc,
  ApiSampleIntegrityRelatednessCheck,
  ApiSampleIntegritySexCheck,
  QcStatus,
} from '../../lib/apiTypes';
import { apiPath } from '../../lib/apiPath';
import { parsePedigree } from '../../lib/pedigree';

const OVERALL_COPY: Record<QcStatus, string> = {
  pass: 'All sample-integrity checks passed. No swaps or mislabelled relationships detected.',
  warn: 'Some checks need attention — review the warnings before interpretation.',
  fail: 'A check failed. Resolve possible sample swaps or pedigree errors before interpretation.',
  skip: 'Sample-integrity QC could not run (no genotypes available).',
};

const worstStatus = worstQcStatus;

const StatusChip: React.FC<{ status: QcStatus }> = ({ status }) => (
  <span className={QC_STATUS_CHIP[status]}>{QC_STATUS_LABEL[status]}</span>
);

const sexGlyph = (sex?: string | null): string => {
  const value = (sex || '').toLowerCase();
  if (value === 'male' || value === '1') return '♂';
  if (value === 'female' || value === '2') return '♀';
  return '◇';
};

const formatSex = (sex?: string | null): string => {
  const value = (sex || '').toLowerCase();
  if (value === 'male' || value === '1') return 'male';
  if (value === 'female' || value === '2') return 'female';
  return value || 'unknown';
};

// The genotype-sex cell's mark for each sex-check verdict. Only a pass confirms the
// recorded sex. A warn did not (the genotypes left the sex indeterminate, or no sex was
// recorded to compare with), nor did a skip (no chrX genotypes), so neither may look like
// a match: this is a sample-identity check (TF-06 H4).
const SEX_CHECK_MARK: Record<QcStatus, { tone: string; glyph: string }> = {
  pass: { tone: 'match', glyph: '✓' },
  warn: { tone: 'unconfirmed', glyph: '!' },
  fail: { tone: 'mismatch', glyph: '✗' },
  skip: { tone: 'unchecked', glyph: '?' },
};

// The API types the verdict as a string: an unexpected one reads as unconfirmed.
const sexCheckMark = (status: QcStatus) => SEX_CHECK_MARK[status] ?? SEX_CHECK_MARK.warn;

// The ring tooltip's words for a sex check: "(matches record)" for a pass alone. A warn or
// a skip gives its own message, which says why the sex could not be confirmed.
const sexCheckNote = (check: ApiSampleIntegritySexCheck): string => {
  if (check.status === 'pass') return `Sex ${formatSex(check.inferred_sex)} (matches record)`;
  if (check.status === 'fail') {
    return `Sex mismatch: recorded ${formatSex(check.recorded_sex)}, genotypes ${formatSex(check.inferred_sex)}`;
  }
  return check.message;
};

// Roll each sample's sex / Mendelian / relatedness checks into one ring status
// for the pedigree, with a tooltip summarising why.
const buildQcStatusBySample = (
  qc: ApiSampleIntegrityQc,
): Record<string, PedigreeQcStatus> => {
  const byId = new Map<string, { statuses: QcStatus[]; notes: string[] }>();
  const add = (id: string | undefined, status: QcStatus, note?: string) => {
    const key = (id || '').trim();
    if (!key) return;
    const entry = byId.get(key) ?? { statuses: [], notes: [] };
    entry.statuses.push(status);
    if (note) entry.notes.push(note);
    byId.set(key, entry);
  };

  qc.sex_checks.forEach((c) => add(c.sample_id, c.status, sexCheckNote(c)));
  qc.mendelian_checks.forEach((c) =>
    add(
      c.child,
      c.status,
      `Mendelian error rate ${(c.mendel_rate * 100).toFixed(2)}%`,
    ),
  );
  qc.relatedness_checks.forEach((c) => {
    const note =
      c.status === 'fail'
        ? `Relatedness ${c.sample_a}↔${c.sample_b}: expected ${c.expected_relationship}, observed ${c.inferred_relationship}`
        : undefined;
    add(c.sample_a, c.status, note);
    add(c.sample_b, c.status, note);
  });

  const result: Record<string, PedigreeQcStatus> = {};
  byId.forEach((entry, id) => {
    const status = worstStatus(entry.statuses);
    if (status === 'skip') return; // no ring when nothing actually ran for this sample
    result[id] = {
      status,
      label: entry.notes.join(' · ') || undefined,
    };
  });
  return result;
};

// Each inferred relationship gets a distinct hue so the matrix reads at a glance.
const RELATIONSHIP_COLORS: Record<string, string> = {
  duplicate: '#7c3aed', // self / monozygotic twin / sample duplicate
  'parent-child': '#2563eb',
  sibling: '#0d9488',
  'second-degree': '#d97706',
  'third-degree': '#f59e0b',
  unrelated: '#64748b',
  indeterminate: '#cbd5e1',
};

const relationshipColor = (relationship: string): string =>
  RELATIONSHIP_COLORS[relationship] ?? '#64748b';

const RELATIONSHIP_LEGEND: Array<{ key: string; label: string }> = [
  { key: 'duplicate', label: 'Duplicate / MZ twin' },
  { key: 'parent-child', label: 'Parent–child' },
  { key: 'sibling', label: 'Sibling' },
  { key: 'second-degree', label: '2nd degree' },
  { key: 'third-degree', label: '3rd degree' },
  { key: 'unrelated', label: 'Unrelated' },
];

const QcRingLegend: React.FC = () => (
  <ul className="qc-ring-legend" aria-label="Pedigree QC legend">
    <li>
      <span className="qc-ring-swatch qc-ring-swatch--pass" /> Checks passed
    </li>
    <li>
      <span className="qc-ring-swatch qc-ring-swatch--warn" /> Warning
    </li>
    <li>
      <span className="qc-ring-swatch qc-ring-swatch--fail" /> Failed — possible swap / sex or pedigree error
    </li>
  </ul>
);

const SampleQcTable: React.FC<{
  members: ApiFamilyRecord['members'];
  sexChecks: ApiSampleIntegritySexCheck[];
  mendelianChecks: ApiSampleIntegrityMendelianCheck[];
  perSample: Record<string, PedigreeQcStatus>;
}> = ({ members, sexChecks, mendelianChecks, perSample }) => {
  const sexById = new Map(sexChecks.map((c) => [c.sample_id, c]));
  const mendelByChild = new Map(mendelianChecks.map((c) => [c.child, c]));

  return (
    <div className="data-table-shell overflow-x-auto">
      <table className="analysis-table family-members-table">
        <thead>
          <tr>
            <th>Sample</th>
            <th>Role</th>
            <th>Recorded sex</th>
            <th>Genotype sex</th>
            <th>Mendelian errors</th>
            <th>Sample status</th>
          </tr>
        </thead>
        <tbody>
          {members.map((member) => {
            const sex = sexById.get(member.sample_id);
            const mendel = mendelByChild.get(member.sample_id);
            const ring = perSample[member.sample_id];
            const sexMark = sex ? sexCheckMark(sex.status) : null;
            return (
              <tr key={member.sample_id}>
                <td>
                  <span className="family-member-identity">
                    <span className="family-member-sex" aria-hidden="true">
                      {sexGlyph(member.sex)}
                    </span>
                    <span className="family-member-name-button">{member.sample_id}</span>
                    {member.affected ? (
                      <span className="family-member-affected" title="Affected">
                        *
                      </span>
                    ) : null}
                  </span>
                </td>
                <td>{member.role || '-'}</td>
                <td>{formatSex(member.sex)}</td>
                <td>
                  {sex && sexMark ? (
                    <InfoTip
                      label={sex.message}
                      className={`qc-genotype-sex qc-genotype-sex--${sexMark.tone}`}
                    >
                      {formatSex(sex.inferred_sex)} {sexMark.glyph}
                    </InfoTip>
                  ) : (
                    <span className="dashboard-link-note">-</span>
                  )}
                </td>
                <td>
                  {mendel ? (
                    <InfoTip
                      label={mendel.message}
                      className={`qc-mendel qc-mendel--${mendel.status === 'pass' ? 'ok' : mendel.status}`}
                    >
                      {(mendel.mendel_rate * 100).toFixed(2)}%
                      <span className="qc-mendel-sub">
                        {' '}
                        ({mendel.mendel_errors.toLocaleString()}/{mendel.informative_sites.toLocaleString()})
                      </span>
                    </InfoTip>
                  ) : (
                    <span className="dashboard-link-note">-</span>
                  )}
                </td>
                <td>
                  {ring?.label ? (
                    <InfoTip label={ring.label}>
                      <StatusChip status={ring.status} />
                    </InfoTip>
                  ) : (
                    <StatusChip status={ring?.status ?? 'skip'} />
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

const RelatednessMatrix: React.FC<{
  samples: string[];
  checks: ApiSampleIntegrityRelatednessCheck[];
}> = ({ samples, checks }) => {
  const byPair = new Map<string, ApiSampleIntegrityRelatednessCheck>();
  checks.forEach((c) => byPair.set([c.sample_a, c.sample_b].sort().join('|'), c));

  return (
    <div className="data-table-shell overflow-x-auto">
      <table className="qc-matrix" aria-label="Relatedness association matrix">
        <thead>
          <tr>
            <th className="qc-matrix-corner" />
            {samples.map((s) => (
              <th key={s} scope="col">
                {s}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {samples.map((rowSample, rowIndex) => (
            <tr key={rowSample}>
              <th scope="row">{rowSample}</th>
              {samples.map((colSample, colIndex) => {
                if (rowIndex === colIndex) {
                  return (
                    <td key={colSample} className="qc-matrix-diag">
                      —
                    </td>
                  );
                }
                // Symmetric matrix → show only the lower-left triangle once.
                if (colIndex > rowIndex) {
                  return <td key={colSample} className="qc-matrix-empty" aria-hidden="true" />;
                }
                const check = byPair.get([rowSample, colSample].sort().join('|'));
                if (!check) {
                  return <td key={colSample} className="qc-matrix-empty" />;
                }
                const emphasis =
                  check.status === 'fail'
                    ? ' qc-matrix-cell--fail'
                    : check.status === 'warn'
                      ? ' qc-matrix-cell--warn'
                      : '';
                const color = relationshipColor(check.inferred_relationship);
                const tooltip = `${check.sample_a} ↔ ${check.sample_b}: expected ${check.expected_relationship}, observed ${check.inferred_relationship}. φ=${check.kinship.toFixed(3)}, IBS0=${check.ibs0_rate.toFixed(3)}. ${check.message}`;
                return (
                  <td
                    key={colSample}
                    className={`qc-matrix-cell${emphasis}`}
                    style={{ background: `${color}22` }}
                  >
                    <InfoTip label={tooltip} className="qc-matrix-cell-inner">
                      <span className="qc-matrix-rel" style={{ color }}>
                        {check.inferred_relationship}
                      </span>
                      <span className="qc-matrix-metric">φ {check.kinship.toFixed(3)}</span>
                      <span className="qc-matrix-metric">IBS0 {check.ibs0_rate.toFixed(3)}</span>
                    </InfoTip>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <ul className="qc-matrix-legend" aria-label="Relationship colours">
        {RELATIONSHIP_LEGEND.map((entry) => (
          <li key={entry.key}>
            <span
              className="qc-matrix-legend-swatch"
              style={{ background: relationshipColor(entry.key) }}
            />
            {entry.label}
          </li>
        ))}
      </ul>
    </div>
  );
};

const PaternityCard: React.FC<{ check: ApiSampleIntegrityPaternityCheck }> = ({ check }) => {
  const homSeen = check.hom_alt_transmitted ?? 0;
  const homAll = homSeen + (check.hom_alt_not_transmitted ?? 0);
  const hetSeen = check.het_transmitted ?? 0;
  const hetAll = hetSeen + (check.het_not_transmitted ?? 0);
  return (
    <section className="surface-card space-y-2">
      <h2 className="section-title">Paternity (the father&apos;s alleles in the cfDNA)</h2>
      <div className="qc-check-row">
        <StatusChip status={check.status} />
        <div className="qc-check-body">
          <p className="qc-check-title">
            Father {check.father} — homozygous alleles seen {homSeen} of {homAll} (all expected), het alleles seen{' '}
            {hetSeen} of {hetAll} (half expected)
          </p>
          <p className="table-subtle">{check.message}</p>
        </div>
      </div>
    </section>
  );
};

const FamilySampleQcPage: React.FC = () => {
  const { familyId } = useParams<{ familyId: string }>();

  const {
    data: qc,
    isLoading: qcLoading,
    isError: qcError,
  } = useQuery<ApiSampleIntegrityQc>({
    queryKey: ['family', familyId, 'sample-integrity-qc'],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}/qc/sample-integrity`);
      return res.data as ApiSampleIntegrityQc;
    },
  });

  // The pedigree drawing + members table are reused from the family record.
  const { data: family } = useQuery<ApiFamilyRecord>({
    queryKey: ['family', familyId],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}`);
      return res.data as ApiFamilyRecord;
    },
  });

  if (!familyId) {
    return <PageState kicker="Sample QC" title="Family not specified" />;
  }

  if (qcLoading) {
    return (
      <PageState
        loading
        kicker="Sample QC"
        title="Running sample-integrity checks"
        message="Estimating relatedness, Mendelian-error rate and genotype sex."
      />
    );
  }

  if (qcError || !qc) {
    return (
      <PageState
        kicker="Sample QC"
        title="QC could not be computed"
        message="The genotypes for this family could not be analysed."
        action={
          <Link to={`/families/${familyId}`} className="button-secondary">
            Back to family
          </Link>
        }
      />
    );
  }

  const perSample = buildQcStatusBySample(qc);
  const members = family?.members ?? [];
  const pedRows = parsePedigree(family?.pedigree);
  const hasPedigree = members.length > 0 || pedRows.length > 0;

  const relatednessSamples = Array.from(
    new Set(qc.relatedness_checks.flatMap((c) => [c.sample_a, c.sample_b])),
  );
  // Order matrix axes by family-member order where possible, else alphabetically.
  const memberOrder = new Map(members.map((m, index) => [m.sample_id, index]));
  relatednessSamples.sort((a, b) => {
    const oa = memberOrder.get(a);
    const ob = memberOrder.get(b);
    if (oa !== undefined && ob !== undefined) return oa - ob;
    if (oa !== undefined) return -1;
    if (ob !== undefined) return 1;
    return a.localeCompare(b);
  });

  const relatednessTitle =
    qc.application === 'pgt' ? 'Parentage (embryos ↔ parents)' : 'Relatedness vs pedigree';

  return (
    <div className="page-shell space-y-6">
      <FamilyPageHeader
        kicker={`Sample-integrity QC · ${qc.application_label || qc.application}`}
        familyId={familyId}
        family={family}
      >
        <p className="catalog-card-copy">
          {qc.genotype_source
            ? `${qc.genotype_source} genotypes · ${qc.autosomal_sites.toLocaleString()} autosomal sites`
            : 'cfDNA classification'}
        </p>
      </FamilyPageHeader>

      <section className={`surface-card qc-overall qc-overall--${qc.overall_status}`}>
        <div className="qc-overall-head">
          <StatusChip status={qc.overall_status} />
          <p className="report-paragraph">{OVERALL_COPY[qc.overall_status]}</p>
        </div>
        {qc.application_summary ? <p className="table-subtle">{qc.application_summary}</p> : null}
        {qc.notes.length ? (
          <ul className="report-criteria-list">
            {qc.notes.map((note) => (
              <li key={note} className="table-subtle">
                {note}
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      {hasPedigree ? (
        <section className="surface-card space-y-3">
          <div className="qc-section-head">
            <h2 className="section-title">Pedigree &amp; sample integrity</h2>
            <QcRingLegend />
          </div>
          <p className="table-subtle">
            Each individual&apos;s symbol is outlined by its roll-up QC status — sex concordance and
            Mendelian transmission. Hover a symbol (or a row in the table below) for details.
          </p>
          <div className="qc-pedigree-frame overflow-x-auto">
            <Pedigree
              rows={pedRows}
              members={members}
              relationships={family?.relationships}
              qcStatusBySample={perSample}
            />
          </div>
        </section>
      ) : null}

      {members.length && (qc.sex_checks.length || qc.mendelian_checks.length) ? (
        <section className="surface-card space-y-2">
          <h2 className="section-title">Family members &amp; per-sample checks</h2>
          <SampleQcTable
            members={members}
            sexChecks={qc.sex_checks}
            mendelianChecks={qc.mendelian_checks}
            perSample={perSample}
          />
        </section>
      ) : null}

      {relatednessSamples.length ? (
        <section className="surface-card space-y-2">
          <h2 className="section-title">{relatednessTitle}</h2>
          <p className="table-subtle">
            Pairwise kinship (φ) and IBS0, coloured by the inferred relationship (lower triangle —
            the matrix is symmetric). Pairs whose observed relationship contradicts the pedigree —
            including co-parents who look related (consanguinity) — are outlined in red.
          </p>
          <RelatednessMatrix samples={relatednessSamples} checks={qc.relatedness_checks} />
        </section>
      ) : null}

      {qc.paternity_check ? <PaternityCard check={qc.paternity_check} /> : null}

      {qc.fetal_sex_check ? (
        <section className="surface-card space-y-2">
          <h2 className="section-title">Fetal sex (paternal X transmission)</h2>
          <div className="qc-check-row">
            <StatusChip status={qc.fetal_sex_check.status} />
            <div className="qc-check-body">
              <p className="qc-check-title">
                Fetus appears {qc.fetal_sex_check.inferred_sex} — {qc.fetal_sex_check.x_transmitted}{' '}
                paternal-X transmitted, {qc.fetal_sex_check.x_not_transmitted} absent
              </p>
              <p className="table-subtle">{qc.fetal_sex_check.message}</p>
            </div>
          </div>
        </section>
      ) : null}

      {qc.category_qc_check ? (
        <section className="surface-card space-y-2">
          <h2 className="section-title">cfDNA category QC</h2>
          <div className="qc-check-row">
            <StatusChip status={qc.category_qc_check.status} />
            <div className="qc-check-body">
              <p className="qc-check-title">
                Maternal transmission {(qc.category_qc_check.maternal_inherited_rate * 100).toFixed(0)}%
                ({qc.category_qc_check.maternal_inherited}/{qc.category_qc_check.maternal_informative}{' '}
                maternal-het sites) · {qc.category_qc_check.denovo} de-novo ·{' '}
                {qc.category_qc_check.paternal_absent} paternal-absent
              </p>
              <p className="table-subtle">{qc.category_qc_check.message}</p>
            </div>
          </div>
        </section>
      ) : null}
    </div>
  );
};

export default FamilySampleQcPage;
