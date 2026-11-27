import React from 'react';
import { Link, useSearchParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';

import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import type { ClinvarCogaOut, ClinvarReanalysisOut } from '../../lib/apiSchema.generated';
import { clinvarReleaseLabel, clinvarValueText } from '../../lib/clinvar';
import { useProjectCatalog } from '../../lib/reference';
import { routePath } from '../../lib/routePath';
import QueryFailure from '../../components/QueryFailure';
import { formatReportTime } from '../families/signedReportRecord';

/** Each ALT's ClinVar value, with the release it is of: "Likely pathogenic ★☆☆☆ (2026-11-22)". */
const describeValues = (values: ClinvarCogaOut[]): string =>
  values.length
    ? values
        .map(
          (value) =>
            `${values.length > 1 ? `${value.alt}: ` : ''}${clinvarValueText(value)}${value.release ? ` (${value.release})` : ''}`,
        )
        .join('; ')
    : 'none';

/**
 * The re-analysis signal after sign-out (the ClinVar design's phase 3, the owner's decision B10):
 * a project's signed analyses one of whose small variants CoGA's current ClinVar release
 * classifies otherwise than its signed version saw. Read only: a signed version never changes,
 * and a re-analysis is a new one; who follows the list up, and how, is the laboratory's
 * procedure. No mail goes out.
 */
const ClinvarReanalysisPage: React.FC = () => {
  const [search, setSearch] = useSearchParams();
  const projects = useProjectCatalog();
  const choices = projects.data ?? [];
  const projectId = search.get('project') ?? (choices.length === 1 ? choices[0].id : '');

  const list = useQuery<ClinvarReanalysisOut>({
    queryKey: ['clinvar-reanalysis', projectId],
    enabled: Boolean(projectId),
    queryFn: async () => (await api.get(apiPath`/projects/${projectId}/clinvar-reanalysis`)).data as ClinvarReanalysisOut,
  });
  const releases = Object.entries(list.data?.releases ?? {});
  const items = list.data?.items ?? [];

  return (
    <div className="page-shell space-y-5">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-1">
            <p className="page-kicker">Signed records</p>
            <h2 className="catalog-card-title">ClinVar reclassifications since sign-out</h2>
            <p className="catalog-card-copy">
              The signed analyses of a project with a reported or classified small variant that CoGA’s current
              ClinVar release classifies otherwise than its signed version saw. CoGA changes nothing of a signed
              analysis; follow the list up as the laboratory’s procedure says. A new review status or other stars
              of the same classification is not listed.
            </p>
          </div>
        </div>
        <label className="field-label">
          <span>Project</span>
          <select
            value={projectId}
            onChange={(event) => setSearch(event.target.value ? { project: event.target.value } : {})}
          >
            <option value="">Choose a project</option>
            {choices.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
        </label>
        {projects.isError ? <QueryFailure what="your projects" error={projects.error} onRetry={() => void projects.refetch()} /> : null}
      </section>

      {list.isError ? <QueryFailure what="the ClinVar reclassifications" error={list.error} onRetry={() => void list.refetch()} /> : null}
      {list.isLoading && projectId ? (
        <section className="surface-card">
          <p className="report-paragraph">Reading the project’s signed analyses…</p>
        </section>
      ) : null}
      {list.data ? (
        <section className="surface-card space-y-3" aria-label="ClinVar reclassifications">
          <p className="report-paragraph">
            {releases.length
              ? `Read against ${releases
                  .map(([assembly, release]) => (release ? `${clinvarReleaseLabel(release)} for ${assembly}` : `no ClinVar release for ${assembly}`))
                  .join(', ')}.`
              : 'The project has no signed analysis with a classified small variant.'}
          </p>
          {items.length ? (
            <div className="data-table-shell">
              <table className="analysis-table">
                <thead>
                  <tr>
                    <th>Family</th>
                    <th>Signed version</th>
                    <th>Variant</th>
                    <th>In the record</th>
                    <th>Class</th>
                    <th>ClinVar as signed</th>
                    <th>ClinVar now</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={`${item.family_id}-${item.analysis ?? ''}-${item.variant_id}`}>
                      <td>
                        {item.family_id}
                        {item.analysis ? ` (${item.analysis})` : ''}
                      </td>
                      <td>
                        <Link to={routePath`/families/${item.family_id}/report?version=${item.version}`}>
                          Version {item.version}
                        </Link>
                        , {formatReportTime(item.signed_out_at)} · {item.report_code}
                      </td>
                      <td>{item.variant_id}</td>
                      <td>{item.reported ? 'Reported' : 'Classified, not reported'}</td>
                      <td>{item.classification ?? 'none'}</td>
                      <td>{describeValues(item.clinvar_coga_frozen)}</td>
                      <td>{describeValues(item.clinvar_coga_now)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : releases.length ? (
            <p className="report-paragraph">
              No signed analysis of this project has a variant that the current release classifies otherwise.
            </p>
          ) : null}
        </section>
      ) : null}
    </div>
  );
};

export default ClinvarReanalysisPage;
