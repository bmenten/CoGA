import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import api from '../../lib/api';
import type { ApiAnnotationManifest, ApiAnnotationModule } from '../../lib/apiTypes';
import { apiPath } from '../../lib/apiPath';

/** Where the provenance came from — surfaced so a reviewer can judge its weight. */
const SOURCE_LABELS: Record<string, string> = {
  vcf_header: 'from VCF headers',
  manifest: 'from import manifest',
  manual: 'entered manually',
};

const COLLAPSED_COUNT = 6;

/** The version to show for a module, given the page's modality (issue #294). */
function modalityVersion(module: ApiAnnotationModule, modality?: string): string | null {
  if (modality && module.by_modality?.[modality]) return module.by_modality[modality];
  return module.version;
}

/**
 * Whether a module belongs on a modality-scoped footer. Platform/reference modules
 * (assembly, Monarch, …) and flat/legacy modules with no per-modality detail are
 * shared across pages; pipeline modules show only on the modality that cited them.
 */
function showsOnModality(module: ApiAnnotationModule, modality?: string): boolean {
  if (!modality) return true;
  if (module.layer === 'reference' || module.key === 'assembly' || !module.by_modality) return true;
  return module.by_modality[modality] != null;
}

/**
 * Annotation-provenance footer: the tool/database versions a family's data was built
 * from. With `modality` set, it shows that modality's versions (issue #294) — so the
 * SNV and SV filter pages show their own annotation versions even when they diverge.
 * The full, frozen record lives on the report ([[clinical-traceability]]).
 */
export default function AnnotationProvenanceSummary({
  familyId,
  modality,
}: {
  familyId: string;
  modality?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const { data, isError, refetch } = useQuery<ApiAnnotationManifest>({
    queryKey: ['family', familyId, 'annotation-manifest'],
    enabled: Boolean(familyId),
    queryFn: async () =>
      (await api.get(apiPath`/families/${familyId}/annotation-manifest`)).data as ApiAnnotationManifest,
  });

  const modules = (data?.modules ?? [])
    .filter((module) => showsOnModality(module, modality))
    .map((module) => ({ module, version: modalityVersion(module, modality) }))
    .filter((entry): entry is { module: ApiAnnotationModule; version: string } =>
      Boolean(entry.version),
    );
  // A failed manifest is said in the footer's place: the footer used to vanish, as for a
  // family whose annotation versions were never recorded (#610).
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
  if (!modules.length) {
    return null;
  }
  const shown = expanded ? modules : modules.slice(0, COLLAPSED_COUNT);
  const hidden = modules.length - shown.length;

  return (
    <footer
      className="surface-card annotation-provenance-footer"
      data-testid="annotation-provenance"
    >
      <span className="annotation-provenance-label">Annotation versions</span>
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
      {data?.source && (
        <span className="annotation-provenance-source">
          {SOURCE_LABELS[data.source] ?? data.source}
        </span>
      )}
    </footer>
  );
}
