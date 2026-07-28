import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';

import api from '../../lib/api';
import type { CarrierModalMode, GlobalVariantRow, VariantCarriers } from './types';

interface VariantCarrierModalProps {
  variant: GlobalVariantRow;
  mode: CarrierModalMode;
  assemblyId?: string;
  includeImputed?: boolean;
  onClose: () => void;
}

const MODE_TITLE: Record<CarrierModalMode, string> = {
  het: 'Heterozygous carriers',
  hom: 'Homozygous carriers',
  all: 'Carriers',
  families: 'Families',
};

const formatLocus = (variant: GlobalVariantRow) =>
  `${variant.chr}:${variant.pos} ${variant.ref || ''}>${variant.alt || ''}`.trim();

const VariantCarrierModal = ({
  variant,
  mode,
  assemblyId,
  includeImputed = false,
  onClose,
}: VariantCarrierModalProps) => {
  const genotypeParam = mode === 'het' || mode === 'hom' ? mode : undefined;
  const familiesOnly = mode === 'families';

  const { data, isLoading, isError } = useQuery<VariantCarriers>({
    queryKey: [
      'variant-explorer',
      'carriers',
      variant.key,
      genotypeParam ?? 'all',
      assemblyId ?? null,
      includeImputed,
    ],
    queryFn: async () => {
      const params: Record<string, string> = {};
      if (assemblyId) params.assembly_id = assemblyId;
      if (genotypeParam) params.genotype = genotypeParam;
      if (includeImputed) params.include_imputed = 'true';
      const res = await api.get(`/variant-explorer/small-variants/${variant.key}/carriers`, {
        params,
      });
      return res.data as VariantCarriers;
    },
  });

  const families = data?.families ?? [];

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-surface surface-card variant-carrier-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="variant-carrier-modal-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="variant-review-modal-header">
          <div className="variant-review-modal-summary">
            <p className="page-kicker">{MODE_TITLE[mode]}</p>
            <h2 id="variant-carrier-modal-title" className="catalog-card-title">
              {variant.gene || 'Intergenic variant'}
            </h2>
            <p className="variant-review-modal-subtitle">
              {formatLocus(variant)}
              {variant.hgvsc ? ` · ${variant.hgvsc}` : ''}
            </p>
          </div>
          <button type="button" className="button-secondary" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="variant-review-modal-body">
          {isLoading ? <p className="table-subtle">Loading carriers…</p> : null}
          {isError ? (
            <div className="variant-workspace-feedback variant-workspace-feedback--error">
              Failed to load carriers.
            </div>
          ) : null}

          {!isLoading && !isError ? (
            families.length === 0 ? (
              <p className="table-subtle">No carriers found in your accessible projects.</p>
            ) : (
              <>
                <p className="table-subtle">
                  {familiesOnly
                    ? `${families.length} famil${families.length === 1 ? 'y' : 'ies'}`
                    : `${data?.total_samples ?? 0} sample${(data?.total_samples ?? 0) === 1 ? '' : 's'} · Het: ${data?.het_samples ?? 0} · Hom: ${data?.hom_samples ?? 0} · in ${families.length} famil${families.length === 1 ? 'y' : 'ies'}`}
                </p>
                {data?.truncated ? (
                  <p className="status-note status-note--warning">
                    Showing the first {data?.total_samples ?? 0} carrier
                    {(data?.total_samples ?? 0) === 1 ? '' : 's'} — more exist than can be listed.
                    Filter by genotype or narrow your project access to see specific carriers.
                  </p>
                ) : null}
                <ul className="variant-carrier-family-list">
                  {families.map((family) => (
                    <li key={family.family_uuid} className="variant-carrier-family">
                      <div className="variant-carrier-family-header">
                        <Link
                          to={`/families/${encodeURIComponent(family.family_id)}`}
                          className="table-link"
                        >
                          {family.family_id}
                        </Link>
                        {family.project_name ? (
                          <span className="table-subtle"> · {family.project_name}</span>
                        ) : null}
                        <span className="table-subtle"> · {family.carrier_count} carrier{family.carrier_count === 1 ? '' : 's'}</span>
                      </div>
                      {!familiesOnly ? (
                        <ul className="variant-carrier-sample-list">
                          {family.samples.map((sample) => (
                            <li key={`${family.family_uuid}:${sample.sample_id}`} className="variant-carrier-sample">
                              <Link
                                to={`/families/${encodeURIComponent(family.family_id)}`}
                                className="table-link"
                              >
                                {sample.sample_id}
                              </Link>
                              <span className="table-subtle"> · {sample.zygosity}</span>
                              {sample.role ? <span className="table-subtle"> · {sample.role}</span> : null}
                              {sample.phenotype_summary ? (
                                <span className="table-subtle"> · {sample.phenotype_summary}</span>
                              ) : null}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </>
            )
          ) : null}
        </div>
      </div>
    </div>
  );
};

export default VariantCarrierModal;
