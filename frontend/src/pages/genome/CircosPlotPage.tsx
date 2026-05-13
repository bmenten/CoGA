import { useState, useMemo, type FC } from 'react';
import { useParams, useLocation, Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import type { ApiFamilyRecord } from '../../lib/apiTypes';
import CircosPlot, { Chromosome, Variant, CHROMS } from '../../components/visualizations/CircosPlot';
import PageState from '../../components/PageState';
import { useFamilyReference } from '../../lib/reference';

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
      const response = await api.get(`/families/${familyId}`);
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

  const { data: chromData } = useQuery<Chromosome[]>({
    queryKey: ['circos-chromosomes', ASSEMBLY],
    queryFn: async () => {
      const response = await api.get(`/chromosomes/${ASSEMBLY}/details`);
      const chromosomes = new Map<string, Chromosome>();
      (response.data as Chromosome[]).forEach((entry) => {
        const chrom = entry.chr.replace(/^chr/i, '');
        chromosomes.set(chrom, { ...entry, chr: chrom });
      });
      return CHROMS.map((chrom) => chromosomes.get(chrom)).filter(Boolean) as Chromosome[];
    },
  });

  const { data: variants } = useQuery<Variant[]>({
    queryKey: ['family-circos', familyId, queryParams.toString()],
    queryFn: async () => {
      const res = await api.get(
        `/families/${familyId}/structural-variants?${queryParams.toString()}`
      );
      const all = res.data.variants as Variant[];
      return all.filter((v) => v.type);
    },
    enabled: !!familyId,
  });

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
                  {`chr${c}`}
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
          <CircosPlot
            chromData={chromData}
            variants={variants}
            selected={selected}
            onChromosomeClick={handleChromClick}
            onVariantClick={handleVariantClick}
          />
          {variants && variants.length === 0 && (
            <p className="analysis-count mt-4">No variants for this family.</p>
          )}
        </section>
      </main>
    </div>
  );
};

export default CircosPlotPage;
