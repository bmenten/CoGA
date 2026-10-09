import { useState, useMemo, type FC } from 'react';
import { useParams, useLocation, Link, useNavigate } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import type { ChromosomeOut, FamilyOut } from '../../lib/apiSchema.generated';
import CircosPlot, { Chromosome, Variant, CHROMS } from '../../components/visualizations/CircosPlot';
import FamilyLoadFailure from '../../components/FamilyLoadFailure';
import FamilyPageBanners from '../../components/FamilyPageBanners';
import PageState from '../../components/PageState';
import VizErrorOverlay from '../../components/visualizations/VizErrorOverlay';
import { compareChromosomes, formatChromosomeLabel, normalizeChrom } from '../../lib/chromosomes';
import { getErrorMessage } from '../../lib/errorMessage';
import { useFamilyReference } from '../../lib/reference';
import { apiPath, raw } from '../../lib/apiPath';
import { searchWithResolvedProject } from './viewerShared';

// A nuclear chromosome's name without "chr": a number, a number with a letter (2A) or one
// letter (X, W). Contigs (1_KI270706v1_random, Un_GL000195v1, EBV) and the mitochondrion
// (MT) do not match; the plot leaves them out.
const NUCLEAR_CHROMOSOME = /^(\d+[A-Z]?|[A-Z])$/;

/**
 * The chromosomes of the family's assembly as the plot draws them: those of 1–22, X and Y
 * the assembly has, in that order, at its own sizes. `unplaceable` lists its other nuclear
 * chromosomes (chr23 and up, W, Z, 2A): the plot has no place for them, and the page says so
 * rather than drawing the assembly without them.
 */
const placeChromosomes = (rows: ChromosomeOut[]) => {
  const byName = new Map<string, Chromosome>();
  const unplaceable = new Set<string>();
  rows.forEach(({ chr, size, bands }) => {
    const name = normalizeChrom(chr);
    if (CHROMS.includes(name)) {
      byName.set(name, { chr: name, size, bands });
    } else if (NUCLEAR_CHROMOSOME.test(name)) {
      unplaceable.add(name);
    }
  });
  return {
    drawn: CHROMS.flatMap((name) => byName.get(name) ?? []),
    unplaceable: [...unplaceable].sort(compareChromosomes).map(formatChromosomeLabel),
  };
};

