import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import api from '../../lib/api';
import type {
  ApiAnalysisAnnotationManifest,
  ApiAnnotationManifest,
  ApiAnnotationModule,
} from '../../lib/apiTypes';
import { apiPath } from '../../lib/apiPath';
import { analysisLabel } from '../../lib/familyAnalyses';

/** Where the provenance came from — surfaced so a reviewer can judge its weight. */
const SOURCE_LABELS: Record<string, string> = {
  vcf_header: 'from VCF headers',
  manifest: 'from import manifest',
  manual: 'entered manually',
};

const COLLAPSED_COUNT = 6;

/**
 * The family's annotation manifests: the tool and database versions each of its analyses' data
 * was built from, one per analysis (a family sequenced short-read and then long-read keeps both).
 * The provenance footer, the pipeline settings panel and the live report read it.
 */
export const useAnnotationManifest = (familyId: string | undefined) =>
  useQuery<ApiAnnotationManifest>({
    queryKey: ['family', familyId, 'annotation-manifest'],
    enabled: Boolean(familyId),
    queryFn: async () =>
      (await api.get(apiPath`/families/${familyId}/annotation-manifest`)).data as ApiAnnotationManifest,
  });

/**
 * The manifest of one analysis (`analysisType`, as the family's package records key it): the
 * versions a sign-out of it freezes. Without one (null), the family's only manifest, of a family
 * of one analysis or none. Null when the family holds no such analysis, or several and none was
 * named.
 */
export const analysisManifest = (
  manifest: ApiAnnotationManifest | undefined,
  analysisType: string | null | undefined,
): ApiAnalysisAnnotationManifest | null => {
  const analyses = manifest?.analyses ?? [];
  if (analysisType) {
    const own = analyses.find((entry) => entry.analysis_type === analysisType);
    if (own) return own;
  }
  return analyses.length === 1 ? analyses[0] : null;
};

/** The version to show for a module, given the page's modality. */
function modalityVersion(module: ApiAnnotationModule, modality?: string): string | null {
  if (modality && module.by_modality?.[modality]) return module.by_modality[modality];
  return module.version;
}

/**
 * Whether a module belongs on a modality-scoped footer. Platform/reference modules
 * (assembly, Monarch, …) and modules recorded without per-modality detail (as in a
 * manifest an admin entered by hand) are shared across pages; pipeline modules show
 * only on the modality that cited them.
 */
function showsOnModality(module: ApiAnnotationModule, modality?: string): boolean {
  if (!modality) return true;
  if (module.layer === 'reference' || module.key === 'assembly' || !module.by_modality) return true;
  return module.by_modality[modality] != null;
}

/** One analysis's versions on the footer, named with its analysis when the family holds several. */
function AnalysisVersions({
  manifest,
  named,
  modality,
}: {
  manifest: ApiAnalysisAnnotationManifest;
  named: boolean;
  modality?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const modules = manifest.modules
    .filter((module) => showsOnModality(module, modality))
    .map((module) => ({ module, version: modalityVersion(module, modality) }))
    .filter((entry): entry is { module: ApiAnnotationModule; version: string } =>
      Boolean(entry.version),
    );
  if (!modules.length) {
    return null;
  }
  const shown = expanded ? modules : modules.slice(0, COLLAPSED_COUNT);
  const hidden = modules.length - shown.length;
  const analysisName = named ? (analysisLabel(manifest.analysis_type) ?? manifest.analysis_type) : null;

  return (
    <footer
      className="surface-card annotation-provenance-footer"
      data-testid="annotation-provenance"
      data-analysis={manifest.analysis_type ?? undefined}
    >
      <span className="annotation-provenance-label">
        Annotation versions{analysisName ? ` — ${analysisName}` : ''}
      </span>
      {shown.map(({ module, version }) => (
        <span
          key={module.key}
          className="badge-chip annotation-provenance-chip"
          title={module.detail ? `${module.label} — ${module.detail}` : module.label}
        >
          {module.label} <strong>{version}</strong>
        </span>
      ))}
      {hidden > 0 && (
        <button
          type="button"
          className="button-link annotation-provenance-toggle"
          onClick={() => setExpanded(true)}
        >
          +{hidden} more
        </button>
      )}
      {expanded && modules.length > COLLAPSED_COUNT && (
        <button
          type="button"
          className="button-link annotation-provenance-toggle"
          onClick={() => setExpanded(false)}
        >
          show less
        </button>
      )}
      {manifest.source && (
        <span className="annotation-provenance-source">
          {SOURCE_LABELS[manifest.source] ?? manifest.source}
        </span>
      )}
    </footer>
  );
}

/**
 * Annotation-provenance footer: the tool/database versions a family's data was built
 * from. With `modality` set, it shows that modality's versions — so the
 * SNV and SV filter pages show their own annotation versions even when they diverge.
 * A family that holds several analyses has a line of each analysis's versions: the pages show
 * every analysis's data, each built with its own. The full, frozen record lives on the report
 * ([[clinical-traceability]]).
 */
export default function AnnotationProvenanceSummary({
  familyId,
  modality,
}: {
  familyId: string;
  modality?: string;
}) {
  const { data, isError, refetch } = useAnnotationManifest(familyId);

  // A failed manifest is said in the footer's place: the footer does not vanish, as for a
  // family whose annotation versions were never recorded.
  if (isError) {
    return (
      <footer className="surface-card annotation-provenance-footer" data-testid="annotation-provenance" role="alert">
        <span className="annotation-provenance-label">Annotation versions</span>{' '}
        <span>could not be loaded.</span>{' '}
        <button type="button" className="button-link" onClick={() => void refetch()}>
          Retry
        </button>
      </footer>
    );
  }
  const analyses = data?.analyses ?? [];
  return (
    <>
      {analyses.map((manifest) => (
        <AnalysisVersions
          key={manifest.analysis_type ?? ''}
          manifest={manifest}
          named={analyses.length > 1}
          modality={modality}
        />
      ))}
    </>
  );
}
