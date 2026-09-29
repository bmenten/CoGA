import { useState, useMemo, type FC } from 'react';
import { useParams, useLocation, Link, useNavigate } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import type { ApiFamilyRecord } from '../../lib/apiTypes';
import CircosPlot, { Chromosome, Variant, CHROMS } from '../../components/visualizations/CircosPlot';
import PageState from '../../components/PageState';
import VizErrorOverlay from '../../components/visualizations/VizErrorOverlay';
import { formatChromosomeLabel } from '../../lib/chromosomes';
import { getErrorMessage } from '../../lib/errorMessage';
import { useFamilyReference } from '../../lib/reference';
import { apiPath, raw } from '../../lib/apiPath';

const ASSEMBLY = 'GRCh38';

const CircosPlotPage: FC = () => {
  const { familyId } = useParams<{ familyId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const preferredProjectId = useMemo(
    () => new URLSearchParams(location.search).get('project_id') || undefined,
    [location.search],
  );

  const { data: family } = useQuery<Pick<ApiFamilyRecord, 'projects'>>({
    queryKey: ['family', familyId],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const response = await api.get(apiPath`/families/${familyId}`);
      return response.data as Pick<ApiFamilyRecord, 'projects'>;
    },
  });

  const { projectId: resolvedProjectId } = useFamilyReference(
    family?.projects as string[] | undefined,
    preferredProjectId,
  );

  const queryParams = useMemo(() => {
    const p = new URLSearchParams(location.search);
    if (resolvedProjectId) {
      p.set('project_id', resolvedProjectId);
    }
    p.set('page_size', '0');
    return p;
  }, [location.search, resolvedProjectId]);

  const resolvedSearch = useMemo(() => {
    const params = new URLSearchParams(location.search);
    params.delete('project_id');
    if (resolvedProjectId) {
      params.set('project_id', resolvedProjectId);
    }
    return params.toString();
  }, [location.search, resolvedProjectId]);

  const [selected, setSelected] = useState<Record<string, boolean>>(() =>
    CHROMS.reduce(
      (acc, c) => ({ ...acc, [c]: true }),
      {} as Record<string, boolean>
    )
  );

  const {
    data: chromData,
    error: chromError,
    refetch: refetchChroms,
  } = useQuery<Chromosome[]>({
    queryKey: ['circos-chromosomes', ASSEMBLY],
    queryFn: async () => {
      const response = await api.get(apiPath`/chromosomes/${ASSEMBLY}/details`);
      const chromosomes = new Map<string, Chromosome>();
      (response.data as Chromosome[]).forEach((entry) => {
        const chrom = entry.chr.replace(/^chr/i, '');
        chromosomes.set(chrom, { ...entry, chr: chrom });
      });
      return CHROMS.map((chrom) => chromosomes.get(chrom)).filter(Boolean) as Chromosome[];
    },
  });

  const {
    data: svPage,
    isError: variantsFailed,
    refetch: refetchVariants,
  } = useQuery<{ variants: Variant[]; capped: boolean; limit: number | null }>({
    queryKey: ['family-circos', familyId, queryParams.toString()],
    queryFn: async () => {
      const res = await api.get(
        apiPath`/families/${familyId}/structural-variants?${raw(queryParams.toString())}`
      );
      const all = res.data.variants as Variant[];
      return {
        variants: all.filter((v) => v.type),
        // Past the backend's candidate cap the list stops part-way through the genome, so
        // the last chromosomes would be drawn without their links (#589).
        capped: Boolean(res.data.total_is_estimated),
        limit: typeof res.data.count_limit === 'number' ? res.data.count_limit : null,
      };
    },
    enabled: !!familyId,
  });
  const tooManyVariants = Boolean(svPage?.capped);
  // Undefined until loaded, and while failed or capped: none is drawn, never as "none".
  const variants = tooManyVariants ? undefined : svPage?.variants;

  const toggleChrom = (chr: string) => {
    setSelected((prev) => ({ ...prev, [chr]: !prev[chr] }));
  };

  const selectAll = () =>
    setSelected(
      CHROMS.reduce(
        (acc, c) => ({ ...acc, [c]: true }),
        {} as Record<string, boolean>
      )
    );

  // A failed request must not read as a plot still loading, forever (#589, #510).
  if (!chromData && chromError) {
    return (
      <PageState
        kicker="Visualization"
        title="Could not load the chromosomes"
        message={`${getErrorMessage(chromError, 'The request failed').replace(/\.+$/, '')}. The circos plot needs them.`}
        action={
          <button type="button" className="button-secondary" onClick={() => void refetchChroms()}>
            Retry
          </button>
        }
      />
    );
  }

  if (!chromData) {
    return (
      <PageState
        kicker="Visualization"
        title="Loading circos plot"
        message="Preparing chromosome scaffolds and structural variant links."
      />
    );
  }

  const handleChromClick = (chr: string) =>
    navigate(`/families/${familyId}/chromosome/${chr}${resolvedSearch ? `?${resolvedSearch}` : ''}`);

  const handleVariantClick = (v: Variant) => {
    if (v.type?.toUpperCase() !== 'BND') return;
    const chr1 = v.chr.replace(/^chr/i, '');
    const chr2 = (v.remote_chr || v.chr).replace(/^chr/i, '');
    const params = new URLSearchParams(location.search);
    params.delete('chrom');
    params.append('chrom', chr1);
    if (chr2 !== chr1) {
      params.append('chrom', chr2);
    }
    if (resolvedProjectId) {
      params.set('project_id', resolvedProjectId);
    }
    navigate(`/families/${familyId}/genome?${params.toString()}`);
  };

  return (
    <div className="page-shell analysis-grid analysis-grid--viewer">
      <aside className="analysis-sidebar analysis-sidebar--viewer">
        <section className="analysis-panel-muted">
          <h2 className="analysis-section-title">Chromosomes</h2>
          <div className="mt-3 flex gap-2 text-sm">
            <button onClick={selectAll} className="subtle-link">
              Select all
            </button>
            <button
              onClick={() =>
                setSelected(
                  CHROMS.reduce(
                    (acc, c) => ({ ...acc, [c]: false }),
                    {} as Record<string, boolean>
                  )
                )
              }
              className="subtle-link"
            >
              Deselect all
            </button>
          </div>
          <ul className="mt-3 grid grid-cols-2 gap-y-2 gap-x-2">
            {CHROMS.map((c) => (
              <li key={c}>
                <label className="analysis-checkbox whitespace-nowrap">
                  <input
                    type="checkbox"
                    checked={selected[c]}
                    onChange={() => toggleChrom(c)}
                  />
                  {formatChromosomeLabel(c)}
                </label>
              </li>
            ))}
          </ul>
        </section>
      </aside>
      <main className="analysis-main analysis-main--viewer">
        <section className="surface-card page-top-card">
          <div className="page-header">
            <div className="space-y-2">
              <p className="page-kicker">Visualization</p>
              <h1 className="catalog-card-title">Circos plot for family {familyId}</h1>
              <p className="catalog-card-copy">
                Explore structural rearrangements across chromosomes.
              </p>
            </div>
            <Link
              to={`/families/${familyId}/structural-variants${resolvedSearch ? `?${resolvedSearch}` : ''}`}
              className="button-secondary hover:no-underline"
            >
              Back to variants
            </Link>
          </div>
        </section>
        <section className="viz-panel">
          {tooManyVariants && (
            <p className="analysis-count mb-4" role="status">
              {`Too many structural variants to draw${
                svPage?.limit ? ` (more than ${svPage.limit.toLocaleString()})` : ''
              }. Narrow them with the filters on the variant list, then open the plot again.`}
            </p>
          )}
          <div className="relative">
            <CircosPlot
              chromData={chromData}
              variants={variants}
              tooManyVariants={tooManyVariants}
              selected={selected}
              onChromosomeClick={handleChromClick}
              onVariantClick={handleVariantClick}
            />
            {/* The plot without its links must not read as a family without SVs (#510). */}
            {variantsFailed && (
              <VizErrorOverlay what="structural variants" onRetry={() => void refetchVariants()} />
            )}
          </div>
          {variants && variants.length === 0 && (
            <p className="analysis-count mt-4">No variants for this family.</p>
          )}
        </section>
      </main>
    </div>
  );
};

export default CircosPlotPage;