const CircosPlotPage: FC = () => {
  const { familyId } = useParams<{ familyId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const preferredProjectId = useMemo(
    () => new URLSearchParams(location.search).get('project_id') || undefined,
    [location.search],
  );

  const {
    data: family,
    isLoading: familyLoading,
    isError: familyFailed,
    error: familyError,
    refetch: refetchFamily,
  } = useQuery<Pick<FamilyOut, 'projects' | 'metadata'>>({
    queryKey: ['family', familyId],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const response = await api.get<Pick<FamilyOut, 'projects' | 'metadata'>>(apiPath`/families/${familyId}`);
      return response.data;
    },
  });

  // The family's assembly is its project's: the plot is drawn on that assembly's
  // chromosomes, never on a default one.
  const {
    assemblyName,
    assemblyValidated,
    projectId: resolvedProjectId,
    isLoading: referenceLoading,
    isError: referenceFailed,
    retry: retryReference,
  } = useFamilyReference(family?.projects, preferredProjectId);

  const queryParams = useMemo(() => {
    const p = new URLSearchParams(location.search);
    if (resolvedProjectId) {
      p.set('project_id', resolvedProjectId);
    }
    p.set('page_size', '0');
    return p;
  }, [location.search, resolvedProjectId]);

  const resolvedSearch = useMemo(
    () => searchWithResolvedProject(location.search, resolvedProjectId),
    [location.search, resolvedProjectId],
  );

  const [selected, setSelected] = useState<Record<string, boolean>>(() =>
    CHROMS.reduce(
      (acc, c) => ({ ...acc, [c]: true }),
      {} as Record<string, boolean>
    )
  );

  const {
    data: assemblyChromosomes,
    error: chromError,
    refetch: refetchChroms,
  } = useQuery<ChromosomeOut[]>({
    queryKey: ['circos-chromosomes', assemblyName],
    enabled: Boolean(assemblyName),
    queryFn: async () => {
      const response = await api.get<ChromosomeOut[]>(apiPath`/chromosomes/${assemblyName}/details`);
      return response.data;
    },
  });
  const placement = useMemo(
    () => (assemblyChromosomes ? placeChromosomes(assemblyChromosomes) : undefined),
    [assemblyChromosomes],
  );

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
    // Asked for once the family's assembly, and with it the project scope, is known.
    enabled: Boolean(familyId && assemblyName),
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

  const loadingState = (
    <PageState
      kicker="Visualization"
      title="Loading circos plot"
      message="Preparing chromosome scaffolds and structural variant links."
    />
  );

  if (familyLoading || (family?.projects?.length && referenceLoading)) {
    return loadingState;
  }

  if (familyFailed) {
    return (
      <FamilyLoadFailure
        kicker="Visualization"
        what="Family"
        error={familyError}
        notFoundMessage="This circos plot could not resolve the requested family."
        onRetry={() => void refetchFamily()}
      />
    );
  }

  // Without the family's assembly there are no chromosomes to draw. None is assumed: every
  // way of not knowing it is said as such.
  if (referenceFailed) {
    return (
      <PageState
        kicker="Visualization"
        title="Reference could not be loaded"
        message="The family's project, and with it the reference assembly, could not be loaded. The circos plot is drawn on that assembly's chromosomes."
        action={
          <button type="button" className="button-secondary" onClick={retryReference}>
            Retry
          </button>
        }
      />
    );
  }

  if (!assemblyName) {
    return (
      <PageState
        kicker="Visualization"
        title="Reference not linked"
        message="The circos plot is drawn on the chromosomes of the family's reference assembly, which comes from its project. This family has no such assembly."
      />
    );
  }

  // A failed request must not read as a plot still loading, forever (#589, #510).
  if (!assemblyChromosomes && chromError) {
    return (
      <PageState
        kicker="Visualization"
        title="Could not load the chromosomes"
        message={`${getErrorMessage(chromError, 'The request failed').replace(/\.+$/, '')}. The circos plot needs the chromosome sizes of ${assemblyName}.`}
        action={
          <button type="button" className="button-secondary" onClick={() => void refetchChroms()}>
            Retry
          </button>
        }
      />
    );
  }

  if (!assemblyChromosomes || !placement) {
    return loadingState;
  }

  if (assemblyChromosomes.length === 0) {
    return (
      <PageState
        kicker="Visualization"
        title={`No chromosome sizes for ${assemblyName}`}
        message={`The circos plot draws each chromosome to its size, and CoGA holds none for ${assemblyName}. An administrator loads them with the assembly's reference data.`}
      />
    );
  }

  // Drawn without some of its chromosomes, the plot would pass for the whole genome.
  if (placement.unplaceable.length > 0 || placement.drawn.length === 0) {
    return (
      <PageState
        kicker="Visualization"
        title={`The circos plot cannot draw ${assemblyName}`}
        message={
          placement.unplaceable.length > 0
            ? `It draws chromosomes 1–22, X and Y. ${assemblyName} also has ${placement.unplaceable.join(', ')}, which it cannot place: it would leave them, and their structural variants, out.`
            : `It draws chromosomes 1–22, X and Y, and ${assemblyName} has none of them.`
        }
      />
    );
  }

  const chromData = placement.drawn;

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
            {chromData.map(({ chr: c }) => (
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
                Explore structural rearrangements across the chromosomes of {assemblyName}.
              </p>
            </div>
            <Link
              to={`/families/${familyId}/structural-variants${resolvedSearch ? `?${resolvedSearch}` : ''}`}
              className="button-secondary hover:no-underline"
            >
              Back to variants
            </Link>
          </div>
          <FamilyPageBanners
            metadata={family?.metadata}
            assemblyScope={{ name: assemblyName, validated: assemblyValidated }}
          />
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
